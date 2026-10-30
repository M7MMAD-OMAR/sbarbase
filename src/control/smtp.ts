import defaults from './mail-defaults.json';
import {copyFileSync,existsSync,lstatSync,mkdirSync,readFileSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {readSettings,writeSettingsFile,SAVE_INTERVAL_MS,SettingsError} from './sign-in';

/** Custom SMTP for one environment, as the Supabase cloud's Emails > SMTP Settings page: sender email and name, host,
 * port, username and password. The console writes the environment's private mail file (`<runtime>-mail.json`, mode 0600,
 * the one lab/mail_config.py validates) and records a pending revision in the sign-in row, so the supervisor's apply
 * (`reconcile_auth`) recreates that environment's Auth with it and rolls back if it does not start. The password is
 * written to that file and never read back: answers carry only whether one is set. The keys the console does not ask
 * for keep what the file had, or `mail-defaults.json`. */

const MAIL_DIRECTORY='.secrets/upstream';
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

function file(directory:string,runtime:string) {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return join(directory,runtime+'-mail.json');
}

/** The saved mail file, or null when there is none. A link, a file open to other users or an unreadable one is refused. */
export function readMail(directory:string,runtime:string):Record<string,unknown>|null {
 const path=file(directory,runtime);
 let status;
 try{status=lstatSync(path);}catch(error){if((error as {code?:string}).code==='ENOENT')return null;throw error;}
 if(!status.isFile()||(status.mode&0o077)!==0||status.size>MAX_BYTES)throw new SmtpError('file');
 const parsed=JSON.parse(readFileSync(path,'utf8')) as unknown;
 if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new SmtpError('file');
 return parsed as Record<string,unknown>;
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
 if(typeof value!=='string'||/[\r\n\x00]/.test(value)||(!allowEmpty&&value==='')||value.length>(LIMITS[field]??512))throw new SmtpError(field);
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
 if(typeof given!=='string'||/[\r\n\x00]/.test(given)||given.length>LIMITS.password!)throw new SmtpError('password');
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
 const path=file(directory,runtime),prior=path.replace(/-mail\.json$/,'-mail.prior');
 if(existsSync(prior))return;
 if(existsSync(path))copyFileSync(path,prior);else writeFileSync(prior,'none',{mode:0o600});
}

function writeMail(directory:string,runtime:string,mail:Record<string,unknown>) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=file(directory,runtime),pending=path+'.pending';
 if(existsSync(path)&&lstatSync(path).isSymbolicLink())throw new SmtpError('file');
 rmSync(pending,{force:true});
 writeFileSync(pending,JSON.stringify(mail),{mode:0o600,flag:'wx'});
 renameSync(pending,path);
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
export function smtpHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=MAIL_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/smtp$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   let state=catalog.signIn(actor,environment);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment,state.runtime,true);
   if(request.method==='GET')return answer(reply(200,{data:{...state,smtp:smtpView(readMail(directory,state.runtime))}}));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   state=catalog.signIn(actor,environment);
   const body=input&&typeof input==='object'&&!Array.isArray(input)?input as Record<string,unknown>:{};
   const base=body.base_revision;
   if(base!==undefined&&base!==null&&base!==state.revision)return reply(409,{message:'These settings changed since you opened them. Reload and try again.'});
   const waited=state.updatedAt===null?Infinity:Date.now()-state.updatedAt;
   if(waited>=0&&waited<saveInterval){const busy=reply(429,{message:'Wait a few seconds before saving again.'});busy.headers.set('retry-after',String(Math.ceil((saveInterval-waited)/1000)));return busy;}
   if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new SmtpError('enabled');
   // Everything that can refuse is read and checked before anything is written.
   const before=readMail(directory,state.runtime);
   const settings=readSettings(signInDirectory,state.runtime);
   const after=body.enabled===false?null:mailFromInput(input,before);
   const revision=Math.max(state.revision,settings?.revision??0)+1;
   keepPrior(directory,state.runtime);
   if(after)writeMail(directory,state.runtime,after);else rmSync(file(directory,state.runtime),{force:true});
   // The apply records its outcome against the revision of the sign-in file when there is one, so that file moves with the row.
   if(settings)writeSettingsFile(signInDirectory,state.runtime,{...settings,revision});
   const saved=catalog.requestSignIn(actor,environment,revision,changedSmtpKeys(before,after));
   return answer(reply(202,{data:{...saved,smtp:smtpView(after)}}));
  } catch(error) {
   if(error instanceof SettingsError&&error.message==='file')return reply(409,{message:'The saved sign-in settings file cannot be read on the server (a link, wrong permissions, or a newer version). Fix it on the host.'});
   if(error instanceof SmtpError&&error.message==='file')return reply(409,{message:'The saved mail file cannot be read on the server (a link or wrong permissions). Fix it on the host.'});
   if(error instanceof SmtpError)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} setting`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
