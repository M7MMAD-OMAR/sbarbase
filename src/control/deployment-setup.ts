import {bindManagementPublication} from './management-publication';
import {refreshCurrentManagement} from './management-context';
import {Database} from 'bun:sqlite';
import {randomUUID} from 'node:crypto';
import {closeSync,constants,fchmodSync,fstatSync,lstatSync,mkdirSync,openSync} from 'node:fs';
import {resolve,join,parse} from 'node:path';
import type {Catalog} from './catalog';
import type {ManagementIdentity} from './auth';

/** A preparation intent. Runtime acceptance comes from a separately owned probe role. */
export type DeploymentIntent={version:1;profile:'local'|'public';public_url:string;ipv4:string[];ipv6:string[];
 provider_access:boolean;tls:'managed'|'external'|'local';acme_email:string;certificate_ref:string|null;
 site_url:string;redirect_urls:string[];smtp_ref:string|null};
export type DeploymentPreview={dns_records:{name:string;type:string;value:string}[];callback_url:string;
 auth_external_url:string;site_url:string;redirect_urls:string[];warnings:string[];required_checks:string[];production_ready:false};
export type DeploymentSetupView={revision:number;operation:string|null;state:'unconfigured'|'prepared';
 intent:DeploymentIntent|null;preview:DeploymentPreview|null;runtime_applied:false;production_ready:false};
const MAX_BODY=32768;
const ROUTE=/^\/management\/v1\/environments\/([a-f0-9-]{36})\/deployment-setup$/;
const reply=(status:number,data:unknown)=>Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
export class DeploymentRefusal extends Error{}

/** Read before parsing. Delayed, chunked or unbounded bodies cannot bypass admission. */
async function boundedBody(request:Request):Promise<Uint8Array>{
 const length=request.headers.get('content-length');
 if(length&&(!/^\d+$/.test(length)||Number(length)>MAX_BODY))throw new DeploymentRefusal('configuration_size');
 if(!request.body)throw new DeploymentRefusal('configuration_json');
 const reader=request.body.getReader(),parts:Uint8Array[]=[];let size=0;
 let timedOut=false;
 const abort=()=>void reader.cancel().catch(()=>{});
 const timeout=setTimeout(()=>{timedOut=true;abort();},10000);request.signal.addEventListener('abort',abort,{once:true});
 try{
  for(;;){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;
   if(size>MAX_BODY)throw new DeploymentRefusal('configuration_size');parts.push(next.value);}
  if(request.signal.aborted||timedOut)throw new DeploymentRefusal('request_aborted');
  const out=new Uint8Array(size);let offset=0;for(const part of parts){out.set(part,offset);offset+=part.length;}return out;
 }finally{clearTimeout(timeout);request.signal.removeEventListener('abort',abort);void reader.cancel().catch(()=>{});reader.releaseLock();}
}

/** The same public, repository-owned validator used inside the Linux runtime. */
export async function prepareDeployment(raw:Uint8Array,runtime:string,checkout=process.cwd()):Promise<{intent:DeploymentIntent;preview:DeploymentPreview}>{
 if(raw.byteLength>MAX_BODY||!/^e_[a-f0-9]{24}$/.test(runtime))throw new DeploymentRefusal('configuration_size');
 const child=Bun.spawn(['python3',join(checkout,'lab/domain_transport.py'),'preview','--runtime',runtime],
  {cwd:checkout,stdin:raw,stdout:'pipe',stderr:'pipe',env:{PATH:process.env.PATH??'/usr/bin:/bin',PYTHONDONTWRITEBYTECODE:'1'}});
 const timer=setTimeout(()=>child.kill(),10000);
 try{
  const [code,stdout,stderr]=await Promise.all([child.exited,new Response(child.stdout).text(),new Response(child.stderr).text()]);
  if(stdout.length>65536||stderr.length>4096)throw new DeploymentRefusal('validator_output');
  if(code!==0)throw new DeploymentRefusal('configuration');
  const intent=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw)) as DeploymentIntent;
  const preview=JSON.parse(stdout) as DeploymentPreview;
  // The validator normalizes the origin and addresses; preserve that normalized origin.
  intent.public_url=preview.auth_external_url.slice(0,-('/'+runtime+'/auth/v1').length);
  intent.ipv4=preview.dns_records.filter(record=>record.type==='A').map(record=>record.value);
  intent.ipv6=preview.dns_records.filter(record=>record.type==='AAAA').map(record=>record.value);
  return {intent,preview};
 }catch(error){if(error instanceof DeploymentRefusal)throw error;throw new DeploymentRefusal('validator_unavailable');}
 finally{clearTimeout(timer);if(child.exitCode===null)child.kill();}
}

/** Pin every directory descriptor before opening the private SQLite file. */
function openDirectory(directory:string):number{
 if(!directory.startsWith('/')||resolve(directory)!==directory)throw new DeploymentRefusal('private_directory');
 const root=parse(directory).root;let descriptor=openSync(root,constants.O_RDONLY|constants.O_DIRECTORY);
 try{for(const part of directory.slice(root.length).split('/')){
  const next=openSync(`/proc/self/fd/${descriptor}/${part}`,constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW);
  closeSync(descriptor);descriptor=next;
 }}catch{closeSync(descriptor);throw new DeploymentRefusal('private_directory');}
 const mode=fstatSync(descriptor);
 if(mode.uid!==process.getuid?.()||(mode.mode&0o077)){closeSync(descriptor);throw new DeploymentRefusal('private_directory');}
 return descriptor;
}

/** Separate private intent storage. No pending preparation is labelled applied. */
export class DeploymentSetupStore{
 private db:Database;
 private directoryFd:number;
 constructor(directory:string){
  this.directoryFd=openDirectory(directory);
  const filename=`/proc/self/fd/${this.directoryFd}/setup.sqlite`;
  let descriptor:number;
  try{descriptor=openSync(filename,constants.O_RDWR|constants.O_CREAT|constants.O_NOFOLLOW|constants.O_NONBLOCK,0o600);}
  catch{closeSync(this.directoryFd);throw new DeploymentRefusal('private_file');}
  try{const info=fstatSync(descriptor);if(!info.isFile()||info.uid!==process.getuid?.()||(info.mode&0o077)||info.nlink!==1)
    throw new DeploymentRefusal('private_file');fchmodSync(descriptor,0o600);}
  catch(error){closeSync(this.directoryFd);throw error;}finally{closeSync(descriptor);}
  try{this.db=new Database(filename,{strict:true});this.db.exec(`PRAGMA busy_timeout=3000;PRAGMA journal_mode=DELETE;
   CREATE TABLE IF NOT EXISTS setup(environment TEXT PRIMARY KEY,runtime TEXT NOT NULL,revision INTEGER NOT NULL,
    operation TEXT NOT NULL UNIQUE,actor TEXT NOT NULL,epoch INTEGER NOT NULL,routing_revision INTEGER NOT NULL,intent TEXT NOT NULL,preview TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS events(operation TEXT PRIMARY KEY,environment TEXT NOT NULL,actor TEXT NOT NULL,
    epoch INTEGER NOT NULL,revision INTEGER NOT NULL,created INTEGER NOT NULL);`);}
  catch(error){closeSync(this.directoryFd);throw error;}
 }
 get(environment:string,runtime:string):DeploymentSetupView{
  const row=this.db.query<{revision:number;operation:string;intent:string;preview:string},[string,string]>(
   'SELECT revision,operation,intent,preview FROM setup WHERE environment=? AND runtime=?').get(environment,runtime);
  return {revision:row?.revision??0,operation:row?.operation??null,state:row?'prepared':'unconfigured',
   intent:row?JSON.parse(row.intent):null,preview:row?JSON.parse(row.preview):null,runtime_applied:false,production_ready:false};
 }
 save(environment:string,runtime:string,actor:string,epoch:number,revision:number,prepared:{intent:DeploymentIntent;preview:DeploymentPreview},routingRevision:number):DeploymentSetupView{
  return this.db.transaction(()=>{
   const existing=this.get(environment,runtime);if(existing.revision!==revision)throw new DeploymentRefusal('revision_conflict');
   const op=randomUUID(),next=revision+1;
   this.db.query(`INSERT INTO setup VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(environment) DO UPDATE SET runtime=excluded.runtime,
    revision=excluded.revision,operation=excluded.operation,actor=excluded.actor,epoch=excluded.epoch,intent=excluded.intent,
    routing_revision=excluded.routing_revision,preview=excluded.preview`).run(environment,runtime,next,op,actor,epoch,routingRevision,JSON.stringify(prepared.intent),JSON.stringify(prepared.preview));
   this.db.query('INSERT INTO events VALUES (?,?,?,?,?,?)').run(op,environment,actor,epoch,next,Date.now());
   return this.get(environment,runtime);
  }).immediate();
 }
 close(){this.db.close();closeSync(this.directoryFd);}
}

/** Caller routes through application's native MFA scope. Recheck after every await.
 * Installation roles and the selected environment are serialized by Catalog's
 * ready-environment transaction before persisting or publishing an intent.
 */
export function deploymentSetupHandler(catalog:Catalog,identify:ManagementIdentity,
 directory=resolve('.secrets/deployment'),checkout=process.cwd()){
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(ROUTE);if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT','POST'].includes(request.method))return reply(405,{message:'Method not allowed'});
  let actor:string|null;
  try{actor=await identify(request);}catch{return reply(503,{message:'Authentication unavailable'});}
  if(!actor)return reply(401,{message:'Authentication required'});
  const environment=match[1]!,epoch=catalog.managementSecurity.epoch(actor);
  let initialRuntime:string;
  let routingRevision:number|undefined,runtimeEpoch:number;
  const current=()=>{
   if(catalog.managementSecurity.epoch(actor!)!==epoch||!catalog.installationOperator(actor!))throw new Error('Forbidden');
   if(routingRevision!==undefined){const route=catalog.runtimeRouting(initialRuntime);
    if(route.maintenance||route.revision!==routingRevision)throw new Error('Environment is not ready');}
  };
  try{
   current();initialRuntime=catalog.signIn(actor,environment).runtime;
   routingRevision=catalog.runtimeRouting(initialRuntime).revision;runtimeEpoch=catalog.runtimeEpoch(initialRuntime);current();
   const answer=(response:Response)=>bindManagementPublication(response,catalog,()=>{
    current();return catalog.withReadyEnvironment(actor!,environment,true,job=>{
     if(job.runtime!==initialRuntime||catalog.runtimeEpoch(job.runtime)!==runtimeEpoch)throw new Error('Environment is not ready');
     return response;
    });
   });
   let prepared:{intent:DeploymentIntent;preview:DeploymentPreview}|undefined,revision=0;
   if(request.method!=='GET'){
    const raw=await boundedBody(request);await refreshCurrentManagement();current();
    const version=request.headers.get('if-match');
    if(request.method==='PUT'&&(!version||!/^\d{1,9}$/.test(version)))throw new DeploymentRefusal('revision');
    revision=Number(version??0);
    prepared=await prepareDeployment(raw,initialRuntime,checkout);current();
   }
   // A new Request avoids an identity adapter's per-request cache after body/probe awaits.
   await refreshCurrentManagement();
   const fresh=await identify(new Request(request.url,{headers:request.headers,signal:request.signal}));
   if(fresh!==actor)throw new Error('Forbidden');
   current();
   return catalog.withReadyEnvironment(actor,environment,true,job=>{
    current();if(job.runtime!==initialRuntime)throw new Error('Forbidden');
    if(request.method==='POST')return answer(reply(200,{data:prepared!.preview}));
    // Create only after authority is serialized; refuse unsafe existing directories.
    try{lstatSync(directory);}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;
     const parent=resolve(directory,'..');const fd=openDirectory(parent);closeSync(fd);mkdirSync(directory,{mode:0o700});}
    const store=new DeploymentSetupStore(directory);
    try{
     current();const data=request.method==='GET'?store.get(environment,initialRuntime):
      store.save(environment,initialRuntime,actor!,epoch,revision,prepared!,routingRevision!);
     current();return answer(reply(request.method==='PUT'?202:200,{data}));
    }finally{store.close();}
   });
  }catch(error){
   if(error instanceof DeploymentRefusal)return reply(error.message==='revision_conflict'?409:400,{message:'Check the deployment setup',field:error.message});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(503,{message:'Deployment setup unavailable'});
  }
 };
}
