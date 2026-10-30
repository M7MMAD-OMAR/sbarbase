import defaults from './mail-defaults.json';
import {copyFileSync,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {Catalog} from './catalog';
import {type ManagementIdentity} from './auth';
import {SAVE_INTERVAL_MS} from './sign-in';
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
/** Confirmation and mail limits, as the cloud's Confirm email switch and Rate Limits page show them; null when the file holds a form the console does not edit. */
export type MailLimits={confirm_email:boolean;rate_limit_email_sent:number|null;rate_limit_otp:number;rate_limit_verify:number;min_interval_seconds:number|null};
export type SmtpView=SmtpFields&MailLimits&{enabled:boolean;password_set:boolean};

const file=(directory:string,runtime:string)=>runtimeFile(directory,runtime,'-mail.json');

/** The saved mail file, or null when there is none. A link, a file open to other users or an unreadable one is refused. */
export function readMail(directory:string,runtime:string):Record<string,unknown>|null {
 return readPrivateObject(file(directory,runtime),MAX_BYTES,()=>new SmtpError('file'));
}

const LIMIT_DEFAULTS={rate_limit_email_sent:defaults.rate_limit_email_sent,rate_limit_otp:defaults.rate_limit_otp,rate_limit_verify:defaults.rate_limit_verify,min_interval_seconds:60};
const wholeSeconds=(value:unknown)=>{const match=typeof value==='string'?value.match(/^(\d+)s$/):null;return match?Number(match[1]):null;};

export function smtpView(mail:Record<string,unknown>|null):SmtpView {
 if(!mail)return {enabled:false,admin_email:'',sender_name:'',host:'',port:587,user:'',password_set:false,confirm_email:true,...LIMIT_DEFAULTS};
 // A file written from the host stores the limit as a string ("30"); a form like "30/1h" is shown as unset, never as a wrong number.
 const sent=mail.rate_limit_email_sent;
 return {confirm_email:mail.autoconfirm!==true,rate_limit_email_sent:typeof sent==='number'?sent:typeof sent==='string'&&/^\d+$/.test(sent)?Number(sent):null,
  rate_limit_otp:Number(mail.rate_limit_otp??LIMIT_DEFAULTS.rate_limit_otp),rate_limit_verify:Number(mail.rate_limit_verify??LIMIT_DEFAULTS.rate_limit_verify),
  min_interval_seconds:wholeSeconds(mail.max_frequency),enabled:true,admin_email:String(mail.admin_email??''),sender_name:String(mail.sender_name??''),host:String(mail.host??''),
  port:Number(mail.port??587),user:String(mail.user??''),password_set:typeof mail.pass==='string'&&mail.pass!==''};
}

/** Field limits: the file must stay under lab/mail_config.py MAX_BYTES, or the environment could not start. */
const LIMITS:Record<string,number>={admin_email:254,sender_name:200,host:253,user:512,password:1024};
const plain=(value:unknown,field:string,allowEmpty:boolean)=>{
 if(!cleanText(value,LIMITS[field]??512,{allowEmpty}))throw new SmtpError(field);
 return value;
};

/** The mail file for these console fields. An empty password keeps the saved one; the rest of the file is kept or defaulted. */
export function mailFromInput(input:unknown,current:Record<string,unknown>|null):Record<string,unknown> {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new SmtpError('settings');
 const value=input as Record<string,unknown>;
 for(const key of Object.keys(value))if(!['enabled','admin_email','sender_name','host','port','user','password','base_revision','confirm_email','rate_limit_email_sent','rate_limit_otp','rate_limit_verify','min_interval_seconds'].includes(key))throw new SmtpError('settings');
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
 const mail:Record<string,unknown>={...base,...(current??{}),host,port,user,pass,admin_email:admin,sender_name:name};
 // The cloud's Confirm email switch is the opposite of autoconfirm; the limits are whole numbers; an omitted one keeps the file's value.
 if(value.confirm_email!==undefined){if(typeof value.confirm_email!=='boolean')throw new SmtpError('confirm_email');mail.autoconfirm=!value.confirm_email;}
 const count=(field:string,min:number,max:number)=>{const given=value[field];if(given===undefined)return;
  if(typeof given!=='number'||!Number.isInteger(given)||given<min||given>max)throw new SmtpError(field);return given;};
 const sent=count('rate_limit_email_sent',1,1000000);if(sent!==undefined)mail.rate_limit_email_sent=sent;
 const otp=count('rate_limit_otp',1,1000000);if(otp!==undefined)mail.rate_limit_otp=otp;
 const verify=count('rate_limit_verify',1,1000000);if(verify!==undefined)mail.rate_limit_verify=verify;
 const gap=count('min_interval_seconds',1,86400);if(gap!==undefined)mail.max_frequency=`${gap}s`;
 // The loader refuses a larger file, which would stop this environment's Auth from starting.
 if(Buffer.byteLength(JSON.stringify(mail))>MAX_BYTES)throw new SmtpError('settings');
 return mail;
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
  ['autoconfirm','MAILER_AUTOCONFIRM'],['rate_limit_email_sent','RATE_LIMIT_EMAIL_SENT'],['rate_limit_otp','RATE_LIMIT_OTP'],['rate_limit_verify','RATE_LIMIT_VERIFY'],['max_frequency','SMTP_MAX_FREQUENCY']]
  // The limits are compared as text: the host writes "30", the console writes 30, and that is not a change.
 .filter(([key])=>String(before[key!])!==String(after[key!])).map(([,name])=>name!);
}

/** GET and PUT `/environments/{id}/smtp`, for owners and admins of a ready environment. */
export function smtpHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return settingsHandler<Record<string,unknown>>(catalog,identify,signInDirectory,saveInterval,{
  segment:'smtp',error:SmtpError,noun:'setting',
  fileMessage:'The saved mail file cannot be read on the server (a link or wrong permissions). Fix it on the host.',
  read:runtime=>readMail(directory,runtime),
  check:body=>{if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new SmtpError('enabled');},
  next:(input,body,before)=>body.enabled===false?null:mailFromInput(input,before),
  write:(runtime,after)=>{keepPrior(directory,runtime);writePrivateFile(directory,file(directory,runtime),after&&JSON.stringify(after),()=>new SmtpError('file'));},
  changed:changedSmtpKeys,
  view:(_runtime,saved)=>({smtp:smtpView(saved)})});
}
