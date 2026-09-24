import {mkdirSync,readFileSync,readdirSync,renameSync,rmSync,statSync,writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** Edge Functions for one environment (docs/guides/edge-functions.md).
 *
 * GET    /environments/{id}/functions              state, deployed functions and secret names
 * PUT    /environments/{id}/functions              {"enabled": true|false}
 * PUT    /environments/{id}/functions/{name}       deploy: {"files": {"index.ts": "..."}, "shared"?: {...}, "verify_jwt"?: bool}
 * DELETE /environments/{id}/functions/{name}
 * PUT    /environments/{id}/function-secrets       {"secrets": {"NAME": "value" | null}}
 *
 * A deploy writes a new version folder and then switches the environment's manifest to it,
 * so a request runs either the old code or the new, never a mix. `shared` files land in
 * `_shared` next to the function, as in a Supabase project, so `../_shared/cors.ts` imports
 * work unchanged. Secret values are written to a private file and never read back. */

export const FUNCTION_NAME=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const SECRET_NAME=/^[A-Z_][A-Z0-9_]{0,127}$/;
const FILE_PATH=/^[A-Za-z0-9_][A-Za-z0-9_.@+-]*(\/[A-Za-z0-9_][A-Za-z0-9_.@+-]*)*$/;
const MAX_BODY=10*1024*1024,MAX_FILES=500,MAX_SECRETS=100,MAX_SECRET=65536;
const ENTRYPOINTS=['index.ts','index.js','index.tsx','index.jsx','index.mjs'];

export type FunctionEntry={version:string;verify_jwt:boolean;updated_at:number;size:number};
export type Manifest={functions:Record<string,FunctionEntry>};
export class DeployError extends Error {}

function runtimeDirectory(root:string,runtime:string) {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return join(root,runtime);
}

function atomicWrite(path:string,content:string,mode=0o644) {
 mkdirSync(dirname(path),{recursive:true});
 const pending=`${path}.${randomBytes(4).toString('hex')}.pending`;
 writeFileSync(pending,content,{mode});
 renameSync(pending,path);
}

export function readManifest(codeRoot:string,runtime:string):Manifest {
 try{
  const value=JSON.parse(readFileSync(join(runtimeDirectory(codeRoot,runtime),'functions.json'),'utf8'));
  return value&&typeof value.functions==='object'?value as Manifest:{functions:{}};
 }catch{return {functions:{}};}
}

function readSecrets(secretsRoot:string,runtime:string):Record<string,string> {
 try{return JSON.parse(readFileSync(join(runtimeDirectory(secretsRoot,runtime),'secrets.json'),'utf8'));}catch{return {};}
}

function files(value:unknown,field:string):Record<string,string> {
 if(value===undefined)return {};
 if(!value||typeof value!=='object'||Array.isArray(value))throw new DeployError(field);
 const result:Record<string,string>={};
 for(const [path,content] of Object.entries(value as Record<string,unknown>)){
  if(path.length>200||!FILE_PATH.test(path)||path.split('/').some(part=>part==='.'||part==='..')||typeof content!=='string')
   throw new DeployError(`${field}: ${path.slice(0,80)}`);
  result[path]=content;
 }
 return result;
}

/** Checks a deploy request and returns what to write. */
export function deployment(input:unknown):{files:Record<string,string>;shared:Record<string,string>;verify_jwt:boolean} {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new DeployError('body');
 const value=input as Record<string,unknown>;
 if(Object.keys(value).some(key=>!['files','shared','verify_jwt'].includes(key)))throw new DeployError('body');
 const code=files(value.files,'files'),shared=files(value.shared,'shared');
 if(!ENTRYPOINTS.some(name=>name in code))throw new DeployError('files: an index.ts (or index.js) entry point');
 if(Object.keys(code).length+Object.keys(shared).length>MAX_FILES)throw new DeployError('files: at most 500');
 const verify=value.verify_jwt??true;
 if(typeof verify!=='boolean')throw new DeployError('verify_jwt');
 return {files:code,shared,verify_jwt:verify};
}

/** Writes a version and switches the manifest to it; removes versions nothing uses any more. */
export function deploy(codeRoot:string,runtime:string,name:string,input:ReturnType<typeof deployment>,now=Date.now()):FunctionEntry {
 const root=runtimeDirectory(codeRoot,runtime);
 const version=`v${now.toString(36)}-${randomBytes(4).toString('hex')}`;
 let size=0;
 for(const [path,content] of Object.entries(input.files)){atomicWrite(join(root,version,name,path),content);size+=Buffer.byteLength(content);}
 for(const [path,content] of Object.entries(input.shared)){atomicWrite(join(root,version,'_shared',path),content);size+=Buffer.byteLength(content);}
 const manifest=readManifest(codeRoot,runtime);
 const entry={version,verify_jwt:input.verify_jwt,updated_at:now,size};
 manifest.functions[name]=entry;
 atomicWrite(join(root,'functions.json'),JSON.stringify(manifest,null,1));
 prune(root,manifest,now);
 return entry;
}

export function remove(codeRoot:string,runtime:string,name:string,now=Date.now()):boolean {
 const root=runtimeDirectory(codeRoot,runtime);
 const manifest=readManifest(codeRoot,runtime);
 if(!manifest.functions[name])return false;
 delete manifest.functions[name];
 atomicWrite(join(root,'functions.json'),JSON.stringify(manifest,null,1));
 prune(root,manifest,now);
 return true;
}

/** Version folders no function points at, once they are a few minutes old: a request that
 * started on the previous version still finds its files. */
function prune(root:string,manifest:Manifest,now:number) {
 const used=new Set(Object.values(manifest.functions).map(entry=>entry.version));
 for(const entry of readdirSync(root,{withFileTypes:true})){
  if(!entry.isDirectory()||used.has(entry.name)||!/^v[a-z0-9]+-[a-f0-9]{8}$/.test(entry.name))continue;
  const path=join(root,entry.name);
  try{if(now-statSync(path).mtimeMs>5*60_000)rmSync(path,{recursive:true,force:true});}catch{}
 }
}

export function changeSecrets(secretsRoot:string,runtime:string,input:unknown):string[] {
 const changes=(input as {secrets?:unknown}|null)?.secrets;
 if(!changes||typeof changes!=='object'||Array.isArray(changes)||Object.keys(input as object).length!==1)throw new DeployError('secrets');
 const current=readSecrets(secretsRoot,runtime);
 for(const [name,value] of Object.entries(changes as Record<string,unknown>)){
  if(!SECRET_NAME.test(name)||name.startsWith('SUPABASE_')||name.startsWith('SB_'))throw new DeployError(`secret name ${name.slice(0,40)}`);
  if(value===null){delete current[name];continue;}
  if(typeof value!=='string'||value.length>MAX_SECRET)throw new DeployError(`secret ${name}`);
  current[name]=value;
 }
 if(Object.keys(current).length>MAX_SECRETS)throw new DeployError('secrets: at most 100');
 const directory=runtimeDirectory(secretsRoot,runtime);
 mkdirSync(directory,{recursive:true,mode:0o700});
 atomicWrite(join(directory,'secrets.json'),JSON.stringify(current),0o600);
 return Object.keys(current).sort();
}

async function body(request:Request):Promise<unknown> {
 if(Number(request.headers.get('content-length'))>MAX_BODY)throw new DeployError('body: at most 10 MiB');
 const reader=request.body?.getReader();
 if(!reader)throw new DeployError('body');
 const chunks:Uint8Array[]=[];let size=0;
 while(true){
  const item=await reader.read();
  if(item.done)break;
  size+=item.value.byteLength;
  if(size>MAX_BODY){void reader.cancel().catch(()=>{});throw new DeployError('body: at most 10 MiB');}
  chunks.push(item.value);
 }
 try{return JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new DeployError('body: JSON');}
}

export function functionsHandler(catalog:Catalog,identify:ManagementIdentity,codeRoot='.lab/upstream/functions',
 secretsRoot='.secrets/upstream/functions') {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(functions|function-secrets)(?:\/([^/]+))?$/);
  if(!match||(match[2]==='function-secrets'&&match[3]))return reply(404,{message:'Unknown route'});
  const [,environment,kind,name]=match as unknown as [string,string,string,string|undefined];
  const allowed=kind==='function-secrets'?['PUT']:name?['PUT','DELETE']:['GET','PUT'];
  if(!allowed.includes(request.method))return reply(405,{message:'Method not allowed'});
  if(name!==undefined&&!FUNCTION_NAME.test(name))return reply(400,{message:'A function name is letters, digits, - and _, at most 64'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   const state=catalog.functions(actor,environment!);
   const view=()=>{
    const manifest=readManifest(codeRoot,state.runtime);
    return {...catalog.functions(actor,environment!),
     functions:Object.entries(manifest.functions).sort(([a],[b])=>a.localeCompare(b))
      .map(([fn,entry])=>({name:fn,verify_jwt:entry.verify_jwt,updated_at:entry.updated_at,size:entry.size,path:`/${state.runtime}/functions/v1/${fn}`})),
     secrets:Object.keys(readSecrets(secretsRoot,state.runtime)).sort()};
   };
   if(kind==='function-secrets'){
    const names=changeSecrets(secretsRoot,state.runtime,await body(request));
    catalog.recordFunction(actor,environment!,'functions.secrets_changed',{count:names.length});
    return reply(200,{data:{secrets:names}});
   }
   if(!name&&request.method==='GET')return reply(200,{data:view()});
   if(!name){
    let input:unknown;
    try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
    const enabled=(input as {enabled?:unknown}|null)?.enabled;
    if(typeof enabled!=='boolean'||Object.keys(input as object).length!==1)return reply(400,{message:'Send {"enabled": true} or {"enabled": false}'});
    catalog.requestFunctions(actor,environment!,enabled);
    return reply(202,{data:view()});
   }
   if(request.method==='DELETE'){
    if(!remove(codeRoot,state.runtime,name))return reply(404,{message:'Function not found'});
    catalog.recordFunction(actor,environment!,'functions.deleted',{name});
    return reply(200,{data:view()});
   }
   const entry=deploy(codeRoot,state.runtime,name,deployment(await body(request)));
   catalog.recordFunction(actor,environment!,'functions.deployed',{name,version:entry.version,size:entry.size});
   // The first deploy starts the environment's Edge Functions, as on Supabase.
   if(state.desired==='off'&&state.state!=='pending')catalog.requestFunctions(actor,environment!,true);
   return reply(201,{data:{...view(),deployed:{name,...entry}}});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(error instanceof DeployError)return reply(400,{message:`Check the ${message}`});
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Edge Functions change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
