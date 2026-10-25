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
import {functionsStorage,functionsStorageListeners,inSubnet} from '../src/control/functions-storage';

// Local experimental API only. No remote bind or default production exposure.
// A server puts the TLS proxy in front of this listener, and the proxy is started
// with a fixed --upstream, so SBARBASE_CONSOLE_PORT pins the loopback port. Without
// it the port is ephemeral, which suits the lab and breaks a proxy on restart.
if(import.meta.main){
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
  studio:()=>studioState().upstream});
} catch(error) {
 // A fixed listener that still cannot bind stops the process instead of serving without it.
 console.error((error as Error).message);
 app.close();
 process.exit(1);
}
await Bun.write('.lab/upstream/server.json',JSON.stringify({url:`http://127.0.0.1:${gateway.port}`,pid:process.pid}));
console.log(`Local Sbarbase API: http://127.0.0.1:${gateway.port}`);
function finish(){
 gateway.stop();app.close();
 try {if(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')).pid===process.pid)unlinkSync('.lab/upstream/server.json');}catch {}
 process.exit(0);
}
// SIGINT or SIGTERM: every listener drains (Gateway.drain), then finish() runs. A second signal finishes at once.
stopOnSignals(async()=>{
 console.log(`Stopping: in-flight requests and database sessions have ${drainMs} ms to finish`);
 if(!await gateway.drain(drainMs))console.log('Stopping: closed what was still open at the deadline');
},finish);
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
};
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
 const held=()=>options.hold()!==undefined,applicationHeld=()=>options.hold()==='all';
 const probe=confirmationProbe(held,options.probeToken);
 app.keys.confirmationProbe=(_runtime,token)=>probe.key(token);
 const handler=holdExceptProbe(app.handler,options.hold,probe),health=healthHandler(app.catalog,applicationHeld,app.keys);
 // Fixed listeners retry a failed bind for a bounded time (bindWithRetry).
 const bindLog=(name:string)=>(line:string)=>console.error(`${name} listener: ${line}`);
 const attempts=options.bindAttempts,delayMs=options.bindDelayMs;
 const server=await bindWithRetry(()=>serveLocal(async request=>{
  const url=new URL(request.url);
  if(studioRuntime(url.hostname))return app.studio(request);
  if(!['127.0.0.1','localhost'].includes(url.hostname))return new Response('Invalid host',{status:403});
  if(url.pathname==='/favicon.ico')return new Response(null,{status:204});
  if(url.pathname==='/health')return health(request);
  return await uiStatic(request)??handler(request);
 },options.consolePort,{hostnames:name=>!!studioRuntime(name),
  // An environment's Realtime socket; Studio hosts never carry one.
  isUpgrade:isRealtimeSocket,upgrade:(path,headers)=>studioRuntime((headers.get('host')??'').replace(/:\d+$/,''))
   ?{ok:false,status:404,message:'Unknown route'}
   :applicationHeld()?{ok:false,status:503,message:'Sbarbase is confirming an upgrade; try again shortly'}:app.realtime(path,headers)}),
  {attempts:options.consolePort?attempts:1,delayMs,log:bindLog('Console')});
 app.listening(server.port);
 // Studio's own server-side calls arrive on the runtime network's gateway address, which
 // exists once the runtime is up; the listener opens when the first Studio session records it.
 let internal:Awaited<ReturnType<typeof serveLocal>>|undefined,internalAt='',stopping=false;
 const studioTimer=setInterval(async()=>{
  const at=options.studio();const want=at?`${at.host}:${at.port}`:'';
  if(want===internalAt)return;
  internal?.stop(true);internal=undefined;internalAt='';
  if(!at)return;
  try{
   const opened=await serveLocal(app.upstream,at.port,{host:at.host});
   // A stop or drain that began during the bind: the new listener closes at once.
   if(stopping){opened.stop(true);return;}
   internal=opened;internalAt=want;
  }
  catch(error){console.error('Studio internal route unavailable:',(error as Error).message);}
 },2000);
 // Edge Functions reach the shared Storage at their environment network's gateway address, with
 // the tenant set here and only sources inside that network admitted
 // (docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md). Uploads get the functions' own 150 s limit.
 const functionsStorageAt=functionsStorageListeners({read:options.endpoints,log:line=>console.error(line),
  open:(runtime,at)=>serveLocal(functionsStorage({runtime,storage:()=>options.endpoints()[runtime]?.storage,
   active:()=>!applicationHeld()&&app.catalog.runtimeReady(runtime)&&!!options.endpoints()[runtime]?.functions}),
   at.port,{host:at.host,admit:source=>inSubnet(source,at.subnet),requestTimeout:150_000})});
 await functionsStorageAt.reconcile();
 const functionsTimer=setInterval(()=>void functionsStorageAt.reconcile(),2000);
 const stop=()=>{stopping=true;clearInterval(studioTimer);internal?.stop(true);clearInterval(functionsTimer);functionsStorageAt.stop();server.stop(true);};
 // Direct database access (migrations, psql, an ORM): developer logins only, to their own database.
 if(options.databaseAt){
  const at=options.databaseAt;
  try {
   await bindWithRetry(()=>app.database.start({...at,log:line=>console.log(line),
    tls:options.tls,target:()=>applicationHeld()?undefined:options.database()}),{attempts,delayMs,log:bindLog('Direct database')});
  } catch(error) {
   stop();
   throw new Error(`Direct database access unavailable: ${(error as Error).message} (stopping; set SBARBASE_DATABASE_PORT=off to run without it)`);
  }
 }
 // External access (docs/engineering/EXTERNAL-DATABASE.md): TLS only, and only the environments that opted in.
 if(options.externalAt&&options.tls){
  const at=options.externalAt;
  try {
   const guard=new ExternalGuard(runtime=>app.catalog.externalDatabaseEnabled(runtime),line=>console.log(line));
   await bindWithRetry(()=>app.database.startExternal({...at,log:line=>console.log(line),tls:options.tls,guard,maxConnections:128,
    target:()=>applicationHeld()?undefined:options.database()},options.tls!.ca),{attempts,delayMs,log:bindLog('External database')});
  } catch(error) {
   stop();
   throw new Error(`External database access unavailable: ${(error as Error).message} (stopping; set SBARBASE_DATABASE_EXTERNAL_PORT=off to run without it)`);
  }
 }
 // Stopped by stop() and by drain(), so no renewal reloads a listener once the drain began.
 const renewal=options.tls?watchDatabaseTls(options.tls,tls=>app.database.reloadTls(tls),options.tlsDirectory,undefined,options.tlsCheckMs,
  line=>console.log(line)):undefined;
 return {port:server.port,stop:()=>{renewal?.();stop();},
  /** Every listener stops accepting after the grace (a newer version listening on the same ports
   * with SO_REUSEPORT takes the new connections); in-flight requests and database sessions get
   * until `drainMs`, then the rest is closed. True when everything finished in time. */
  drain(drainMs:number,graceMs?:number):Promise<boolean>{
   stopping=true;clearInterval(studioTimer);clearInterval(functionsTimer);renewal?.();
   return drainListeners([server,internal,functionsStorageAt,app.database],drainMs,graceMs);
  }};
}
