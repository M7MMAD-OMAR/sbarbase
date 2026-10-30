import {Catalog} from './catalog';
import {type ManagementIdentity} from './auth';
import {SAVE_INTERVAL_MS} from './sign-in';
import {cleanText,PRIVATE_DIRECTORY,readPrivateObject,runtimeFile,settingsHandler,writePrivateFile} from './settings-route';

/** Phone sign-in for one environment, as the Supabase cloud's Phone provider page: the switch, the SMS provider and its credentials, phone
 * confirmations, and two limits. The console writes the environment's private SMS file (`<runtime>-sms.json`, mode 0600, read by
 * lab/sms_config.py) and records a pending revision in the sign-in row, so the supervisor's apply recreates that environment's Auth with it
 * and rolls back if it does not start. A credential is written to that file and never read back: answers say only whether one is set. Only
 * settings tried against the pinned Auth are offered (lab/auth-names-check.py). */

const MAX_BYTES=8192,MAX_VALUE=256;
/** As lab/sms_config.py PROVIDERS: each provider's fields, in order, and which are secret. */
export const PROVIDERS:Record<string,{label:string;fields:{name:string;label:string;secret:boolean}[]}>={
 twilio:{label:'Twilio',fields:[{name:'account_sid',label:'Twilio account SID',secret:false},{name:'auth_token',label:'Twilio auth token',secret:true},{name:'message_service_sid',label:'Twilio message service SID',secret:false}]},
 twilio_verify:{label:'Twilio Verify',fields:[{name:'account_sid',label:'Twilio account SID',secret:false},{name:'auth_token',label:'Twilio auth token',secret:true},{name:'message_service_sid',label:'Twilio Verify service SID',secret:false}]},
 messagebird:{label:'MessageBird',fields:[{name:'access_key',label:'MessageBird access key',secret:true},{name:'originator',label:'MessageBird originator',secret:false}]},
 textlocal:{label:'Textlocal',fields:[{name:'api_key',label:'Textlocal API key',secret:true},{name:'sender',label:'Textlocal sender',secret:false}]},
 vonage:{label:'Vonage',fields:[{name:'api_key',label:'Vonage API key',secret:false},{name:'api_secret',label:'Vonage API secret',secret:true},{name:'from',label:'Vonage from',secret:false}]}};

export class SmsError extends Error {}

type Saved=Record<string,unknown>&{version:number;enabled:boolean;provider:string;autoconfirm:boolean;rate_limit_sms_sent:number};
export type SmsView={enabled:boolean;provider:string;phone_confirmations:boolean;rate_limit_sms_sent:number;
 fields:Record<string,string>;secrets_set:Record<string,boolean>;providers:{id:string;label:string;fields:{name:string;label:string;secret:boolean}[]}[]};

const file=(directory:string,runtime:string)=>runtimeFile(directory,runtime,'-sms.json');

export function readSms(directory:string,runtime:string):Saved|null {
 const parsed=readPrivateObject(file(directory,runtime),MAX_BYTES,()=>new SmsError('file'));
 if(parsed===null)return null;
 const saved=parsed as Saved;
 if(saved.version!==1||!Object.hasOwn(PROVIDERS,saved.provider)||typeof saved.enabled!=='boolean')throw new SmsError('file');
 return saved;
}

const all=Object.entries(PROVIDERS).map(([id,item])=>({id,label:item.label,fields:item.fields}));

/** The page's view: every non secret value, and only whether each secret is set. */
export function smsView(saved:Saved|null):SmsView {
 const provider=saved?.provider??'twilio';
 const block=(saved?.[provider]&&typeof saved[provider]==='object'?saved[provider]:{}) as Record<string,unknown>;
 const fields:Record<string,string>={},secrets_set:Record<string,boolean>={};
 for(const field of PROVIDERS[provider]!.fields) {
  const value=typeof block[field.name]==='string'?block[field.name] as string:'';
  if(field.secret)secrets_set[field.name]=value!=='';else fields[field.name]=value;
 }
 return {enabled:!!saved?.enabled,provider,phone_confirmations:saved?saved.autoconfirm!==true:true,
  rate_limit_sms_sent:typeof saved?.rate_limit_sms_sent==='number'?saved.rate_limit_sms_sent:30,fields,secrets_set,providers:all};
}

const plain=(value:unknown,name:string)=>{
 if(!cleanText(value,MAX_VALUE,{control:true}))throw new SmsError(name);
 return value;
};

/** The SMS file for the console's input. A blank secret keeps the saved one when the provider is unchanged; a changed provider needs its own. */
export function smsFromInput(input:unknown,current:Saved|null):Saved {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new SmsError('settings');
 const value=input as Record<string,unknown>;
 for(const key of Object.keys(value))if(!['enabled','provider','phone_confirmations','rate_limit_sms_sent','credentials','base_revision'].includes(key))throw new SmsError('settings');
 const provider=value.provider;
 if(typeof provider!=='string'||!Object.hasOwn(PROVIDERS,provider))throw new SmsError('provider');
 const given=value.credentials;
 if(given!==undefined&&(!given||typeof given!=='object'||Array.isArray(given)))throw new SmsError('credentials');
 const entered=(given??{}) as Record<string,unknown>;
 const known=new Set(PROVIDERS[provider]!.fields.map(field=>field.name));
 for(const key of Object.keys(entered))if(!known.has(key))throw new SmsError('credentials');
 // The saved credentials count only for the same provider: another provider's are never carried over.
 const saved=(current&&current.provider===provider&&current[provider]&&typeof current[provider]==='object'?current[provider]:{}) as Record<string,unknown>;
 const block:Record<string,string>={};
 for(const field of PROVIDERS[provider]!.fields) {
  const typed=entered[field.name];
  block[field.name]=typeof typed==='string'&&typed!==''?plain(typed,`${PROVIDERS[provider]!.label} ${field.label}`):typeof saved[field.name]==='string'&&saved[field.name]!==''?saved[field.name] as string:plain('',field.label);
 }
 const confirmations=value.phone_confirmations===undefined?true:value.phone_confirmations;
 if(typeof confirmations!=='boolean')throw new SmsError('phone confirmations');
 const limit=value.rate_limit_sms_sent===undefined?30:value.rate_limit_sms_sent;
 if(typeof limit!=='number'||!Number.isInteger(limit)||limit<1||limit>1000000)throw new SmsError('rate limit');
 const out:Saved={version:1,enabled:true,provider,autoconfirm:!confirmations,rate_limit_sms_sent:limit,[provider]:block};
 if(Buffer.byteLength(JSON.stringify(out))>MAX_BYTES)throw new SmsError('settings');
 return out;
}

/** The platform key names of what a save changed, never a value. */
export function changedSmsKeys(before:Saved|null,after:Saved|null):string[] {
 const on=(saved:Saved|null)=>!!saved?.enabled;
 if(!on(before)&&!on(after))return [];
 if(on(before)!==on(after))return ['EXTERNAL_PHONE_ENABLED','SMS_PROVIDER'];
 const keys:string[]=[];
 if(before!.provider!==after!.provider)keys.push('SMS_PROVIDER');
 if(before!.autoconfirm!==after!.autoconfirm)keys.push('SMS_AUTOCONFIRM');
 if(before!.rate_limit_sms_sent!==after!.rate_limit_sms_sent)keys.push('RATE_LIMIT_SMS_SENT');
 for(const field of PROVIDERS[after!.provider]!.fields)
  if(JSON.stringify((before![before!.provider] as Record<string,unknown>)?.[field.name])!==JSON.stringify((after![after!.provider] as Record<string,unknown>)[field.name]))keys.push('SMS_'+(after!.provider.toUpperCase())+'_'+field.name.toUpperCase());
 return keys;
}

/** GET and PUT `/environments/{id}/sms`, for owners and admins of a ready environment. */
export function smsHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return settingsHandler<Saved>(catalog,identify,signInDirectory,saveInterval,{
  segment:'sms',error:SmsError,noun:'setting',
  fileMessage:'The saved phone settings file cannot be read on the server (a link, wrong permissions or a bad format). Fix it on the host.',
  read:runtime=>readSms(directory,runtime),
  check:body=>{if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new SmsError('enabled');},
  next:(input,body,before)=>body.enabled===false?null:smsFromInput(input,before),
  write:(runtime,after)=>writePrivateFile(directory,file(directory,runtime),after&&JSON.stringify(after),()=>new SmsError('file')),
  changed:changedSmsKeys,
  view:(_runtime,saved)=>({sms:smsView(saved)})});
}
