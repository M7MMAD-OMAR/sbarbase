import {serveLocal,consolePort,bindWithRetry,drainDeadline,drainListeners,stopOnSignals} from '../src/http/local-server';
import {uiStatic} from './ui-static';
import {openUpstreamApplication,studioState} from './upstream-app';
import {studioRuntime} from '../src/control/studio';
import {isRealtimeSocket} from '../src/gateway/realtime';
import {readFileSync,unlinkSync} from 'node:fs';
import {databaseListen} from '../src/http/database-proxy';
import {externalListen,ExternalGuard} from '../src/http/database-external';
import {ensureDatabaseTls,watchDatabaseTls,type DatabaseTls} from '../src/http/database-tls';
import {readJsonCached} from '../src/http/cached-json';
import {upgradeHoldScope,type HoldScope} from '../src/gateway/hold';
import {confirmationProbe,holdExceptProbe} from '../src/gateway/hold-bypass';
import {healthHandler} from '../src/http/health';
import {functionsStorage,functionsStorageListeners,functionsStorageWanted,inSubnet} from '../src/control/functions-storage';
import {candidateRequested,candidateSignals,handoverEnabled,libcSteering,ListenerRecord,namedForHandover,ownDescriptor,PROMOTION_FAILED,publishPromoted,readProtocol,
 STATE_DIRECTORY,withPidHeader,type FixedListener,type Steering} from '../src/http/handover';

// Local experimental API only. No remote bind or default production exposure.
// A server puts the TLS proxy in front of this listener, and the proxy is started
// with a fixed --upstream, so SBARBASE_CONSOLE_PORT pins the loopback port. Without
// it the port is ephemeral, which suits the lab and breaks a proxy on restart.
if(import.meta.main){
// The handover (src/http/handover.ts, lab/handover.py): on only with SBARBASE_HANDOVER=1. A candidate
// is the new version's process while the previous version's still serves; it installs its signal
// handlers before anything else, serves privately, and joins the shared ports when told (SIGUSR2).
const handover=handoverEnabled()?{candidate:candidateRequested(),protocol:readProtocol(),stateDirectory:STATE_DIRECTORY}:undefined;
let promote:()=>Promise<void>=async()=>{},release=()=>{};
let ready=false,earlyStop=false;
const queued:(()=>void)[]=[];
if(handover?.candidate){
 const signalled=candidateSignals({promote:()=>promote(),release:()=>release(),
  failed:error=>{console.error(`Promotion failed: ${(error as Error).message}`);process.exit(PROMOTION_FAILED);}});
 process.on('SIGUSR2',()=>{if(ready)signalled();else queued.push(signalled);});
 // A stop before the gateway is up is carried out as soon as it is (stopOnSignals below).
 process.on('SIGTERM',()=>{earlyStop=true;});process.on('SIGINT',()=>{earlyStop=true;});
}
const app=openUpstreamApplication();
// How long a stop lets in-flight requests and database sessions finish (src/http/local-server.ts).
const drainMs=drainDeadline(process.env.SBARBASE_DRAIN_MS);
// TLS for the database listeners: a private CA and server certificate made on first start. Without
// `openssl` the listeners stay cleartext on loopback and the external one stays off.
let tls:DatabaseTls|undefined;
try{tls=ensureDatabaseTls();}catch(error){console.error(`Database TLS unavailable: ${(error as Error).message}`);}
let gateway:Gateway;
try {
 gateway=await startGateway(app,{hold:upgradeHoldScope(),consolePort:consolePort(process.env.SBARBASE_CONSOLE_PORT),databaseAt:databaseListen(),
  tls,externalAt:tls?externalListen():null,
  endpoints:()=>readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>,
  database:()=>{
   try{const value=readJsonCached('.lab/upstream/database.json') as {host?:string;port?:number};
    return value.host&&value.port?{host:value.host,port:value.port}:undefined;}catch{return undefined;}
  },
  studio:()=>studioState().upstream,handover});
} catch(error) {
 // A fixed listener that still cannot bind stops the process instead of serving without it.
 console.error((error as Error).message);
 app.close();
 process.exit(1);
}
if(handover?.candidate){
 // B12: the private address is what the supervisor probes; nothing is published on the shared ports yet.
 await Bun.write(`${STATE_DIRECTORY}/candidate.json`,JSON.stringify({url:`http://127.0.0.1:${gateway.port}`,pid:process.pid,protocol:handover.protocol}));
 console.log(`Local Sbarbase API (candidate, private): http://127.0.0.1:${gateway.port}`);
}else{
 await Bun.write('.lab/upstream/server.json',JSON.stringify({url:`http://127.0.0.1:${gateway.port}`,pid:process.pid,
  ...(handover?{protocol:handover.protocol}:{})}));
 console.log(`Local Sbarbase API: http://127.0.0.1:${gateway.port}`);
}
function finish(){
 gateway.stop();app.close();
 try {if(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')).pid===process.pid)unlinkSync('.lab/upstream/server.json');}catch {}
 if(handover)new ListenerRecord(STATE_DIRECTORY,process.pid,handover.protocol,handover.candidate?'candidate':'serving').remove();
 if(handover?.candidate)try {unlinkSync(`${STATE_DIRECTORY}/candidate.json`);}catch {}
 process.exit(0);
}
if(handover?.candidate){
 promote=async()=>{
  const at=await gateway.promote!();
  // H3: the shared address is published only now, with the private one beside it.
  publishPromoted(STATE_DIRECTORY,at,`http://127.0.0.1:${gateway.port}`,handover.protocol);
  console.log(`Local Sbarbase API promoted: ${at}`);
 };
 release=()=>gateway.release!();
}
// SIGINT or SIGTERM: every listener drains (Gateway.drain), then finish() runs. A second signal finishes at once.
const stop=stopOnSignals(async()=>{
 console.log(`Stopping: in-flight requests and database sessions have ${drainMs} ms to finish`);
 if(!await gateway.drain(drainMs))console.log('Stopping: closed what was still open at the deadline');
},finish);
if(handover?.candidate){ready=true;for(const run of queued.splice(0))run();}
if(earlyStop)stop();
}

type UpstreamApplication=ReturnType<typeof openUpstreamApplication>;
/** What the gateway needs from the application (lab/upstream-app.ts). */
export type GatewayApplication=Pick<UpstreamApplication,'handler'|'studio'|'upstream'|'realtime'|'database'|'listening'>&{
 catalog:Pick<UpstreamApplication['catalog'],'schemaVersion'|'runtimeReady'|'externalDatabaseEnabled'>;
 keys:Pick<UpstreamApplication['keys'],'check'|'confirmationProbe'>;
};
export type GatewayOptions={
 /** The upgrade hold in force (src/gateway/hold.ts upgradeHoldScope). */
 hold:()=>HoldScope|undefined;
 consolePort:number;
 /** The direct database listener's address, or null when it is off (databaseListen). */
 databaseAt:{port:number;host:string}|null;
 /** The TLS material for the database listeners and the external listener's address (null or absent: off). */
 tls?:DatabaseTls;externalAt?:{port:number;host:string}|null;
 /** Where the renewal check (watchDatabaseTls) reads the TLS material, and how often. */
 tlsDirectory?:string;tlsCheckMs?:number;
 /** The published endpoints (.lab/upstream/endpoints.json), the shared database and Studio's internal address. */
 endpoints:()=>Record<string,any>;
 database:()=>{host:string;port:number}|undefined;
 studio:()=>{host:string;port:number}|undefined;
 /** Bind attempts for each fixed listener and the wait between them (bindWithRetry). */
 bindAttempts?:number;bindDelayMs?:number;
 /** The supervisor's probe token file (src/gateway/hold-bypass.ts PROBE_TOKEN). */
 probeToken?:string;
 /** The in-place handover (src/http/handover.ts, lab/handover.py); absent: nothing of it runs. A
  * `candidate` serves on a private port and opens no fixed listener until `promote()`. */
 handover?:HandoverOptions;
};
export type HandoverOptions={candidate:boolean;protocol:number;stateDirectory?:string;pid?:number;proc?:string;steering?:Steering;
 /** How often the process checks whether it was named for the handover and so must keep its listeners as they are. */
 freezeEveryMs?:number};
export type Gateway=Awaited<ReturnType<typeof startGateway>>;

/** Every listener this process owns, and how the upgrade hold applies to each.
 *
 * While a new version waits for its health checks, application traffic (the gateway, Realtime
 * sockets, direct database access and the Functions' Storage) is held; the console, Studio and
 * /health are not. A hold scoped to the management API holds the same as a full one except the
 * tenant data plane: the gateway routes in DATA_PLANE, Realtime sockets, direct database access
 * and the Functions' Storage keep flowing (src/gateway/hold.ts). The supervisor's own probe of
 * each environment passes under either scope (src/gateway/hold-bypass.ts).
 *
 * `drain` stops every listener gracefully (src/http/local-server.ts drainListeners); `stop`
 * closes them at once. A fixed listener that cannot bind within its retries throws, after
 * closing the listeners already open. */
export async function startGateway(app:GatewayApplication,options:GatewayOptions) {
 const handover=options.handover,candidate=!!handover?.candidate;
 const directory=handover?.stateDirectory??STATE_DIRECTORY,pid=handover?.pid??process.pid,proc=handover?.proc??'/proc';
 const record=handover?new ListenerRecord(directory,pid,handover.protocol,candidate?'candidate':'serving'):undefined;
 const held=()=>options.hold()!==undefined,applicationHeld=()=>options.hold()==='all';
 const probe=confirmationProbe(held,options.probeToken);
 app.keys.confirmationProbe=(_runtime,token)=>probe.key(token);
 const handler=holdExceptProbe(app.handler,options.hold,probe),health=healthHandler(app.catalog,applicationHeld,app.keys);
 // Fixed listeners retry a failed bind for a bounded time (bindWithRetry).
 const bindLog=(name:string)=>(line:string)=>console.error(`${name} listener: ${line}`);
 const attempts=options.bindAttempts,delayMs=options.bindDelayMs;
 // Every fixed listener this process owns (the console on its fixed port, the database listeners, Studio's
 // internal route, one Functions Storage listener per environment): what the handover compares and steers.
 const fixed=new Map<string,FixedListener>();
 let frozen=false;
 // Set at promotion (a candidate) and at the release of the steering (H9a).
 let promoted=false,released=false;
 const publish=()=>{if(record)try{record.write(frozen,[...fixed.values()]);}catch(error){console.error(`Listener record: ${(error as Error).message}`);}};
 // The supervisor named this process for the handover: from here it opens and closes no fixed listener.
 const freeze=()=>{
  if(!handover||(candidate&&!promoted))return false;
  if(!frozen&&namedForHandover(directory,pid)){frozen=true;publish();}
  return frozen;
 };
 const consoleFetch=async(request:Request)=>{
  const url=new URL(request.url);
  if(studioRuntime(url.hostname))return app.studio(request);
  if(!['127.0.0.1','localhost'].includes(url.hostname))return new Response('Invalid host',{status:403});
  if(url.pathname==='/favicon.ico')return new Response(null,{status:204});
  if(url.pathname==='/health')return handover?withPidHeader(health(request),request,pid):health(request);
  return await uiStatic(request)??handler(request);
 };
 const consoleOptions={hostnames:(name:string)=>!!studioRuntime(name),
  // An environment's Realtime socket; Studio hosts never carry one.
  isUpgrade:isRealtimeSocket,upgrade:(path:string,headers:Headers)=>studioRuntime((headers.get('host')??'').replace(/:\d+$/,''))
   ?{ok:false as const,status:404,message:'Unknown route'}
   :applicationHeld()?{ok:false as const,status:503,message:'Sbarbase is confirming an upgrade; try again shortly'}:app.realtime(path,headers)};
 // A candidate serves its handler on a private ephemeral port (the supervisor probes it there and the
 // MCP endpoint reaches the API there for the process's life); the fixed port is bound at promotion.
 const server=await bindWithRetry(()=>serveLocal(consoleFetch,candidate?0:options.consolePort,consoleOptions),
  {attempts:!candidate&&options.consolePort?attempts:1,delayMs,log:bindLog('Console')});
 app.listening(server.port);
 if(!candidate)fixed.set('console',{name:'console',host:'127.0.0.1',port:server.port});
 // Studio's own server-side calls arrive on the runtime network's gateway address, which
 // exists once the runtime is up; the listener opens when the first Studio session records it.
 let internal:Awaited<ReturnType<typeof serveLocal>>|undefined,internalAt='',stopping=false;
 let fixedConsole:Awaited<ReturnType<typeof serveLocal>>|undefined,renewal:(()=>void)|undefined;
 const studioStep=async()=>{
  if(freeze())return;
  const at=options.studio();const want=at?`${at.host}:${at.port}`:'';
  if(want===internalAt)return;
  internal?.stop(true);internal=undefined;internalAt='';fixed.delete('studio-internal');publish();
  if(!at)return;
  try{
   const opened=await serveLocal(app.upstream,at.port,{host:at.host});
   // A stop or drain that began during the bind: the new listener closes at once.
   if(stopping){opened.stop(true);return;}
   internal=opened;internalAt=want;
   fixed.set('studio-internal',{name:'studio-internal',host:at.host,port:at.port});publish();
  }
  catch(error){console.error('Studio internal route unavailable:',(error as Error).message);}
 };
 const startStudioTimer=()=>setInterval(studioStep,handover?.freezeEveryMs??2000);
 let studioTimer=candidate?undefined:startStudioTimer();
 // Edge Functions reach the shared Storage at their environment network's gateway address, with
 // the tenant set here and only sources inside that network admitted
 // (docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md). Uploads get the functions' own 150 s limit.
 const functionsStorageAt=functionsStorageListeners({read:options.endpoints,log:line=>console.error(line),
  open:(runtime,at)=>serveLocal(functionsStorage({runtime,storage:()=>options.endpoints()[runtime]?.storage,
   active:()=>!applicationHeld()&&app.catalog.runtimeReady(runtime)&&!!options.endpoints()[runtime]?.functions}),
   at.port,{host:at.host,admit:source=>inSubnet(source,at.subnet),requestTimeout:150_000})});
 // The Functions Storage listeners that are open, as the record names them.
 const functionsRecord=()=>{
  for(const name of [...fixed.keys()])if(name.startsWith('functions-storage:'))fixed.delete(name);
  const wanted=functionsStorageWanted(options.endpoints());
  for(const runtime of functionsStorageAt.runtimes()){
   const at=wanted.get(runtime);
   if(at)fixed.set(`functions-storage:${runtime}`,{name:`functions-storage:${runtime}`,host:at.host,port:at.port});
  }
  publish();
 };
 const functionsStep=async()=>{if(freeze())return;await functionsStorageAt.reconcile();if(record)functionsRecord();};
 let functionsTimer:ReturnType<typeof setInterval>|undefined;
 if(!candidate){
  await functionsStorageAt.reconcile();
  if(record)functionsRecord();
  functionsTimer=setInterval(()=>void functionsStep(),handover?.freezeEveryMs??2000);
 }
 const stop=()=>{stopping=true;clearInterval(studioTimer);internal?.stop(true);clearInterval(functionsTimer);functionsStorageAt.stop();fixedConsole?.stop(true);server.stop(true);};
 // Direct database access (migrations, psql, an ORM): developer logins only, to their own database.
 const openDatabase=(tries:number|undefined)=>bindWithRetry(()=>app.database.start({...options.databaseAt!,log:line=>console.log(line),
  tls:options.tls,target:()=>applicationHeld()?undefined:options.database()}),{attempts:tries,delayMs,log:bindLog('Direct database')});
 const openExternal=(tries:number|undefined)=>{
  const guard=new ExternalGuard(runtime=>app.catalog.externalDatabaseEnabled(runtime),line=>console.log(line));
  return bindWithRetry(()=>app.database.startExternal({...options.externalAt!,log:line=>console.log(line),tls:options.tls,guard,maxConnections:128,
   target:()=>applicationHeld()?undefined:options.database()},options.tls!.ca),{attempts:tries,delayMs,log:bindLog('External database')});
 };
 if(options.databaseAt&&!candidate){
  try {
   await openDatabase(attempts);
   fixed.set('database',{name:'database',...options.databaseAt});
  } catch(error) {
   stop();
   throw new Error(`Direct database access unavailable: ${(error as Error).message} (stopping; set SBARBASE_DATABASE_PORT=off to run without it)`);
  }
 }
 // External access (docs/engineering/EXTERNAL-DATABASE.md): TLS only, and only the environments that opted in.
 if(options.externalAt&&options.tls&&!candidate){
  try {
   await openExternal(attempts);
   fixed.set('database-external',{name:'database-external',...options.externalAt});
  } catch(error) {
   stop();
   throw new Error(`External database access unavailable: ${(error as Error).message} (stopping; set SBARBASE_DATABASE_EXTERNAL_PORT=off to run without it)`);
  }
 }
 // Stopped by stop() and by drain(), so no renewal reloads a listener once the drain began.
 const watchRenewal=()=>options.tls?watchDatabaseTls(options.tls,tls=>app.database.reloadTls(tls),options.tlsDirectory,undefined,options.tlsCheckMs,
  line=>console.log(line)):undefined;
 if(!candidate)renewal=watchRenewal();
 publish();

 // Promotion (H3), in the candidate only: every fixed listener is bound with SO_REUSEPORT, this
 // process finds its own descriptor for it and attaches the one instruction program to it, and only
 // then are the record and the timers written and started. Any failure stops what was bound and
 // throws: a fixed listener never fails silently at promotion.
 const steered:{name:string;fd:number}[]=[];
 const promote=async():Promise<string>=>{
  const steering=handover!.steering??libcSteering();
  const opened:(()=>void)[]=[];
  const share=(item:FixedListener)=>{
   const fd=ownDescriptor(item.host,item.port,proc);
   if(steering.attach(fd)!==0)throw new Error(`The reuseport program could not be attached to ${item.name}`);
   steered.push({name:item.name,fd});
   fixed.set(item.name,{...item,steered:true});
  };
  try{
   const front=await serveLocal(consoleFetch,options.consolePort,consoleOptions);
   fixedConsole=front;opened.push(()=>front.stop(true));
   share({name:'console',host:'127.0.0.1',port:front.port});
   if(options.databaseAt){
    await openDatabase(1);opened.push(()=>app.database.stop());
    share({name:'database',...options.databaseAt});
   }
   if(options.externalAt&&options.tls){
    await openExternal(1);
    share({name:'database-external',...options.externalAt});
   }
   const at=options.studio();
   if(at){
    const studio=await serveLocal(app.upstream,at.port,{host:at.host});
    internal=studio;internalAt=`${at.host}:${at.port}`;opened.push(()=>studio.stop(true));
    share({name:'studio-internal',host:at.host,port:at.port});
   }
   const wanted=functionsStorageWanted(options.endpoints());
   await functionsStorageAt.reconcile();
   const open=new Set(functionsStorageAt.runtimes());
   if([...wanted.keys()].some(runtime=>!open.has(runtime)))throw new Error('A Functions Storage listener could not be bound');
   for(const [runtime,where] of wanted)share({name:`functions-storage:${runtime}`,host:where.host,port:where.port});
  }catch(error){
   for(const close of opened.reverse())try{close();}catch{}
   functionsStorageAt.stop();
   fixedConsole=undefined;
   throw error;
  }
  publish();
  promoted=true;
  studioTimer=startStudioTimer();
  functionsTimer=setInterval(()=>void functionsStep(),handover?.freezeEveryMs??2000);
  renewal=watchRenewal();
  return `http://127.0.0.1:${options.consolePort}`;
 };
 // H9a: detach the program from every listener this process steered, so the group hashes again.
 // A detach that fails is logged and changes nothing else.
 const releaseSteering=()=>{
  if(!promoted||released)return;
  released=true;
  const steering=handover!.steering??libcSteering();
  for(const item of steered){
   const code=steering.detach(item.fd);
   if(code===0){const current=fixed.get(item.name);if(current)fixed.set(item.name,{...current,steered:false});}
   else console.error(`The reuseport program could not be detached from ${item.name} (setsockopt returned ${code})`);
  }
  // From here this process is the one serving: the next handover reads it as the old one.
  if(record)record.role='serving';
  publish();
 };
 return {port:server.port,stop:()=>{renewal?.();stop();},
  /** The candidate's: binds and steers every fixed listener; the shared console address. */
  promote:candidate?promote:undefined,
  /** The candidate's, once promoted: detaches the steering program from every listener it steered. */
  release:candidate?releaseSteering:undefined,
  /** Every listener stops accepting after the grace (a newer version listening on the same ports
   * with SO_REUSEPORT takes the new connections); in-flight requests and database sessions get
   * until `drainMs`, then the rest is closed. True when everything finished in time. */
  drain(drainMs:number,graceMs?:number):Promise<boolean>{
   stopping=true;clearInterval(studioTimer);clearInterval(functionsTimer);renewal?.();
   return drainListeners([server,fixedConsole,internal,functionsStorageAt,app.database],drainMs,graceMs);
  }};
}
