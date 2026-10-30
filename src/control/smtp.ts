import defaults from './mail-defaults.json';
import {copyFileSync,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {Catalog} from './catalog';
import {type ManagementIdentity} from './auth';
import {baseSettings,SAVE_INTERVAL_MS,type SignInSettings} from './sign-in';
import {cleanText,PRIVATE_DIRECTORY,readPrivateObject,runtimeFile,settingsHandler,writePrivateFile} from './settings-route';

/** Custom SMTP for one environment, as the Supabase cloud's Emails > SMTP Settings page: sender email and name, host,
 * port, username and password. The console writes the environment's private mail file (`<runtime>-mail.json`, mode 0600,
 * the one lab/mail_config.py validates) and records a pending revision in the sign-in row, so the supervisor's apply
 * (`reconcile_auth`) recreates that environment's Auth with it and rolls back if it does not start. The password is
 * written to that file and never read back: answers carry only whether one is set. The keys the console does not ask
 * for keep what the file had, or `mail-defaults.json`. */

const HOST=/^[A-Za-z0-9][A-Za-z0-9._-]*$/,ADDRESS=/^[^@\s<>,;:"]+@[^@\s<>,;:"]+\.[^@\s<>,;:"]+$/;
const MAX_BYTES=8192;
/** Hosts an environment's Auth must not be pointed at: this machine, link-local and unspecified addresses (the cloud metadata address
 * among them), numeric forms of those, and this installation's own containers. A relay on a private network is allowed; the host
 * firewall decides what the outbound network reaches. */
const internalHost=(host:string)=>{
 const name=host.toLowerCase();
 if(/^\d+$/.test(name)||/^0x[0-9a-f]+$/.test(name)||name==='localhost'||name.endsWith('.localhost')||name.startsWith('sbarbase-'))return true;
 const parts=name.split('.').map(Number);
 if(parts.length!==4||!/^[0-9.]+$/.test(name))return false;
 return parts[0]===127||parts[0]===0||(parts[0]===169&&parts[1]===254);
};

export class SmtpError extends Error {}

export type SmtpFields={admin_email:string;sender_name:string;host:string;port:number;user:string};
/** Confirmation and mail limits, as the cloud's Confirm email switch and Rate Limits page show them; null when a mail file holds a form the console does not edit. */
export type MailLimits={confirm_email:boolean;rate_limit_email_sent:number|null;rate_limit_otp:number|null;rate_limit_verify:number|null;min_interval_seconds:number|null};
/** `confirm_email_chosen`: Confirm email was set on purpose, in the sign-in settings or in a mail file, rather than being what this environment has when nothing is saved. */
export type SmtpView=SmtpFields&MailLimits&{enabled:boolean;password_set:boolean;confirm_email_chosen:boolean};

function file(directory:string,runtime:string) {
 return runtimeFile(directory,runtime,'-mail.json');
}

/** The saved mail file, or null when there is none. A link, a file open to other users or an unreadable one is refused. */
export function readMail(directory:string,runtime:string):Record<string,unknown>|null {
 return readPrivateObject(file(directory,runtime),MAX_BYTES,()=>new SmtpError('file'));
}

/** Confirm email and the mail limits are sign-in settings (the `config` store of src/control/sign-in.ts, REGISTRY page `emails`), so they save with custom SMTP
 * off. A mail file written before that carries them too (`file`): it stays the value of a setting the sign-in settings do not name, so nothing changes on
 * upgrade, and the first save through this route moves them across and takes them out of the file. */
const SHARED=[
 {field:'confirm_email',key:'MAILER_AUTOCONFIRM',file:'autoconfirm',min:0,max:0},
 {field:'rate_limit_email_sent',key:'RATE_LIMIT_EMAIL_SENT',file:'rate_limit_email_sent',min:1,max:1000000},
 {field:'rate_limit_otp',key:'RATE_LIMIT_OTP',file:'rate_limit_otp',min:1,max:1000000},
 {field:'rate_limit_verify',key:'RATE_LIMIT_VERIFY',file:'rate_limit_verify',min:1,max:1000000},
 {field:'min_interval_seconds',key:'SMTP_MAX_FREQUENCY',file:'max_frequency',min:1,max:86400}] as const;
type Shared=typeof SHARED[number];
type ConfigValue=boolean|number|string;

const DEFAULTS={rate_limit_email_sent:defaults.rate_limit_email_sent,rate_limit_otp:defaults.rate_limit_otp,rate_limit_verify:defaults.rate_limit_verify,min_interval_seconds:60};
const wholeSeconds=(value:unknown)=>{const match=typeof value==='string'?value.match(/^(\d+)s$/):null;return match?Number(match[1]):null;};
/** What a mail file holds for a shared setting, in the form the sign-in settings keep it; undefined when it holds a form the console does not edit (such as 30/1h or 1h30m). */
function fileValue(mail:Record<string,unknown>|null,entry:Shared):ConfigValue|undefined {
 const value=mail?.[entry.file];
 if(entry.field==='confirm_email')return typeof value==='boolean'?value:undefined;
 if(entry.field==='min_interval_seconds'){const seconds=wholeSeconds(value);return seconds!==null&&seconds>=entry.min&&seconds<=entry.max?seconds:undefined;}
 const number=typeof value==='number'?value:typeof value==='string'&&/^\d+$/.test(value)?Number(value):undefined;
 return number!==undefined&&Number.isInteger(number)&&number>=entry.min&&number<=entry.max?number:undefined;
}

/** The view of the mail file and of the shared settings: a setting the sign-in settings name, else the mail file's, else what this environment runs with. */
export function smtpView(mail:Record<string,unknown>|null,config:Record<string,ConfigValue>={}):SmtpView {
 const stored=(entry:Shared)=>config[entry.key];
 const limit=(entry:Shared,fallback:number|null)=>{const set=stored(entry);if(typeof set==='number')return set;if(mail&&mail[entry.file]!==undefined){const kept=fileValue(mail,entry);return typeof kept==='number'?kept:null;}return fallback;};
 const [confirm,sent,otp,verify,gap]=SHARED;
 const autoconfirm=stored(confirm),fromFile=fileValue(mail,confirm);
 // With no mail file and nothing saved, this environment sends no email and signs people up confirmed (lab/run.py): Confirm email is off.
 const confirm_email=typeof autoconfirm==='boolean'?!autoconfirm:mail?fromFile!==true:false;
 const limits={confirm_email,rate_limit_email_sent:limit(sent,DEFAULTS.rate_limit_email_sent),rate_limit_otp:limit(otp,DEFAULTS.rate_limit_otp),
  rate_limit_verify:limit(verify,DEFAULTS.rate_limit_verify),min_interval_seconds:limit(gap,DEFAULTS.min_interval_seconds),confirm_email_chosen:typeof autoconfirm==='boolean'||mail!==null};
 if(!mail)return {enabled:false,admin_email:'',sender_name:'',host:'',port:587,user:'',password_set:false,...limits};
 return {...limits,enabled:true,admin_email:String(mail.admin_email??''),sender_name:String(mail.sender_name??''),host:String(mail.host??''),
  port:Number(mail.port??587),user:String(mail.user??''),password_set:typeof mail.pass==='string'&&mail.pass!==''};
}

/** Field limits: the file must stay under lab/mail_config.py MAX_BYTES, or the environment could not start. */
const LIMITS:Record<string,number>={admin_email:254,sender_name:200,host:253,user:512,password:1024};
const plain=(value:unknown,field:string,allowEmpty:boolean)=>{
 if(!cleanText(value,LIMITS[field]??512,{allowEmpty}))throw new SmtpError(field);
 return value;
};

const MAIL_INPUT=['enabled','admin_email','sender_name','host','port','user','password','base_revision',...SHARED.map(entry=>entry.field)];

/** The shared settings in a save: a member to set (a boolean for Confirm email, a whole number for a limit), null to put it back to the default, or nothing to keep it. */
export function sharedFromInput(value:Record<string,unknown>):Record<string,ConfigValue|null> {
 const given:Record<string,ConfigValue|null>={};
 for(const entry of SHARED) {
  const member=value[entry.field];
  if(member===undefined)continue;
  if(member===null){given[entry.key]=null;continue;}
  if(entry.field==='confirm_email'){if(typeof member!=='boolean')throw new SmtpError(entry.field);given[entry.key]=!member;continue;}
  if(typeof member!=='number'||!Number.isInteger(member)||member<entry.min||member>entry.max)throw new SmtpError(entry.field);
  given[entry.key]=member;
 }
 return given;
}

/** The mail file for these console fields. An empty password keeps the saved one; the rest of the file is kept or defaulted. The shared settings are not
 * written here (`sharedSettings` moves them to the sign-in settings); a saved file that still holds them keeps them until then. */
export function mailFromInput(input:unknown,current:Record<string,unknown>|null):Record<string,unknown> {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new SmtpError('settings');
 const value=input as Record<string,unknown>;
 for(const key of Object.keys(value))if(!MAIL_INPUT.includes(key))throw new SmtpError('settings');
 sharedFromInput(value);
 const admin=plain(value.admin_email,'admin_email',false),host=plain(value.host,'host',false);
 if(!ADDRESS.test(admin))throw new SmtpError('admin_email');
 if(!HOST.test(host)||internalHost(host))throw new SmtpError('host');
 const name=plain(value.sender_name??'','sender_name',true);
 if(/[<>"]/.test(name))throw new SmtpError('sender_name');
 const port=value.port;
 if(typeof port!=='number'||!Number.isInteger(port)||port<1||port>65535)throw new SmtpError('port');
 const user=plain(value.user??'','user',true);
 const given=value.password??'';
 if(!cleanText(given,LIMITS.password!,{allowEmpty:true}))throw new SmtpError('password');
 const pass=given||(typeof current?.pass==='string'?current.pass:'');
 if(!pass)throw new SmtpError('password');
 const {about:_about,...base}=defaults as Record<string,unknown>;
 for(const entry of SHARED)delete base[entry.file];
 const mail:Record<string,unknown>={...base,...(current??{}),host,port,user,pass,admin_email:admin,sender_name:name};
 // The loader refuses a larger file, which would stop this environment's Auth from starting.
 if(Buffer.byteLength(JSON.stringify(mail))>MAX_BYTES)throw new SmtpError('settings');
 return mail;
}

/** The sign-in settings and the mail file after a save, as the first holds the shared settings: what the save names is set (null clears), and a value only the
 * mail file holds is moved across, so taking it out of the file (or removing the file) loses nothing. A form the console does not edit stays in the file.
 * With no sign-in settings saved and nothing named, nothing moves and no settings are made. */
export function sharedSettings(settings:SignInSettings|null,given:Record<string,ConfigValue|null>,before:Record<string,unknown>|null,after:Record<string,unknown>|null):{after:Record<string,unknown>|null;settings:SignInSettings|null;keys:string[]} {
 const config:Record<string,ConfigValue>={...(settings?.config??{})};
 // Custom SMTP turned on from nothing, or turned off: Confirm email must not change by the file coming or going. A file without the key means confirmation
 // required (lab/mail_config.py OPTIONAL), and with no file this environment confirms nothing, so the choice is written down. The limits need no such care:
 // the file's defaults are Auth's own.
 const enabling=before===null&&after!==null,disabling=before!==null&&after===null;
 const moving=settings!==null||Object.keys(given).length>0||enabling||disabling;
 for(const entry of SHARED) {
  const member=given[entry.key];
  if(member===null)delete config[entry.key];
  else if(member!==undefined)config[entry.key]=member;
  else if(moving&&config[entry.key]===undefined){
   const kept=fileValue(before,entry)??(entry.field==='confirm_email'&&(disabling||enabling)?false:undefined);
   if(kept!==undefined)config[entry.key]=kept;
  }
 }
 // The file keeps a form the console cannot hold, but not a setting that is now named in the sign-in settings or was just put back to the default.
 let file=after;
 if(file){file={...file};for(const entry of SHARED)if(config[entry.key]!==undefined||given[entry.key]!==undefined)delete file[entry.file];}
 const keys=SHARED.map(entry=>entry.key).filter(key=>settings?.config?.[key]!==config[key]);
 if(keys.length===0)return {after:file,settings,keys};
 return {after:file,settings:{...(settings??baseSettings()),config},keys};
}

/** The mail file as it was before the first unapplied save, so a failed apply can put it back (lab/auth_settings.py `apply`). A second
 * save while one is pending keeps the first copy. `none` marks that there was no file. */
function keepPrior(directory:string,runtime:string) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=file(directory,runtime),prior=path.replace(/-mail\.json$/,'-mail.prior');
 if(existsSync(prior))return;
 if(existsSync(path))copyFileSync(path,prior);else writeFileSync(prior,'none',{mode:0o600});
}

/** The platform key names of what a save changed, never a value. */
export function changedSmtpKeys(before:Record<string,unknown>|null,after:Record<string,unknown>|null):string[] {
 if(!before&&!after)return [];
 if(!before||!after)return ['SMTP_HOST','SMTP_PORT','SMTP_USER','SMTP_PASS','SMTP_ADMIN_EMAIL','SMTP_SENDER_NAME'];
 return [['host','SMTP_HOST'],['port','SMTP_PORT'],['user','SMTP_USER'],['pass','SMTP_PASS'],['admin_email','SMTP_ADMIN_EMAIL'],['sender_name','SMTP_SENDER_NAME'],
  ]
 // The shared settings are named by `sharedSettings` from the sign-in settings, where they are kept.
 .filter(([key])=>String(before[key!])!==String(after[key!])).map(([,name])=>name!);
}

/** GET and PUT `/environments/{id}/smtp`, for owners and admins of a ready environment. */
export function smtpHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return settingsHandler<Record<string,unknown>>(catalog,identify,signInDirectory,saveInterval,{
  segment:'smtp',error:SmtpError,noun:'setting',
  fileMessage:'The saved mail file cannot be read on the server (a link or wrong permissions). Fix it on the host.',
  read:runtime=>readMail(directory,runtime),
  check:body=>{if(Object.keys(body).some(key=>!MAIL_INPUT.includes(key)))throw new SmtpError('settings');if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new SmtpError('enabled');sharedFromInput(body);},
  next:(input,body,before)=>body.enabled===false?null:mailFromInput(input,before),
  refine:({body,before,after,settings})=>sharedSettings(settings,sharedFromInput(body),before,after),
  write:(runtime,after)=>{keepPrior(directory,runtime);writePrivateFile(directory,file(directory,runtime),after&&JSON.stringify(after),()=>new SmtpError('file'));},
  changed:changedSmtpKeys,
  view:(_runtime,saved,settings)=>({smtp:smtpView(saved,settings?.config)})});
}
