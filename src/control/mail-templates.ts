import defaults from './mail-template-defaults.json';
import {randomBytes,timingSafeEqual} from 'node:crypto';
import {Catalog} from './catalog';
import {type ManagementIdentity} from './auth';
import {SAVE_INTERVAL_MS} from './sign-in';
import {readMail} from './smtp';
import {PRIVATE_DIRECTORY,readPrivateJson,runtimeFile,settingsHandler,wellFormed,writePrivateFile} from './settings-route';

/** Email templates for one environment, as the Supabase cloud's Emails > Templates page: a subject and an HTML body for each of
 * the six messages Auth sends. The pinned Auth takes the address of a page, not a body, and fetches it when it sends. So the
 * console writes the environment's private templates file (`<runtime>-templates.json`, mode 0600, read by lab/mail_templates.py)
 * and serves the bodies on its internal route, `/<runtime>/mail-templates/<token>/<kind>`, on the host's address on the runtime network.
 * Other containers on this host's Docker networks can reach that address (docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md), so the token is
 * the protection. The token is random per environment, lives in that file and in the Auth container's environment, and is never
 * returned. A save records a pending revision, and the supervisor's apply recreates that environment's Auth with the new addresses
 * and subjects, rolling back if it does not start. */

export const KINDS=defaults.kinds;
export const MAX_BODY=32768,MAX_SUBJECT=200,MAX_BYTES=262144;
const VERSION=1;
const TOKEN=/^[a-f0-9]{64}$/;

export class TemplateError extends Error {}

type Entry={subject:string;body:string};
type Saved={version:number;token:string;templates:Record<string,Entry>};
export type TemplateView={kind:string;label:string;subject:string;body:string;default_subject:string;default_body:string;customized:boolean};

const file=(directory:string,runtime:string,applied=false)=>runtimeFile(directory,runtime,applied?'-templates.applied.json':'-templates.json');

/** As lab/mail_templates.py `validate`: the same fields, the same limits. */
function normalize(parsed:unknown):Saved {
 if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new TemplateError('file');
 const document=parsed as Record<string,unknown>;
 if(document.version!==VERSION||typeof document.token!=='string'||!TOKEN.test(document.token))throw new TemplateError('file');
 const given=document.templates;
 if(!given||typeof given!=='object'||Array.isArray(given))throw new TemplateError('file');
 const templates:Record<string,Entry>={};
 for(const [kind,entry] of Object.entries(given as Record<string,unknown>)) {
  if(!KINDS.includes(kind)||!entry||typeof entry!=='object'||Array.isArray(entry))throw new TemplateError('file');
  const extra=Object.keys(entry as object).filter(key=>key!=='subject'&&key!=='body');
  const {subject='',body=''}=entry as Record<string,unknown>;
  if(extra.length>0||typeof subject!=='string'||typeof body!=='string'||!wellFormed(subject)||!wellFormed(body)||Buffer.byteLength(subject)>MAX_SUBJECT
   ||Buffer.byteLength(body)>MAX_BODY||/\x00/.test(subject+body)||/[\r\n]/.test(subject))throw new TemplateError('file');
  if(subject||body)templates[kind]={subject,body};
 }
 return {version:VERSION,token:document.token,templates};
}

/** The saved templates, or null when there are none. A link, a file open to other users or an unreadable one is refused. */
export function readTemplates(directory:string,runtime:string,applied=false):Saved|null {
 const parsed=readPrivateJson(file(directory,runtime,applied),MAX_BYTES,()=>new TemplateError('file'));
 return parsed===undefined?null:normalize(parsed);
}

export function templatesView(saved:Saved|null):TemplateView[] {
 return KINDS.map(kind=>{
  const entry=saved?.templates[kind];
  return {kind,label:(defaults.labels as Record<string,string>)[kind]!,subject:entry?.subject||(defaults.subjects as Record<string,string>)[kind]!,
   body:entry?.body||(defaults.bodies as Record<string,string>)[kind]!,default_subject:(defaults.subjects as Record<string,string>)[kind]!,
   default_body:(defaults.bodies as Record<string,string>)[kind]!,customized:!!entry?.subject||!!entry?.body};
 });
}

/** A body or subject left as the default is stored as nothing. Braces must pair: Auth parses the body as a Go template when it sends,
 * and a broken one would fail every message of that kind. */
function field(value:unknown,name:string,limit:number,single:boolean):string {
 // A lone surrogate is refused: Python could not encode it, and the apply would fail on a file this side wrote.
 if(typeof value!=='string'||!wellFormed(value)||Buffer.byteLength(value)>limit||/\x00/.test(value)||(single&&/[\r\n]/.test(value)))throw new TemplateError(name);
 // Each `{{` must be closed by a `}}` before the next `{{`: a count alone accepts `}} {{`.
 let inside=false;
 for(const mark of value.match(/\{\{|\}\}/g)??[]){if((mark==='{{')===inside)throw new TemplateError(name);inside=mark==='{{';}
 if(inside)throw new TemplateError(name);
 return value;
}

/** The templates file for the console's input: `{templates:{kind:{subject?,body?}}}`. An omitted kind or member keeps what is saved; null or an empty
 * text, or the default text itself, clears it. Returns null when nothing is left. */
export function templatesFromInput(input:unknown,current:Saved|null):Saved|null {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new TemplateError('settings');
 const value=input as Record<string,unknown>;
 for(const key of Object.keys(value))if(!['templates','base_revision'].includes(key))throw new TemplateError('settings');
 const given=value.templates;
 if(!given||typeof given!=='object'||Array.isArray(given))throw new TemplateError('templates');
 const templates:Record<string,Entry>={};
 for(const [kind,entry] of Object.entries(current?.templates??{}))templates[kind]={...entry};
 for(const [kind,change] of Object.entries(given as Record<string,unknown>)) {
  if(!KINDS.includes(kind)||!change||typeof change!=='object'||Array.isArray(change))throw new TemplateError('templates');
  const next={...(templates[kind]??{subject:'',body:''})};
  for(const [member,text] of Object.entries(change as Record<string,unknown>)) {
   if(member!=='subject'&&member!=='body')throw new TemplateError(kind);
   const standard=(member==='subject'?defaults.subjects:defaults.bodies) as Record<string,string>;
   (next as Record<string,string>)[member]=text===null||text===''||text===standard[kind]?'':field(text,`${kind} ${member}`,member==='subject'?MAX_SUBJECT:MAX_BODY,member==='subject');
  }
  if(next.subject||next.body)templates[kind]=next;else delete templates[kind];
 }
 if(Object.keys(templates).length===0)return null;
 const saved:Saved={version:VERSION,token:current?.token??randomBytes(32).toString('hex'),templates};
 if(Buffer.byteLength(JSON.stringify(saved))>MAX_BYTES)throw new TemplateError('templates');
 return saved;
}

/** The platform key names of what a save changed, never a value. */
export function changedTemplateKeys(before:Saved|null,after:Saved|null):string[] {
 const keys:string[]=[];
 for(const kind of KINDS) {
  const was=before?.templates[kind],now=after?.templates[kind];
  if((was?.body??'')!==(now?.body??''))keys.push('MAILER_TEMPLATES_'+kind.toUpperCase());
  if((was?.subject??'')!==(now?.subject??''))keys.push('MAILER_SUBJECTS_'+kind.toUpperCase());
 }
 return keys;
}

/** GET and PUT `/environments/{id}/mail-templates`, for owners and admins of a ready environment. */
export function mailTemplatesHandler(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,directory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS) {
 // Templates are used only while custom SMTP is on; the page says so when it is not.
 const mailOn=(runtime:string)=>{try{return readMail(directory,runtime)!==null;}catch{return false;}};
 return settingsHandler<Saved>(catalog,identify,signInDirectory,saveInterval,{
  segment:'mail-templates',error:TemplateError,noun:'template',
  fileMessage:'The saved templates file cannot be read on the server (a link, wrong permissions or a bad format). Fix it on the host.',
  read:runtime=>readTemplates(directory,runtime),
  next:(input,_body,before)=>templatesFromInput(input,before),
  write:(runtime,after)=>writePrivateFile(directory,file(directory,runtime),after&&JSON.stringify(after),()=>new TemplateError('file')),
  changed:changedTemplateKeys,
  view:(runtime,saved)=>({templates:templatesView(saved),variables:defaults.variables,mail_enabled:mailOn(runtime)})});
}

const notFound=()=>new Response('Not found',{status:404,headers:{'cache-control':'no-store'}});

/** The internal route Auth fetches a body from: `GET /<runtime>/mail-templates/<token>/<kind>`. It answers only a request that names
 * the environment's own token, with that environment's own body, and the same 404 for everything else, so an unknown environment, kind or token cannot be told apart.
 * Returns null for a path that is not this route. */
export function mailTemplateRoute(directory=PRIVATE_DIRECTORY) {
 return (request:Request):Response|null=>{
  const match=new URL(request.url).pathname.match(/^\/(e_[a-f0-9]{24})\/mail-templates\/([a-f0-9]{64})\/([a-z_]{1,32})$/);
  if(!match)return null;
  if(request.method!=='GET')return notFound();
  try {
   // The copy the running Auth was started with, never a save still waiting or one that was rolled back.
   const saved=readTemplates(directory,match[1]!,true);
   const [,,token,kind]=match;
   const given=Buffer.from(token!),expected=Buffer.from(saved?.token??'0'.repeat(64));
   const entry=saved?.templates[kind!];
   if(!saved||given.length!==expected.length||!timingSafeEqual(given,expected)||!entry?.body)return notFound();
   return new Response(entry.body,{status:200,headers:{'content-type':'text/html; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
  } catch {return notFound();}
 };
}
