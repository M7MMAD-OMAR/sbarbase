import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {mkdirSync,readFileSync,readdirSync,renameSync,rmSync,statSync,writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {settingsChange,type Boot,type BootCheck} from './functions-write';
import {maskValues,usageView,type UsageSource} from './functions-worker';
import {DEFAULT_SECRETS,functionArchive,functionSource,noteSecretTimes,redactCallPath,secretRows} from './functions-read';
import {redact} from './observe';
import {FUNCTION_RANGES,type FunctionRange,type RequestLog} from '../gateway/observe';

/** Edge Functions for one environment (docs/guides/edge-functions.md).
 *
 * GET    /environments/{id}/functions              state, deployed functions and secret names
 * GET    /environments/{id}/functions/{name}/source    the files of the current version, for the code viewer
 * GET    /environments/{id}/functions/{name}/download  the same files as a project shaped ZIP
 * GET    /environments/{id}/functions/{name}/metrics   calls, errors and response time (?range=15m|1h|3h|1d)
 * GET    /environments/{id}/functions/{name}/logs      the function's recent calls (?errors=1&q=&range=&limit=)
 * GET    /environments/{id}/functions/{name}/usage     CPU time and memory per worker, from the runtime's event worker (?range=15m|1h|3h|1d)
 * GET    /environments/{id}/functions/{name}/output    what this function wrote (?errors=1&q=&limit=), from the event worker
 * GET    /environments/{id}/function-secrets           secret names with a digest and when each was set, and the default secrets
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
const MAX_BODY=10*1024*1024,MAX_FILES=500,MAX_SECRETS=100,MAX_SECRET=65536,MAX_FUNCTIONS=100;
import {ENTRYPOINTS} from './function-contract.mjs';
export {ENTRYPOINTS} from './function-contract.mjs';

/** `created_at` and `deployments` came with the console's function pages; an entry written before them reads as created at its
 * last deploy and deployed once. */
export type FunctionEntry={version:string;verify_jwt:boolean;updated_at:number;size:number;created_at?:number;deployments?:number};
export type Manifest={functions:Record<string,FunctionEntry>};
export class DeployError extends Error {}
/** A function's entry, or undefined. A name such as `constructor` or `toString` is a valid function name and a property of every object, so the lookup counts only what the manifest itself holds. */
export function entryOf(manifest:Manifest,name:string):FunctionEntry|undefined {return Object.hasOwn(manifest.functions,name)?manifest.functions[name]:undefined;}

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

/** Writes a version folder and returns its name and size; the manifest still points at the old version. */
export function stageVersion(codeRoot:string,runtime:string,name:string,input:ReturnType<typeof deployment>,now=Date.now()):{version:string;size:number} {
 const root=runtimeDirectory(codeRoot,runtime);
 const version=`v${now.toString(36)}-${randomBytes(4).toString('hex')}`;
 const before=entryOf(readManifest(codeRoot,runtime),name);
 if(!before&&Object.keys(readManifest(codeRoot,runtime).functions).length>=MAX_FUNCTIONS)throw new DeployError('functions: at most 100 in an environment');
 let size=0;
 for(const [path,content] of Object.entries(input.files)){atomicWrite(join(root,version,name,path),content);size+=Buffer.byteLength(content);}
 for(const [path,content] of Object.entries(input.shared)){atomicWrite(join(root,version,'_shared',path),content);size+=Buffer.byteLength(content);}
 return {version,size};
}

/** A staged version that will not go live: removed at once, as nothing points at it. */
export function discardVersion(codeRoot:string,runtime:string,version:string) {
 if(!/^v[a-z0-9]+-[a-f0-9]{8}$/.test(version))throw new Error('Invalid version');
 rmSync(join(runtimeDirectory(codeRoot,runtime),version),{recursive:true,force:true});
}

/** Switches the manifest to a staged version; removes versions nothing uses any more. */
export function commitVersion(codeRoot:string,runtime:string,name:string,staged:{version:string;size:number},verify:boolean,now=Date.now()):FunctionEntry {
 const root=runtimeDirectory(codeRoot,runtime);
 const manifest=readManifest(codeRoot,runtime);
 const before=entryOf(manifest,name);
 // The cap is checked again here: the boot check between staging and committing can take seconds, and two deploys of new names can both pass at staging.
 if(!before&&Object.keys(manifest.functions).length>=MAX_FUNCTIONS)throw new DeployError('functions: at most 100 in an environment');
 const entry={version:staged.version,verify_jwt:verify,updated_at:now,size:staged.size,created_at:before?(before.created_at??before.updated_at):now,deployments:(before?.deployments??(before?1:0))+1};
 manifest.functions[name]=entry;
 atomicWrite(join(root,'functions.json'),JSON.stringify(manifest,null,1));
 prune(root,manifest,now);
 return entry;
}

/** Writes a version and switches the manifest to it; removes versions nothing uses any more. */
export function deploy(codeRoot:string,runtime:string,name:string,input:ReturnType<typeof deployment>,now=Date.now()):FunctionEntry {
 return commitVersion(codeRoot,runtime,name,stageVersion(codeRoot,runtime,name,input,now),input.verify_jwt,now);
}

/** Turns the JWT check of a deployed function on or off without a new version: the runtime reads the manifest on each call. */
export function setVerifyJwt(codeRoot:string,runtime:string,name:string,verify:boolean):FunctionEntry|undefined {
 const manifest=readManifest(codeRoot,runtime),before=entryOf(manifest,name);
 if(!before)return undefined;
 manifest.functions[name]={...before,verify_jwt:verify};
 atomicWrite(join(runtimeDirectory(codeRoot,runtime),'functions.json'),JSON.stringify(manifest,null,1));
 return manifest.functions[name];
}

export function remove(codeRoot:string,runtime:string,name:string,now=Date.now()):boolean {
 const root=runtimeDirectory(codeRoot,runtime);
 const manifest=readManifest(codeRoot,runtime);
 if(!entryOf(manifest,name))return false;
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

export function changeSecrets(secretsRoot:string,runtime:string,input:unknown,now=Date.now()):string[] {
 const changes=(input as {secrets?:unknown}|null)?.secrets;
 if(!changes||typeof changes!=='object'||Array.isArray(changes)||Object.keys(input as object).length!==1)throw new DeployError('secrets');
 const current=readSecrets(secretsRoot,runtime);
 for(const [name,value] of Object.entries(changes as Record<string,unknown>)){
  if(!SECRET_NAME.test(name)||name.startsWith('SUPABASE_')||name.startsWith('SB_'))throw new DeployError(`secret name ${name.slice(0,40)}`);
  if(value===null){delete current[name];continue;}
  if(typeof value!=='string'||value.length>MAX_SECRET)throw new DeployError(`secret ${name}`);
  current[name]=value;
 }
 const names=Object.keys(changes as Record<string,unknown>);
 if(Object.keys(current).length>MAX_SECRETS)throw new DeployError('secrets: at most 100');
 const directory=runtimeDirectory(secretsRoot,runtime);
 mkdirSync(directory,{recursive:true,mode:0o700});
 atomicWrite(join(directory,'secrets.json'),JSON.stringify(current),0o600);
 // The update times and the digest key sit outside the folder the functions container mounts.
 try{noteSecretTimes(secretsRoot,runtime,Object.fromEntries(names.map(name=>[name,(changes as Record<string,string|null>)[name]!])),now);}catch{}
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
 let input:unknown;
 try{input=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new DeployError('body: JSON');}
 await refreshCurrentManagement();
 return input;
}

export function functionsHandler(catalog:Catalog,identify:ManagementIdentity,codeRoot='.lab/upstream/functions',
 secretsRoot='.secrets/upstream/functions',log?:RequestLog,boot?:Boot,usage?:UsageSource) {
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const match=url.pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(functions|function-secrets)(?:\/([^/]+)(?:\/(source|download|metrics|logs|usage|output))?)?$/);
  if(!match||(match[2]==='function-secrets'&&match[3]))return reply(404,{message:'Unknown route'});
  const [,environment,kind,name,part]=match as unknown as [string,string,string,string|undefined,string|undefined];
  const allowed=kind==='function-secrets'?['GET','PUT']:part?['GET']:name?['PUT','PATCH','DELETE']:['GET','PUT'];
  if(!allowed.includes(request.method))return reply(405,{message:'Method not allowed'});
  if(name!==undefined&&!FUNCTION_NAME.test(name))return reply(400,{message:'A function name is letters, digits, - and _, at most 64'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   let state=catalog.functions(actor,environment!);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment!,state.runtime,true);
   const view=()=>{
    const manifest=readManifest(codeRoot,state.runtime);
    return {...catalog.functions(actor,environment!),
     functions:Object.entries(manifest.functions).sort(([a],[b])=>a.localeCompare(b))
      .map(([fn,entry])=>({name:fn,verify_jwt:entry.verify_jwt,updated_at:entry.updated_at,size:entry.size,path:`/${state.runtime}/functions/v1/${fn}`,
       created_at:entry.created_at??entry.updated_at,deployments:entry.deployments??1})),
     secrets:Object.keys(readSecrets(secretsRoot,state.runtime)).sort()};
   };
   if(kind==='function-secrets'&&request.method==='GET'){
    return answer(reply(200,{data:{secrets:secretRows(secretsRoot,state.runtime,readSecrets(secretsRoot,state.runtime)),defaults:DEFAULT_SECRETS}}));
   }
   if(kind==='function-secrets'){
    const input=await body(request);
    state=catalog.functions(actor,environment!);
    const names=changeSecrets(secretsRoot,state.runtime,input);
    catalog.recordFunction(actor,environment!,'functions.secrets_changed',{count:names.length});
    return answer(reply(200,{data:{secrets:names}}));
   }
   if(!name&&request.method==='GET')return answer(reply(200,{data:view()}));
   if(part&&name){
    const entry=entryOf(readManifest(codeRoot,state.runtime),name);
    if(!entry)return answer(reply(404,{message:'Function not found'}));
    if(part==='source')return answer(reply(200,{data:{name,...functionSource(codeRoot,state.runtime,entry.version,name)}}));
    if(part==='download'){
     const archive=functionArchive(codeRoot,state.runtime,entry.version,name,entry.updated_at);
     return answer(new Response(new Uint8Array(archive),{status:200,headers:{'content-type':'application/zip','content-disposition':`attachment; filename="${name}.zip"`,
      'cache-control':'no-store','x-content-type-options':'nosniff'}}));
    }
    if(part==='usage'||part==='output'){
     const range=url.searchParams.get('range')??'1h';
     if(!Object.hasOwn(FUNCTION_RANGES,range))return reply(400,{message:'Choose a range: 15m, 1h, 3h or 1d'});
     const limit=Number(url.searchParams.get('limit')??'100');
     if(!Number.isInteger(limit)||limit<1||limit>200)return reply(400,{message:'Ask for 1 to 200 lines'});
     // The runtime must be on and must have been started with the event worker; otherwise the page is told there is nothing to show.
     const result=state.state==='on'&&usage?await usage(state.runtime,name):{state:'unavailable' as const};
     if(result.state!=='ok')return answer(reply(200,{data:{available:false,reason:state.state==='on'?'unreachable':'off'}}));
     if(part==='usage')return answer(reply(200,{data:{available:true,known:result.known,...usageView(result.buckets,range as FunctionRange,result.since)}}));
     const needle=(url.searchParams.get('q')??'').slice(0,100).toLowerCase(),errors=url.searchParams.get('errors')==='1';
     const since=Date.now()-FUNCTION_RANGES[range as FunctionRange].minutes*60_000;
     const secretValues=Object.values(readSecrets(secretsRoot,state.runtime));
     const lines=result.lines.filter(line=>line.at>=since&&(!errors||line.level==='Error')).map(line=>({at:line.at,level:line.level,msg:redact(maskValues(line.msg,secretValues))}))
      .filter(line=>!needle||line.msg.toLowerCase().includes(needle)).slice(-limit).reverse();
     return answer(reply(200,{data:{available:true,known:result.known,lines}}));
    }
    const range=url.searchParams.get('range')??'1h';
    if(!Object.hasOwn(FUNCTION_RANGES,range))return reply(400,{message:'Choose a range: 15m, 1h, 3h or 1d'});
    if(!log)return reply(503,{message:'Function counters are unavailable'});
    if(part==='metrics')return answer(reply(200,{data:log.functionMetrics(state.runtime,name,range as FunctionRange)}));
    const limit=Number(url.searchParams.get('limit')??'100');
    if(!Number.isInteger(limit)||limit<1||limit>200)return reply(400,{message:'Ask for 1 to 200 calls'});
    const query=(url.searchParams.get('q')??'').slice(0,100);
    const since=Date.now()-FUNCTION_RANGES[range as FunctionRange].minutes*60_000;
    // The filter looks at what the person would see, the redacted path, never at the stored one: a search must not be able to
    // find a token that the answer hides.
    const needle=query.toLowerCase();
    const calls=log.functionCalls(state.runtime,name,{errors:url.searchParams.get('errors')==='1',since,limit:200}).map(call=>({...call,path:redact(redactCallPath(call.path))}))
     .filter(call=>!needle||`${call.method} ${call.path} ${call.status}`.toLowerCase().includes(needle)).slice(0,limit);
    return answer(reply(200,{data:{calls}}));
   }
   if(!name){
    let input:unknown;
    try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
    await refreshCurrentManagement();
    const enabled=(input as {enabled?:unknown}|null)?.enabled;
    if(typeof enabled!=='boolean'||Object.keys(input as object).length!==1)return reply(400,{message:'Send {"enabled": true} or {"enabled": false}'});
    catalog.requestFunctions(actor,environment!,enabled);
    return answer(reply(202,{data:view()}));
   }
   if(request.method==='DELETE'){
    if(!remove(codeRoot,state.runtime,name))return answer(reply(404,{message:'Function not found'}));
    log?.forgetFunction(state.runtime,name);
    catalog.recordFunction(actor,environment!,'functions.deleted',{name});
    return answer(reply(200,{data:view()}));
   }
   if(request.method==='PATCH'){
    const change=settingsChange(await body(request));
    if(!change)return reply(400,{message:'Send {"verify_jwt": true} or {"verify_jwt": false}'});
    state=catalog.functions(actor,environment!);
    const entry=setVerifyJwt(codeRoot,state.runtime,name,change.verify_jwt);
    if(!entry)return answer(reply(404,{message:'Function not found'}));
    catalog.recordFunction(actor,environment!,'functions.settings_changed',{name,verify_jwt:change.verify_jwt});
    return answer(reply(200,{data:view()}));
   }
   const input=deployment(await body(request));
   // Body reads may outlive membership changes. Recheck before publishing files.
   state=catalog.functions(actor,environment!);
   const staged=stageVersion(codeRoot,state.runtime,name,input);
   // A running runtime is asked to start the new version before anything points at it. A version it cannot start never goes live.
   let check:BootCheck='skipped';
   if(boot&&state.state==='on'){
    // A check that fails to run is not a refusal: the deploy goes on and says it was not checked.
    const result=await boot(state.runtime,staged.version,name).catch(()=>({state:'unreachable'} as const));
    if(result.state==='refused'){
     discardVersion(codeRoot,state.runtime,staged.version);
     catalog.recordFunction(actor,environment!,'functions.deploy_refused',{name});
     return reply(422,{message:result.message,boot_check:'failed'});
    }
    check=result.state==='passed'?'passed':result.state==='unreachable'?'unreachable':'skipped';
    try{state=catalog.functions(actor,environment!);}catch(error){discardVersion(codeRoot,state.runtime,staged.version);throw error;}
   }
   let entry:FunctionEntry;
   try{entry=commitVersion(codeRoot,state.runtime,name,staged,input.verify_jwt);}
   catch(error){discardVersion(codeRoot,state.runtime,staged.version);throw error;}
   log?.deployedFunction(state.runtime,name,entry.updated_at);
   catalog.recordFunction(actor,environment!,'functions.deployed',{name,version:entry.version,size:entry.size,boot_check:check});
   // The first deploy starts the environment's Edge Functions, as on Supabase.
   if(state.desired==='off'&&state.state!=='pending')catalog.requestFunctions(actor,environment!,true);
   return answer(reply(201,{data:{...view(),deployed:{name,...entry,boot_check:check}}}));
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(error instanceof DeployError)return reply(400,{message:`Check the ${message}`});
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Edge Functions change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
