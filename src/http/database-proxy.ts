import {connect,createServer,type Socket} from 'node:net';
import {createServer as createTlsServer,type Server as TlsServer} from 'node:tls';
import {ACCEPT_GRACE_MS,pause} from './local-server';

/** Direct database access for one environment at a time (docs/guides/database-access.md).
 *
 * A PostgreSQL client connects here; this reads its startup message and passes the connection
 * to the shared database only when the login is an environment's developer login asking for
 * that environment's own database. Every other login, the superuser included, is refused
 * before a byte reaches PostgreSQL, which then checks the password itself. TLS is declined:
 * the listener is on loopback by default, reached over an SSH tunnel. With a certificate
 * (`tls`, src/http/database-tls.ts) it answers SSLRequest with `S` and serves TLS 1.2 or newer, which
 * clients such as Cloudflare Hyperdrive require; `requireTls` then refuses a cleartext startup. A
 * `guard` (src/http/database-external.ts) decides, before a byte reaches PostgreSQL, whether an
 * environment may be reached on this listener and watches how the password check ends. */

const SSL_REQUEST=80877103,GSS_REQUEST=80877104,CANCEL_REQUEST=80877102,PROTOCOL_3=196608;
const MAX_STARTUP=10_000;
export const DEVELOPER_LOGIN=/^(e_[a-f0-9]{24})_developer$/;

export type StartupDecision={ok:true;user:string;database:string}|{ok:false;message:string};

/** Reads `user` and `database` from a protocol 3 startup message body (after length and version). */
export function startupFields(body:Buffer):Record<string,string> {
 const fields:Record<string,string>={};
 let offset=0;
 while(offset<body.length){
  const end=body.indexOf(0,offset);
  if(end<0||end===offset)break;
  const key=body.subarray(offset,end).toString('utf8');
  const valueEnd=body.indexOf(0,end+1);
  if(valueEnd<0)break;
  fields[key]=body.subarray(end+1,valueEnd).toString('utf8');
  offset=valueEnd+1;
 }
 return fields;
}

export function decide(fields:Record<string,string>):StartupDecision {
 const user=fields.user??'';
 const runtime=user.match(DEVELOPER_LOGIN)?.[1];
 if(!runtime)return {ok:false,message:'Only an environment developer login (e_…_developer) can connect here'};
 const database=fields.database||user;
 if(database!==runtime)return {ok:false,message:`The developer login connects to its own database, ${runtime}`};
 return {ok:true,user,database};
}

function errorPacket(message:string) {
 const fields=Buffer.concat([Buffer.from('SFATAL\0VFATAL\0C28000\0M'),Buffer.from(message),Buffer.from('\0\0')]);
 const header=Buffer.alloc(5);header.write('E',0);header.writeInt32BE(fields.length+4,1);
 return Buffer.concat([header,fields]);
}

/** Decides which connections a listener takes. `admit` answers a refusal message or undefined;
 * `outcome` is told how the password check of an admitted connection ended. */
export type ConnectionGuard={
 accept:(remote:string)=>string|undefined;
 admit:(runtime:string,remote:string)=>string|undefined;
 outcome:(runtime:string,remote:string,result:'ok'|'bad-password')=>void;
 /** An admitted session is registered so the guard can end it when the environment loses access; returns the unregister. */
 session?:(runtime:string,close:()=>void)=>()=>void;
};
type ProxyOptions={port:number;host:string;target:()=>{host:string;port:number}|undefined;log?:(line:string)=>void;
 tls?:{cert:string;key:string};requireTls?:boolean;guard?:ConnectionGuard;maxConnections?:number;startupMs?:number};
type ProxyHandle={port:number;stop:()=>void;drain:(graceMs?:number)=>Promise<void>};
type DirectEndpoint={host:string;port:number;ready:boolean};
const observedProxies=new WeakMap<ProxyHandle,()=>DirectEndpoint>();
const innerPorts=new WeakMap<ProxyHandle,()=>number>();
/** The loopback port of the in-process TLS server (tests only: it admits nothing that did not come through the listener). */
export const innerTlsPort=(handle:ProxyHandle)=>innerPorts.get(handle)?.();
const reloaders=new WeakMap<ProxyHandle,(tls:{cert:string;key:string})=>Promise<void>>();
const directOwners=new WeakMap<DirectDatabase,{proxy?:ProxyHandle;external?:ProxyHandle;externalCa?:string;closed:boolean;starting:boolean}>();

/** One application's listener lifetime. Successful real binds alone establish availability. */
export class DirectDatabase {
 constructor(){directOwners.set(this,{closed:false,starting:false});}
 async start(options:ProxyOptions):Promise<void>{
  const owned=directOwners.get(this)!;
  if(owned.closed||owned.starting||owned.proxy)throw new Error('Database listener already started or stopped');
  owned.starting=true;
  try{
   const proxy=await databaseProxy(options);
   if(owned.closed){proxy.stop();return;}
   owned.proxy=proxy;
  }finally{owned.starting=false;}
 }
 /** A renewed certificate for both listeners (src/http/database-tls.ts): connections that start from now on use it. */
 async reloadTls(tls:{cert:string;key:string}):Promise<void>{
  const owned=directOwners.get(this)!;
  const failures:string[]=[];
  for(const proxy of [owned.proxy,owned.external])
   if(proxy)try{await reloaders.get(proxy)?.(tls);}catch(error){failures.push((error as Error).message);}
  if(failures.length)throw new Error(failures.join('; '));
 }
 /** The external listener (TLS only, per-environment opt-in; src/http/database-external.ts). `ca` is the
  * public certificate authority clients verify it with. */
 async startExternal(options:ProxyOptions,ca:string):Promise<void>{
  const owned=directOwners.get(this)!;
  if(owned.closed||owned.external)throw new Error('External database listener already started or stopped');
  const proxy=await databaseProxy({...options,requireTls:true});
  if(owned.closed){proxy.stop();return;}
  owned.external=proxy;owned.externalCa=ca;
 }
 /** Stops accepting after the grace and settles once every session has ended; stop() closes the rest. */
 drain(graceMs?:number):Promise<void>{
  const owned=directOwners.get(this)!;
  return Promise.all([owned.proxy?.drain(graceMs),owned.external?.drain(graceMs)]).then(()=>undefined);
 }
 stop(){
  const owned=directOwners.get(this)!;owned.closed=true;owned.proxy?.stop();owned.proxy=undefined;
  owned.external?.stop();owned.external=undefined;
 }
}

export function directDatabaseEndpoint(direct?:DirectDatabase):DirectEndpoint|undefined {
 const owned=direct&&directOwners.get(direct);
 return owned&&!owned.closed&&owned.proxy?observedProxies.get(owned.proxy)?.():undefined;
}

export function externalDatabaseEndpoint(direct?:DirectDatabase):(DirectEndpoint&{ca:string})|undefined {
 const owned=direct&&directOwners.get(direct);
 const observed=owned&&!owned.closed&&owned.external?observedProxies.get(owned.external)?.():undefined;
 return observed&&owned?.externalCa?{...observed,ca:owned.externalCa}:undefined;
}

/** The listener binds with SO_REUSEPORT, so a newer version's process can listen on the same
 * port while this one drains: `drain()` keeps accepting for the grace (ACCEPT_GRACE_MS, which
 * says why), then stops accepting and lets every session, and every connection still in its
 * startup, run until it ends; `stop()` closes them. */
export function databaseProxy(options:ProxyOptions) {
 // Retain the exact address and forwarding callback used for this listener's lifetime.
 options=Object.freeze({...options});
 const sockets=new Set<Socket>();
 // Bun cannot start TLS on an accepted socket after the SSLRequest, so a client that asks for TLS is
 // piped to a TLS listener on a loopback port of this process, which hands the decrypted stream back
 // to `serve`. A connection that did not come through the outer listener (no entry in `pending`) is dropped.
 const pending=new Map<string,{raw:Socket;remote:string;address:string;timer:ReturnType<typeof setTimeout>}>();
 let inner:TlsServer|undefined,innerPort=0;
 const server=createServer(raw=>{
  sockets.add(raw);raw.once('close',()=>sockets.delete(raw));
  raw.on('error',()=>raw.destroy());
  const remote=raw.remoteAddress??'';
  let early:string|undefined;
  try{early=options.guard?.accept(remote);}catch{early='External database access is unavailable';}
  if(early){options.log?.(`database refused: ${early}`);raw.end(errorPacket(early));return;}
  serve(raw,raw,remote,false);
  });
 function serve(client:Socket,base:Socket,remote:string,secured:boolean){
  client.on('error',()=>client.destroy());
  let buffer=Buffer.alloc(0);
  const timer=setTimeout(()=>base.destroy(),options.startupMs??10_000);
  const refuse=(message:string)=>{clearTimeout(timer);options.log?.(`database refused: ${message}`);client.end(errorPacket(message));};
  const onData=(chunk:Buffer)=>{
   buffer=Buffer.concat([buffer,chunk]);
   while(buffer.length>=8){
    const length=buffer.readInt32BE(0),code=buffer.readInt32BE(4);
    if(length<8||length>MAX_STARTUP)return refuse('Invalid startup message');
    if(buffer.length<length)return;
    if(length===8&&code===SSL_REQUEST&&inner&&!secured){
     // Bytes after SSLRequest in the same read would be plaintext injected before the handshake.
     if(buffer.length>8)return refuse('Invalid startup message');
     clearTimeout(timer);client.off('data',onData);client.pause();
     // The client sends nothing until it reads S, so the pipe starts after S is written. The handshake
     // has its own 10 s bound here: Bun does not enforce handshakeTimeout, and a client that never
     // starts it would hold a connection slot for ever.
     base.write('S');
     const handshake=setTimeout(()=>base.destroy(),options.startupMs??10_000);
     // Keyed by the inner port as well: after a certificate swap two inner servers exist, and the same
     // ephemeral source port can be handed to a connection of each.
     let port='';
     const target=innerPort;
     const up=connect(target,'127.0.0.1',()=>{
      port=`${target}:${up.localPort}`;
      pending.set(port,{raw:base,remote,address:up.localAddress!,timer:handshake});
      base.pipe(up);up.pipe(base);base.resume();
     });
     // up.localPort is gone once the socket closes, so the key is kept from the connect.
     const done=()=>{clearTimeout(handshake);if(port&&pending.get(port)?.raw===base)pending.delete(port);up.destroy();base.destroy();};
     up.once('close',done);base.once('close',done);up.on('error',()=>{});
     return;
    }
    if(length===8&&(code===SSL_REQUEST||code===GSS_REQUEST)){client.write('N');buffer=buffer.subarray(8);continue;}
    const message=buffer.subarray(0,length);buffer=buffer.subarray(length);
    if(code===CANCEL_REQUEST){
     // A cancel carries no login, so it cannot be admitted; on a TLS-only listener it must come over TLS.
     if(options.requireTls&&!secured)return client.destroy();
     buffer=Buffer.alloc(0);
     return forward(message,undefined);
    }
    if(code!==PROTOCOL_3)return refuse('Unsupported protocol version');
    if(options.requireTls&&!secured)return refuse('This address requires TLS (sslmode=require)');
    const decision=decide(startupFields(message.subarray(8)));
    if(!decision.ok)return refuse(decision.message);
    let denied:string|undefined;
    try{denied=options.guard?.admit(decision.database,remote);}catch{denied='External database access is unavailable';}
    if(denied)return refuse(denied);
    return forward(message,decision.user);
   }
  };
  const forward=(first:Buffer,user:string|undefined)=>{
   clearTimeout(timer);client.off('data',onData);client.pause();
   const target=options.target();
   if(!target)return refuse('The database is not running');
   const close=()=>{client.destroy();upstream.destroy();};
   const upstream=connect(target.port,target.host,()=>{
    upstream.write(first);
    if(!user){
     // A cancel gets no answer and nothing after it: PostgreSQL ends it, and this must not depend on that.
     upstream.pipe(client);client.on('data',()=>{});client.resume();
     return;
    }
    if(buffer.length)upstream.write(buffer);
    if(options.guard){
     const runtime=user.match(DEVELOPER_LOGIN)![1]!;
     let release:(()=>void)|undefined;
     try{watchOutcome(upstream,runtime,remote,options.guard);release=options.guard.session?.(runtime,close);}catch{close();return;}
     if(release)client.once('close',release);
    }
    client.pipe(upstream);upstream.pipe(client);client.resume();
    options.log?.(`database connection for ${user}${secured?' (tls)':''}`);
   });
   upstream.on('error',()=>{if(!client.destroyed)client.end(errorPacket('The database is not reachable'));upstream.destroy();});
   client.once('close',close);upstream.once('close',()=>client.destroy());
  };
  client.on('data',onData);
 }
 server.maxConnections=options.maxConnections??64;
 const makeInner=(tls:{cert:string;key:string})=>new Promise<{server:TlsServer;port:number}>((resolve,reject)=>{
  let server:TlsServer;
  try{
   server=createTlsServer({cert:tls.cert,key:tls.key,minVersion:'TLSv1.2',handshakeTimeout:10_000},tlsSocket=>{
    tlsSocket.on('error',()=>tlsSocket.destroy());
    const key=`${(server.address() as {port:number}).port}:${tlsSocket.remotePort}`;
    const entry=pending.get(key);
    // One claim per piped connection, and only from the address the outer listener connected from.
    if(!entry||entry.address!==tlsSocket.remoteAddress){tlsSocket.destroy();return;}
    pending.delete(key);clearTimeout(entry.timer);
    serve(tlsSocket,entry.raw,entry.remote,true);
   });
  }catch{reject(new Error('The database TLS certificate or key is not usable'));return;}
  server.on('tlsClientError',()=>{});
  const failed=(error:Error)=>reject(error);
  server.once('error',failed);
  server.listen(0,'127.0.0.1',()=>{server.off('error',failed);server.on('error',()=>{});resolve({server,port:(server.address() as {port:number}).port});});
 });
 const startInner=async()=>{
  if(!options.tls)return;
  const made=await makeInner(options.tls);
  inner=made.server;innerPort=made.port;
 };
 /** Bun's setSecureContext does not change what a running TLS server presents, so a renewed
  * certificate starts a new loopback TLS server; the old one finishes the connections it has. */
 let closed=false;
 const swapInner=async(tls:{cert:string;key:string})=>{
  const made=await makeInner(tls);
  // A stop or drain that came while the new server was starting must not leave it listening.
  if(closed){made.server.close();return;}
  const old=inner;inner=made.server;innerPort=made.port;old?.close();
 };
 return startInner().then(()=>new Promise<ProxyHandle>((resolve,reject)=>{
  const failed=(error:Error)=>{inner?.close();reject(error);};
  server.once('error',failed);
  server.listen({port:options.port,host:options.host,reusePort:true},()=>{
   server.off('error',failed);server.on('error',()=>{});
   const address=server.address();
   const boundHost=typeof address==='object'&&address?address.address:options.host;
   let stopped=false;
   const handle={port:typeof address==='object'&&address?address.port:options.port,
    stop(){stopped=true;closed=true;for(const socket of sockets)socket.destroy();server.close();inner?.close();},
    async drain(graceMs=ACCEPT_GRACE_MS){
     stopped=true;await pause(graceMs);server.close();
     return new Promise<void>(resolve=>{
      const settle=()=>{closed=true;inner?.close();resolve();};
      if(!sockets.size){settle();return;}
      const timer=setInterval(()=>{if(!sockets.size){clearInterval(timer);settle();}},25);
     });
    }};
   innerPorts.set(handle,()=>innerPort);
   reloaders.set(handle,tls=>swapInner(tls));
   observedProxies.set(handle,()=>{
    let ready=false;
    try{ready=!stopped&&server.listening&&!!options.target();}catch{}
    return {host:boundHost==='0.0.0.0'||boundHost==='::'?'127.0.0.1':boundHost,port:handle.port,ready};
   });
   resolve(handle);
  });
 }));
}

/** The listener's port and address from SBARBASE_DATABASE_PORT (6543 by default, as Supabase's pooler)
 * and SBARBASE_DATABASE_BIND (127.0.0.1 by default). A port of `off` turns it off. */
export function databaseListen(env:Record<string,string|undefined>=process.env):{port:number;host:string}|null {
 const value=(env.SBARBASE_DATABASE_PORT??'').trim();
 if(value==='off')return null;
 const port=value===''?6543:Number(value);
 if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('SBARBASE_DATABASE_PORT must be a port from 1024 to 65535, or off');
 const host=(env.SBARBASE_DATABASE_BIND??'').trim()||'127.0.0.1';
 if(!/^(\d{1,3}(\.\d{1,3}){3}|::|::1|localhost)$/.test(host))throw new Error('SBARBASE_DATABASE_BIND must be an IP address');
 return {port,host};
}

/** Reads the server's first messages until the password check ends, without changing a byte of the
 * stream: AuthenticationOk (`R` with code 0) is a success, an ErrorResponse with SQLSTATE 28P01
 * is a failed password. Anything else (a SCRAM exchange in progress)
 * keeps watching, for at most 16 messages. */
function watchOutcome(upstream:Socket,runtime:string,remote:string,guard:ConnectionGuard) {
 let pending=Buffer.alloc(0),seen=0,done=false;
 const onData=(chunk:Buffer)=>{
  if(done)return;
  pending=Buffer.concat([pending,chunk]);
  while(pending.length>=5){
   const length=pending.readInt32BE(1);
   if(length<4||length>65_536){done=true;break;}
   if(pending.length<1+length)return;
   const type=String.fromCharCode(pending[0]!),body=pending.subarray(5,1+length);
   pending=pending.subarray(1+length);
   if(type==='R'&&body.length>=4&&body.readInt32BE(0)===0){guard.outcome(runtime,remote,'ok');done=true;break;}
   if(type==='E'){
    const code=/(?:^|\0)C(\w{5})\0/.exec(body.toString('latin1'))?.[1];
    if(code==='28P01')guard.outcome(runtime,remote,'bad-password');
    done=true;break;
   }
   if(++seen>=16){done=true;break;}
  }
  if(done){pending=Buffer.alloc(0);upstream.off('data',onData);}
 };
 upstream.on('data',onData);
}
