import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {lstatSync,mkdirSync,readFileSync,renameSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {authConfigView,REGISTRY} from './auth-config';

/** Sign-in settings for one environment: where Auth sends people after a login or an email
 * link, which OAuth providers it offers, and how long its sessions live. The console saves
 * them here; the supervisor recreates that environment's Auth with them
 * (lab/auth_settings.py) and records the outcome. A provider's client secret is written to
 * the private settings file and never read back: answers carry only whether one is set.
 * The rules mirror lab/auth_settings.py `validate`. */

export const PROVIDERS=['apple','azure','bitbucket','discord','facebook','figma','github','gitlab','google','kakao',
 'keycloak','linkedin_oidc','notion','slack_oidc','spotify','twitch','twitter','workos','zoom'] as const;
/** Providers that may name their own server; true where it is required. */
export const PROVIDER_URL:Record<string,boolean>={azure:false,gitlab:false,keycloak:true,workos:false};
const MAX_REDIRECTS=50;
const SECRETS_DIRECTORY='.secrets/upstream';

type Provider={enabled:boolean;client_id:string;secret:string;url:string;skip_nonce_check:boolean;email_optional:boolean};
/** The three session lifetimes Auth applies: the access token lifetime, the longest a person
 * may stay signed in, and the idle time before a sign out. A null member sets no variable,
 * so that session does not expire. The ranges mirror lab/auth_settings.py `validate`. */
export type SignInSession={access_token_seconds:number|null;timebox_seconds:number|null;inactivity_seconds:number|null};
export type SignInSettings={version?:number;revision:number;site_url:string;redirect_urls:string[];signup:boolean;anonymous:boolean;manual_linking:boolean;config:Record<string,boolean|number>;
 session:SignInSession;providers:Record<string,Provider>};
export type SignInView=Omit<SignInSettings,'providers'|'revision'|'version'|'config'>&{config:Record<string,boolean|number>}&{providers:Record<string,{enabled:boolean;client_id:string;
 secret_set:boolean;url:string;skip_nonce_check:boolean;email_optional:boolean}>};
const NO_CAP:SignInSession={access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null};
const SESSION_RANGE:Record<keyof SignInSession,[number,number]>={access_token_seconds:[60,86400],
 timebox_seconds:[3600,315360000],inactivity_seconds:[300,315360000]};
const SESSION_KEYS=Object.keys(SESSION_RANGE) as (keyof SignInSession)[];

/** The file format this version reads and writes (lab/auth_settings.py VERSION). */
const FILE_VERSION=2;

export class SettingsError extends Error {}

function webAddress(value:unknown,field:string):string {
 if(typeof value!=='string'||!value||value.length>2048||/[\s\x00-\x1f]/.test(value))throw new SettingsError(field);
 let url:URL;
 try{url=new URL(value);}catch{throw new SettingsError(field);}
 if(!['http:','https:'].includes(url.protocol)||!url.hostname||url.username||url.password)throw new SettingsError(field);
 return value;
}

function redirectAddress(value:unknown):string {
 if(typeof value!=='string'||!value||value.length>2048||/[\s\x00-\x1f,]/.test(value)||!/^[a-z][a-z0-9+.-]*:\/\//.test(value))
  throw new SettingsError('redirect_urls');
 return value;
}

/** One session object from the console's input. An omitted object, or an omitted member,
 * keeps what is saved, so a save that does not touch sessions cannot wipe them. */
function sessionFromInput(value:unknown,current:SignInSession|null):SignInSession {
 const saved=current??NO_CAP;
 if(value===undefined)return {...saved};
 if(!value||typeof value!=='object'||Array.isArray(value))throw new SettingsError('session');
 const given=value as Record<string,unknown>;
 for(const key of Object.keys(given))if(!(SESSION_KEYS as string[]).includes(key))throw new SettingsError(key);
 const session={...saved};
 for(const key of SESSION_KEYS) {
  const member=given[key];
  if(member===undefined)continue;
  if(member===null){session[key]=null;continue;}
  const [low,high]=SESSION_RANGE[key];
  if(typeof member!=='number'||!Number.isInteger(member)||member<low||member>high)throw new SettingsError(key);
  session[key]=member;
 }
 return session;
}

/** The registry's simple settings (store `config`): booleans and bounded integers, one entry each. */
/** Keys the registry retired: dropped on read and ignored on input, so a file saved before the change still loads. */
const RETIRED=new Set(Object.keys((REGISTRY as unknown as {retired?:Record<string,string>}).retired??{}));
const withoutRetired=(config:Record<string,boolean|number>)=>Object.fromEntries(Object.entries(config).filter(([key])=>!RETIRED.has(key)));
const CONFIG_KEYS=new Map(REGISTRY.core.filter(entry=>entry.store==='config').map(entry=>[entry.key,entry]));

/** One config object from the console's input. An omitted object or member keeps what is saved; null clears a member. */
function configFromInput(value:unknown,current:Record<string,boolean|number>):Record<string,boolean|number> {
 const result=withoutRetired(current);
 if(value===undefined)return result;
 if(!value||typeof value!=='object'||Array.isArray(value))throw new SettingsError('config');
 for(const [key,member] of Object.entries(value as Record<string,unknown>)) {
  if(RETIRED.has(key))continue;
  const entry=CONFIG_KEYS.get(key);
  if(!entry)throw new SettingsError('config');
  if(member===null){delete result[key];continue;}
  if(entry.type==='bool'){if(typeof member!=='boolean')throw new SettingsError(key);result[key]=member;continue;}
  if(typeof member!=='number'||!Number.isInteger(member)||member<entry.min!||member>entry.max!)throw new SettingsError(key);
  result[key]=member;
 }
 return result;
}

/** The settings to save, from the console's input and what is saved now. An empty secret
 * keeps the saved one, so the console never needs to hold it. */
/** The providers the nonce check applies to (lab/auth_settings.py NONCE_PROVIDERS). */
const NONCE_PROVIDERS=['apple','google'];

export function settingsFromInput(input:unknown,current:SignInSettings|null):SignInSettings {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new SettingsError('settings');
 const value=input as Record<string,unknown>;
 const allowed=new Set(['site_url','redirect_urls','signup','anonymous','session','providers','manual_linking','config','base_revision']);
 if(Object.keys(value).some(key=>!allowed.has(key)))throw new SettingsError('settings');
 const site_url=webAddress(value.site_url,'site_url');
 const redirects=value.redirect_urls??[];
 if(!Array.isArray(redirects)||redirects.length>MAX_REDIRECTS)throw new SettingsError('redirect_urls');
 const signup=value.signup??true,anonymous=value.anonymous??false,linking=value.manual_linking??current?.manual_linking??false;
 if(typeof signup!=='boolean')throw new SettingsError('signup');
 if(typeof anonymous!=='boolean')throw new SettingsError('anonymous');
 if(typeof linking!=='boolean')throw new SettingsError('manual_linking');
 const given=value.providers??{};
 if(!given||typeof given!=='object'||Array.isArray(given))throw new SettingsError('providers');
 const providers:Record<string,Provider>={};
 for(const [name,raw] of Object.entries(given as Record<string,unknown>)) {
  if(!(PROVIDERS as readonly string[]).includes(name))throw new SettingsError('providers');
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new SettingsError(name);
  const entry=raw as Record<string,unknown>;
  if(Object.keys(entry).some(key=>!['enabled','client_id','secret','url','skip_nonce_check','email_optional'].includes(key)))throw new SettingsError(name);
  const enabled:unknown=entry.enabled??false,client:unknown=entry.client_id??'',url:unknown=entry.url??'',given:unknown=entry.secret??'';
  const nonce:unknown=entry.skip_nonce_check??current?.providers?.[name]?.skip_nonce_check??false,optional:unknown=entry.email_optional??current?.providers?.[name]?.email_optional??false;
  if(typeof nonce!=='boolean'||typeof optional!=='boolean')throw new SettingsError(name);
  if(typeof enabled!=='boolean'||typeof client!=='string'||typeof given!=='string'||typeof url!=='string')throw new SettingsError(name);
  const secret=given||(current?.providers[name]?.secret??'');
  // Google takes a list of client IDs: spaces around the commas are tidied, an empty entry is not allowed.
  const ids=name==='google'&&client?client.split(',').map(part=>part.trim()).join(','):client;
  if(name==='google'&&ids&&ids.split(',').includes(''))throw new SettingsError(name);
  if(ids.length>(name==='google'?2048:512)||secret.length>4096||/\s/.test(ids+secret))throw new SettingsError(name);
  if(nonce===true&&!NONCE_PROVIDERS.includes(name))throw new SettingsError(name);
  if(enabled&&(!client||!secret))throw new SettingsError(name);
  if(url){if(!(name in PROVIDER_URL))throw new SettingsError(name);webAddress(url,name);}
  else if(enabled&&PROVIDER_URL[name])throw new SettingsError(name);
  providers[name]={enabled,client_id:ids,secret,url,skip_nonce_check:nonce,email_optional:optional};
 }
 return {version:FILE_VERSION,revision:(current?.revision??0)+1,site_url,redirect_urls:redirects.map(redirectAddress),signup,anonymous,manual_linking:linking,
  session:sessionFromInput(value.session,current?.session??null),config:configFromInput(value.config,current?.config??{}),providers};
}

export function view(settings:SignInSettings|null):SignInView|null {
 if(!settings)return null;
 const providers:SignInView['providers']={};
 for(const [name,entry] of Object.entries(settings.providers))
  providers[name]={enabled:entry.enabled,client_id:entry.client_id,secret_set:!!entry.secret,url:entry.url,
   skip_nonce_check:!!entry.skip_nonce_check,email_optional:!!entry.email_optional};
 return {site_url:settings.site_url,redirect_urls:settings.redirect_urls,signup:settings.signup,anonymous:settings.anonymous,manual_linking:!!settings.manual_linking,config:settings.config??{},
  session:settings.session,providers};
}

/** The platform key names of what a save changes, never a value: the audit event lists these. A secret that
 * differs from the saved one shows as its key. The list is capped so the event always fits. */
export function changedKeys(before:SignInSettings|null,after:SignInSettings):string[] {
 const keys:string[]=[];
 const differs=(left:unknown,right:unknown)=>JSON.stringify(left)!==JSON.stringify(right);
 if(before?.site_url!==after.site_url)keys.push('SITE_URL');
 if(differs(before?.redirect_urls??[],after.redirect_urls))keys.push('URI_ALLOW_LIST');
 if((before?.signup??true)!==after.signup)keys.push('DISABLE_SIGNUP');
 if((before?.anonymous??false)!==after.anonymous)keys.push('EXTERNAL_ANONYMOUS_USERS_ENABLED');
 if((before?.manual_linking??false)!==after.manual_linking)keys.push('SECURITY_MANUAL_LINKING_ENABLED');
 for(const key of CONFIG_KEYS.keys())if(before?.config?.[key]!==after.config[key])keys.push(key);
 const was=before?.session??NO_CAP;
 if(was.access_token_seconds!==after.session.access_token_seconds)keys.push('JWT_EXP');
 if(was.timebox_seconds!==after.session.timebox_seconds)keys.push('SESSIONS_TIMEBOX');
 if(was.inactivity_seconds!==after.session.inactivity_seconds)keys.push('SESSIONS_INACTIVITY_TIMEOUT');
 for(const name of new Set([...Object.keys(before?.providers??{}),...Object.keys(after.providers)])) {
  const old=before?.providers?.[name],next=after.providers[name],prefix='EXTERNAL_'+name.toUpperCase()+'_';
  const pairs:[string,unknown,unknown][]=[['ENABLED',old?.enabled??false,next?.enabled??false],['CLIENT_ID',old?.client_id??'',next?.client_id??''],
   ['SECRET',old?.secret??'',next?.secret??''],['URL',old?.url??'',next?.url??''],['SKIP_NONCE_CHECK',old?.skip_nonce_check??false,next?.skip_nonce_check??false],
   ['EMAIL_OPTIONAL',old?.email_optional??false,next?.email_optional??false]];
  for(const [suffix,left,right] of pairs)if(left!==right)keys.push(prefix+suffix);
 }
 return keys.length>60?[...keys.slice(0,60),'AND_MORE']:keys;
}

/** The installation's public address, as lab/auth_settings.py `public_url` reads it. */
export function publicUrl():string {
 const value=(process.env.SBARBASE_PUBLIC_URL??'').trim().replace(/\/+$/,'');
 try{if(value&&['http:','https:'].includes(new URL(value).protocol))return value;}catch{}
 return 'http://localhost';
}

function file(directory:string,runtime:string) {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return join(directory,runtime+'-auth.json');
}

export function readSettings(directory:string,runtime:string):SignInSettings|null {
 const path=file(directory,runtime);
 // As lab/auth_settings.py `read_private`: a link (a dangling one included), or a file open to other users, is not read.
 let status;
 try{status=lstatSync(path);}catch(error){if((error as {code?:string}).code==='ENOENT')return null;throw error;}
 if(!status.isFile()||(status.mode&0o077)!==0)throw new SettingsError('file');
 // A file written before sessions were saved has no `session`: it reads as no cap and stays
 // as it is on disk until the next save writes the whole object.
 const saved=JSON.parse(readFileSync(path,'utf8')) as SignInSettings&{version?:number};
 // A file from a newer version is not rewritten by this one: its fields would be dropped.
 if(saved.version!==undefined&&saved.version>FILE_VERSION)throw new SettingsError('file');
 const session=saved.session&&typeof saved.session==='object'?saved.session:NO_CAP;
 // A file may lack `providers`, which the Python validator reads as none.
 // Files from before manual linking and the provider switches have none of them: all off, as Auth's own default.
 const providers=Object.fromEntries(Object.entries(saved.providers??{}).map(([name,entry])=>[name,{...entry,skip_nonce_check:entry.skip_nonce_check??false,email_optional:entry.email_optional??false}]));
 return {...saved,manual_linking:saved.manual_linking??false,config:withoutRetired(saved.config??{}),providers,session:{...NO_CAP,...session}};
}

/** As lab/auth_settings.py `file_form`: the version 1 shape while no switch added with version 2 is on. */
function fileForm(settings:SignInSettings):Record<string,unknown> {
 const plain=!settings.manual_linking&&Object.keys(settings.config??{}).length===0&&!Object.values(settings.providers).some(entry=>entry.skip_nonce_check||entry.email_optional);
 // An empty config is left out, so a build from before the simple settings still reads a file that only uses version 2 switches.
 if(!plain){const {config,...rest}=settings;return {...rest,...(Object.keys(config??{}).length>0?{config}:{}),version:FILE_VERSION};}
 const {manual_linking:_linking,config:_config,...rest}=settings;
 return {...rest,version:1,providers:Object.fromEntries(Object.entries(settings.providers).map(([name,{skip_nonce_check:_nonce,email_optional:_optional,...entry}])=>[name,entry]))};
}

/** Writes the settings file (private, replaced in one step); the SMTP route uses it to keep the revision in step. */
export function writeSettingsFile(directory:string,runtime:string,settings:SignInSettings) {writeSettings(directory,runtime,settings);}

function writeSettings(directory:string,runtime:string,settings:SignInSettings) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=file(directory,runtime),pending=path+'.pending';
 writeFileSync(pending,JSON.stringify(fileForm(settings)),{mode:0o600});
 renameSync(pending,path);
}

/** GET and PUT `/environments/{id}/sign-in`, and GET `/environments/{id}/auth-config` (the read only
 * Authentication configuration, src/control/auth-config.ts), for owners and admins of a ready environment. */
/** The shortest time between two saves of one environment: each save restarts its Auth under the installation's operation lock. */
export const SAVE_INTERVAL_MS=10_000;

export function signInHandler(catalog:Catalog,identify:ManagementIdentity,directory=SECRETS_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(sign-in|auth-config)$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const readOnly=match[2]==='auth-config';
  if(!(readOnly?['GET']:['GET','PUT']).includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   let state=catalog.signIn(actor,environment);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment,state.runtime,true);
   const callback_url=`${publicUrl()}/${state.runtime}/auth/v1/callback`;
   if(readOnly)return answer(reply(200,{data:{...state,config:authConfigView(readSettings(directory,state.runtime),state.runtime,publicUrl())}}));
   if(request.method==='GET')return answer(reply(200,{data:{...state,callback_url,settings:view(readSettings(directory,state.runtime))}}));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   state=catalog.signIn(actor,environment);
   // A save made from an older page would overwrite a newer one it never saw.
   const base=input&&typeof input==='object'?(input as Record<string,unknown>).base_revision:undefined;
   if(base!==undefined&&base!==null&&base!==state.revision)return reply(409,{message:'These settings changed since you opened them. Reload and try again.'});
   const before=readSettings(directory,state.runtime);
   // A clock that stepped back gives a negative wait: it never locks a save out.
   const waited=state.updatedAt===null?Infinity:Date.now()-state.updatedAt;
   if(waited>=0&&waited<saveInterval){const busy=reply(429,{message:'Wait a few seconds before saving again.'});busy.headers.set('retry-after',String(Math.ceil((saveInterval-waited)/1000)));return busy;}
   const settings=settingsFromInput(input,before);
   // A save of the mail settings moves the row ahead of this file; the revision never goes back below it.
   settings.revision=Math.max(settings.revision,state.revision+1);
   writeSettings(directory,state.runtime,settings);
   const saved=catalog.requestSignIn(actor,environment,settings.revision,changedKeys(before,settings));
   return answer(reply(202,{data:{...saved,callback_url,settings:view(settings)}}));
  } catch(error) {
   if(error instanceof SettingsError&&error.message==='file')return reply(409,{message:'The saved settings file cannot be read on the server (a link, wrong permissions, or a newer version). Fix it on the host.'});
   if(error instanceof SettingsError)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} setting`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
