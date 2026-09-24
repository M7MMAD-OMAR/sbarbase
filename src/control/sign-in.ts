import {existsSync,mkdirSync,readFileSync,renameSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** Sign-in settings for one environment: where Auth sends people after a login or an email
 * link, and which OAuth providers it offers. The console saves them here; the supervisor
 * recreates that environment's Auth with them (lab/auth_settings.py) and records the outcome.
 * A provider's client secret is written to the private settings file and never read back:
 * answers carry only whether one is set. The rules mirror lab/auth_settings.py `validate`. */

export const PROVIDERS=['apple','azure','bitbucket','discord','facebook','figma','github','gitlab','google','kakao',
 'keycloak','linkedin_oidc','notion','slack_oidc','spotify','twitch','twitter','workos','zoom'] as const;
/** Providers that may name their own server; true where it is required. */
export const PROVIDER_URL:Record<string,boolean>={azure:false,gitlab:false,keycloak:true,workos:false};
const MAX_REDIRECTS=50;
const SECRETS_DIRECTORY='.secrets/upstream';

type Provider={enabled:boolean;client_id:string;secret:string;url:string};
export type SignInSettings={revision:number;site_url:string;redirect_urls:string[];signup:boolean;anonymous:boolean;
 providers:Record<string,Provider>};
export type SignInView=Omit<SignInSettings,'providers'|'revision'>&{providers:Record<string,{enabled:boolean;client_id:string;
 secret_set:boolean;url:string}>};

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

/** The settings to save, from the console's input and what is saved now. An empty secret
 * keeps the saved one, so the console never needs to hold it. */
export function settingsFromInput(input:unknown,current:SignInSettings|null):SignInSettings {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new SettingsError('settings');
 const value=input as Record<string,unknown>;
 const allowed=new Set(['site_url','redirect_urls','signup','anonymous','providers']);
 if(Object.keys(value).some(key=>!allowed.has(key)))throw new SettingsError('settings');
 const site_url=webAddress(value.site_url,'site_url');
 const redirects=value.redirect_urls??[];
 if(!Array.isArray(redirects)||redirects.length>MAX_REDIRECTS)throw new SettingsError('redirect_urls');
 const signup=value.signup??true,anonymous=value.anonymous??false;
 if(typeof signup!=='boolean')throw new SettingsError('signup');
 if(typeof anonymous!=='boolean')throw new SettingsError('anonymous');
 const given=value.providers??{};
 if(!given||typeof given!=='object'||Array.isArray(given))throw new SettingsError('providers');
 const providers:Record<string,Provider>={};
 for(const [name,raw] of Object.entries(given as Record<string,unknown>)) {
  if(!(PROVIDERS as readonly string[]).includes(name))throw new SettingsError('providers');
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new SettingsError(name);
  const entry=raw as Record<string,unknown>;
  if(Object.keys(entry).some(key=>!['enabled','client_id','secret','url'].includes(key)))throw new SettingsError(name);
  const enabled=entry.enabled??false,client=entry.client_id??'',url=entry.url??'';
  let secret=entry.secret??'';
  if(typeof enabled!=='boolean'||typeof client!=='string'||typeof secret!=='string'||typeof url!=='string')throw new SettingsError(name);
  if(!secret)secret=current?.providers[name]?.secret??'';
  if(client.length>512||secret.length>4096||/\s/.test(client+secret))throw new SettingsError(name);
  if(enabled&&(!client||!secret))throw new SettingsError(name);
  if(url){if(!(name in PROVIDER_URL))throw new SettingsError(name);webAddress(url,name);}
  else if(enabled&&PROVIDER_URL[name])throw new SettingsError(name);
  providers[name]={enabled,client_id:client,secret,url};
 }
 return {revision:(current?.revision??0)+1,site_url,redirect_urls:redirects.map(redirectAddress),signup,anonymous,providers};
}

export function view(settings:SignInSettings|null):SignInView|null {
 if(!settings)return null;
 const providers:SignInView['providers']={};
 for(const [name,entry] of Object.entries(settings.providers))
  providers[name]={enabled:entry.enabled,client_id:entry.client_id,secret_set:!!entry.secret,url:entry.url};
 return {site_url:settings.site_url,redirect_urls:settings.redirect_urls,signup:settings.signup,anonymous:settings.anonymous,providers};
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
 if(!existsSync(path))return null;
 return JSON.parse(readFileSync(path,'utf8')) as SignInSettings;
}

function writeSettings(directory:string,runtime:string,settings:SignInSettings) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=file(directory,runtime),pending=path+'.pending';
 writeFileSync(pending,JSON.stringify(settings),{mode:0o600});
 renameSync(pending,path);
}

/** GET and PUT `/environments/{id}/sign-in`, for owners and admins of a ready environment. */
export function signInHandler(catalog:Catalog,identify:ManagementIdentity,directory=SECRETS_DIRECTORY) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/sign-in$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   const state=catalog.signIn(actor,environment);
   const callback_url=`${publicUrl()}/${state.runtime}/auth/v1/callback`;
   if(request.method==='GET')return reply(200,{data:{...state,callback_url,settings:view(readSettings(directory,state.runtime))}});
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   const settings=settingsFromInput(input,readSettings(directory,state.runtime));
   writeSettings(directory,state.runtime,settings);
   const saved=catalog.requestSignIn(actor,environment,settings.revision);
   return reply(202,{data:{...saved,callback_url,settings:view(settings)}});
  } catch(error) {
   if(error instanceof SettingsError)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} setting`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
