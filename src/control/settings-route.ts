import {existsSync,lstatSync,mkdirSync,readFileSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog,type SignInState} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {readSettings,writeSettingsFile,SettingsError} from './sign-in';

/** What the per environment settings routes share (`/smtp`, `/sms`, `/mail-templates`): the path match, the management check, the
 * stale page and spacing refusals, the order of reading, checking and writing, the pending revision in the sign-in row, and the
 * answers for every refusal. Each route supplies only its own file, its own validation and its own view. */

/** No lone surrogate: Python could not encode it into the container's environment file. */
export const wellFormed=(text:string)=>!/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/.test(text);

/** A text setting: a string with no line break, NUL or lone surrogate, within `max` characters, empty only where allowed. `control` also refuses every other control character. */
export function cleanText(value:unknown,max:number,options:{allowEmpty?:boolean;control?:boolean}={}):value is string {
 if(typeof value!=='string'||value.length>max||!wellFormed(value))return false;
 if(value===''&&!options.allowEmpty)return false;
 return !(options.control?/[\x00-\x1f\x7f]/:/[\r\n\x00]/).test(value);
}

/** Where the installation keeps its private per environment files. */
export const PRIVATE_DIRECTORY='.secrets/upstream';

/** The private file of one environment: `<directory>/<runtime><suffix>`. */
export function runtimeFile(directory:string,runtime:string,suffix:string) {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return join(directory,runtime+suffix);
}

/** The JSON of a private file, or undefined when there is none. A link (a dangling one too), a file open to other users, one over `maxBytes`
 * or one that is not JSON is refused with `fail()`, as lab/auth_settings.py `read_private` does. */
export function readPrivateJson(path:string,maxBytes:number,fail:()=>Error):unknown {
 let status;
 try{status=lstatSync(path);}catch(error){if((error as {code?:string}).code==='ENOENT')return undefined;throw error;}
 if(!status.isFile()||(status.mode&0o077)!==0||status.size>maxBytes)throw fail();
 try{return JSON.parse(readFileSync(path,'utf8')) as unknown;}catch{throw fail();}
}

/** As `readPrivateJson`, but the file must hold a JSON object: `null`, a list or a bare value is refused, never read as "no file". */
export function readPrivateObject(path:string,maxBytes:number,fail:()=>Error):Record<string,unknown>|null {
 const parsed=readPrivateJson(path,maxBytes,fail);
 if(parsed===undefined)return null;
 if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw fail();
 return parsed as Record<string,unknown>;
}

/** Writes a private file (mode 0600) by a pending copy renamed over it, or removes it when `content` is null. A link in its place is never followed. */
export function writePrivateFile(directory:string,path:string,content:string|null,fail:()=>Error) {
 mkdirSync(directory,{recursive:true,mode:0o700});
 if(existsSync(path)&&lstatSync(path).isSymbolicLink())throw fail();
 if(content===null){rmSync(path,{force:true});return;}
 const pending=path+'.pending';
 rmSync(pending,{force:true});
 writeFileSync(pending,content,{mode:0o600,flag:'wx'});
 renameSync(pending,path);
}

/** The route's own part. `Saved` is what its file holds, null when there is none. */
export type SettingsRoute<Saved> = {
 /** The path after `/environments/{id}/`. */
 segment:string;
 /** The route's error class: its `file` message is a 409, any other a 400 naming the setting. */
 error:new(message:string)=>Error;
 fileMessage:string;
 /** The word after the setting's name in a 400: `Check the host setting`, `Check the body template`. */
 noun:string;
 read:(runtime:string)=>Saved|null;
 /** Throws the route's error for a body that cannot be used, before anything is read. */
 check?:(body:Record<string,unknown>)=>void;
 /** The file to save, from the input and what is saved now; null removes it. */
 next:(input:unknown,body:Record<string,unknown>,before:Saved|null)=>Saved|null;
 write:(runtime:string,after:Saved|null)=>void;
 changed:(before:Saved|null,after:Saved|null)=>string[];
 /** The members the route adds to the sign-in row in its answers. */
 view:(runtime:string,saved:Saved|null)=>Record<string,unknown>;
};

export const SIGN_IN_FILE_MESSAGE='The saved sign-in settings file cannot be read on the server (a link, wrong permissions, or a newer version). Fix it on the host.';

/** GET and PUT `/environments/{id}/<segment>`, for owners and admins of a ready environment. */
export function settingsHandler<Saved>(catalog:Catalog,identify:ManagementIdentity,signInDirectory:string,saveInterval:number,route:SettingsRoute<Saved>) {
 const pattern=new RegExp(`^/management/v1/environments/([a-f0-9-]{36})/${route.segment}$`);
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(pattern);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   let state:SignInState=catalog.signIn(actor,environment);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment,state.runtime,true);
   if(request.method==='GET')return answer(reply(200,{data:{...state,...route.view(state.runtime,route.read(state.runtime))}}));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   state=catalog.signIn(actor,environment);
   const body=input&&typeof input==='object'&&!Array.isArray(input)?input as Record<string,unknown>:{};
   // A save made from an older page would overwrite a newer one it never saw.
   const base=body.base_revision;
   if(base!==undefined&&base!==null&&base!==state.revision)return reply(409,{message:'These settings changed since you opened them. Reload and try again.'});
   // A clock that stepped back gives a negative wait: it never locks a save out.
   const waited=state.updatedAt===null?Infinity:Date.now()-state.updatedAt;
   if(waited>=0&&waited<saveInterval){const busy=reply(429,{message:'Wait a few seconds before saving again.'});busy.headers.set('retry-after',String(Math.ceil((saveInterval-waited)/1000)));return busy;}
   route.check?.(body);
   // Everything that can refuse is read and checked before anything is written.
   const before=route.read(state.runtime);
   const settings=readSettings(signInDirectory,state.runtime);
   const after=route.next(input,body,before);
   const revision=Math.max(state.revision,settings?.revision??0)+1;
   route.write(state.runtime,after);
   // The apply records its outcome against the revision of the sign-in file when there is one, so that file moves with the row.
   if(settings)writeSettingsFile(signInDirectory,state.runtime,{...settings,revision});
   const saved=catalog.requestSignIn(actor,environment,revision,route.changed(before,after));
   return answer(reply(202,{data:{...saved,...route.view(state.runtime,after)}}));
  } catch(error) {
   if(error instanceof SettingsError&&error.message==='file')return reply(409,{message:SIGN_IN_FILE_MESSAGE});
   if(error instanceof route.error&&error.message==='file')return reply(409,{message:route.fileMessage});
   if(error instanceof route.error)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} ${route.noun}`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
