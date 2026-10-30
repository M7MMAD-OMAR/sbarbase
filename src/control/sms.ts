import {existsSync,lstatSync,mkdirSync,readFileSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {readSettings,writeSettingsFile,SAVE_INTERVAL_MS,SettingsError} from './sign-in';

/** Phone sign-in for one environment, as the Supabase cloud's Phone provider page: the switch, the SMS provider and its credentials, phone
 * confirmations, and two limits. The console writes the environment's private SMS file (`<runtime>-sms.json`, mode 0600, read by
 * lab/sms_config.py) and records a pending revision in the sign-in row, so the supervisor's apply recreates that environment's Auth with it
 * and rolls back if it does not start. A credential is written to that file and never read back: answers say only whether one is set. Only
 * settings tried against the pinned Auth are offered (lab/auth-names-check.py). */

const DIRECTORY='.secrets/upstream';
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

function file(directory:string,runtime:string) {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return join(directory,runtime+'-sms.json');
}

export function readSms(directory:string,runtime:string):Saved|null {
 const path=file(directory,runtime);
 let status;
 try{status=lstatSync(path);}catch(error){if((error as {code?:string}).code==='ENOENT')return null;throw error;}
 if(!status.isFile()||(status.mode&0o077)!==0||status.size>MAX_BYTES)throw new SmsError('file');
 let parsed:unknown;
 try{parsed=JSON.parse(readFileSync(path,'utf8'));}catch{throw new SmsError('file');}
 if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new SmsError('file');
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
 // A lone surrogate is refused: Python could not encode it into the container's environment file.
 if(typeof value!=='string'||value===''||value.length>MAX_VALUE||/[\x00-\x1f\x7f]/.test(value)||/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/.test(value))throw new SmsError(name);
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

function writeSms(directory:string,runtime:string,saved:Saved|null) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=file(directory,runtime),pending=path+'.pending';
 if(existsSync(path)&&lstatSync(path).isSymbolicLink())throw new SmsError('file');
 if(!saved){rmSync(path,{force:true});return;}
 rmSync(pending,{force:true});
 writeFileSync(pending,JSON.stringify(saved),{mode:0o600,flag:'wx'});
 renameSync(pending,path);
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
export function smsHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/sms$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   let state=catalog.signIn(actor,environment);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment,state.runtime,true);
   if(request.method==='GET')return answer(reply(200,{data:{...state,sms:smsView(readSms(directory,state.runtime))}}));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   state=catalog.signIn(actor,environment);
   const body=input&&typeof input==='object'&&!Array.isArray(input)?input as Record<string,unknown>:{};
   const base=body.base_revision;
   if(base!==undefined&&base!==null&&base!==state.revision)return reply(409,{message:'These settings changed since you opened them. Reload and try again.'});
   const waited=state.updatedAt===null?Infinity:Date.now()-state.updatedAt;
   if(waited>=0&&waited<saveInterval){const busy=reply(429,{message:'Wait a few seconds before saving again.'});busy.headers.set('retry-after',String(Math.ceil((saveInterval-waited)/1000)));return busy;}
   if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new SmsError('enabled');
   // Everything that can refuse is read and checked before anything is written.
   const before=readSms(directory,state.runtime);
   const settings=readSettings(signInDirectory,state.runtime);
   const after=body.enabled===false?null:smsFromInput(input,before);
   const revision=Math.max(state.revision,settings?.revision??0)+1;
   writeSms(directory,state.runtime,after);
   if(settings)writeSettingsFile(signInDirectory,state.runtime,{...settings,revision});
   const saved=catalog.requestSignIn(actor,environment,revision,changedSmsKeys(before,after));
   return answer(reply(202,{data:{...saved,sms:smsView(after)}}));
  } catch(error) {
   if(error instanceof SettingsError&&error.message==='file')return reply(409,{message:'The saved sign-in settings file cannot be read on the server (a link, wrong permissions, or a newer version). Fix it on the host.'});
   if(error instanceof SmsError&&error.message==='file')return reply(409,{message:'The saved phone settings file cannot be read on the server (a link, wrong permissions or a bad format). Fix it on the host.'});
   if(error instanceof SmsError)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} setting`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
