import {createServer,type ServerResponse} from 'node:http';
import {connect,type Socket} from 'node:net';
import {EventEmitter} from 'node:events';

/** What the listener does with a WebSocket upgrade: connect it to host:port and send `head`,
 * or answer it with a status. */
/** `onOpen` is called once the socket reaches its upstream; the function it returns is called when the socket closes (the Services page counts open sockets). */
export type UpgradeDecision={ok:true;host:string;port:number;head:string;onOpen?:()=>()=>void}|{ok:false;status:number;message:string};
export type Upgrade=(path:string,headers:Headers)=>UpgradeDecision|Promise<UpgradeDecision>;
const MAX_HEAD=16384;
/** An unread request body up to this size is read and discarded after an early answer, for at
 * most DISCARD_WAIT_MS, so the connection can carry the next request. */
const DISCARD_BODY=64*1024,DISCARD_WAIT_MS=1_000;

const HOP_BY_HOP=new Set(['connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade']);

function writable(response:ServerResponse) {
 return new Promise<void>(resolve=>{
  const done=()=>{response.off('drain',done);response.off('close',done);resolve();};
  response.once('drain',done);response.once('close',done);
  if(response.destroyed)done();
 });
}

/** Loopback adapter with explicit backpressure and socket failure on truncation.
 * Bun.serve on the current runtime did not reliably fail partial stream bodies.
 */
/** The pinned loopback port from SBARBASE_CONSOLE_PORT, or 0 for an ephemeral one. */
export function consolePort(value:string|undefined):number {
 if(value===undefined||value==='')return 0;
 if(!/^\d{1,5}$/.test(value)||Number(value)<1024||Number(value)>65535)throw new Error('SBARBASE_CONSOLE_PORT must be a port number from 1024 to 65535');
 return Number(value);
}

/** How long a stop waits for in-flight requests and database sessions before it closes them:
 * lab/dev.py gives the console process 20 s after SIGTERM before SIGKILL (terminate_group), and
 * the rest of the stop (closing the stores, removing server.json) must still fit in that. The
 * systemd unit allows 150 s. SBARBASE_DRAIN_MS changes it, as SBARBASE_PROXY_RETRY_MS does the
 * TLS proxy's retry window. */
export const DRAIN_MS_DEFAULT=15_000;
const DRAIN_MS_MAX=19_000;

/** The stop's deadline from SBARBASE_DRAIN_MS: whole milliseconds from 0 to 19000, below
 * lab/dev.py's 20 s. Unset or empty means DRAIN_MS_DEFAULT. */
export function drainDeadline(value:string|undefined):number {
 if(value===undefined||value==='')return DRAIN_MS_DEFAULT;
 if(!/^\d{1,5}$/.test(value)||Number(value)>DRAIN_MS_MAX)throw new Error(`SBARBASE_DRAIN_MS must be a number of milliseconds from 0 to ${DRAIN_MS_MAX}`);
 return Number(value);
}

/** How long a draining listener keeps accepting before it closes. On Linux, closing one socket
 * of a SO_REUSEPORT group resets the connections still waiting in its accept queue
 * (net.ipv4.tcp_migrate_req is 0 by default). Measured on the development host: a close made
 * while the process was busy reset 18 to 23 of a 40 connection burst; with arrivals every 2 ms
 * across the close, 0 to 13 per close still reset, with or without this grace. During the grace
 * the listener still accepts, so what was queued is taken and answered, and every answer says
 * `connection: close` so pooled clients reconnect, to a newer process on the same port when one
 * listens. Only a supervisor that steers new connections to the newer process first (a reuseport
 * BPF program) leaves the queue empty at the close; the grace gives that steering time to take
 * effect. 300 ms is many times the time to accept a queued burst and short against the deadline. */
export const ACCEPT_GRACE_MS=300;

/** Waits `ms`; zero resolves on the next turn. */
export const pause=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms));

/** Something that can stop gracefully: `drain` keeps accepting for `graceMs`
 * (ACCEPT_GRACE_MS), then stops accepting and settles once what it carries has finished;
 * `stop(true)` closes whatever is left. */
export type Drainable={drain(graceMs?:number):Promise<void>;stop(force?:boolean):void};

/** Drains every listener at once and, after `deadlineMs`, force-stops all of them, finished or
 * not. True when everything finished in time. */
export async function drainListeners(listeners:(Drainable|undefined)[],deadlineMs:number,graceMs=ACCEPT_GRACE_MS):Promise<boolean> {
 const present=listeners.filter((listener):listener is Drainable=>!!listener);
 let timer:ReturnType<typeof setTimeout>|undefined;
 const finished=await Promise.race([
  Promise.all(present.map(listener=>listener.drain(graceMs).catch(()=>{}))).then(()=>true),
  new Promise<boolean>(resolve=>{timer=setTimeout(()=>resolve(false),deadlineMs);})]);
 clearTimeout(timer);
 for(const listener of present)listener.stop(true);
 return finished;
}

/** Binds a fixed listener, retrying a failed bind every `delayMs` up to `attempts` times
 * (10 s by default): the port may still be held by a process that is exiting, or by an older
 * version that bound it without SO_REUSEPORT. The last error is thrown, and the caller fails
 * closed instead of running without the listener. */
export async function bindWithRetry<T>(open:()=>Promise<T>,{attempts=20,delayMs=500,log}:
 {attempts?:number;delayMs?:number;log?:(line:string)=>void}={}):Promise<T> {
 for(let attempt=1;;attempt++){
  try{return await open();}
  catch(error){
   if(attempt>=attempts)throw error;
   log?.(`bind failed (${(error as Error).message}), retrying (${attempt}/${attempts})`);
   await pause(delayMs);
  }
 }
}

/** A process's stop: the first SIGINT or SIGTERM runs `drain` and then `finish`; a second
 * signal of either kind runs `finish` at once. `finish` runs once and should end the process. */
export function stopOnSignals(drain:()=>Promise<unknown>,finish:()=>void,
 on:(signal:'SIGINT'|'SIGTERM',handler:()=>void)=>void=(signal,handler)=>{process.on(signal,handler);}) {
 let stopping=false,finished=false;
 const end=()=>{if(finished)return;finished=true;finish();};
 const stop=()=>{
  if(stopping){end();return;}
  stopping=true;
  void drain().catch(()=>{}).finally(end);
 };
 on('SIGINT',stop);on('SIGTERM',stop);
 return stop;
}

/** The longest a drain waits, after the grace, for the event loop to have a free turn
 * (settled). A loop held by handlers for up to about a second catches up within it; one that
 * stays saturated longer is exactly the case where new connections must go to the newer
 * process, so the listener stops accepting anyway. Grace plus this is 1.3 s, well under the
 * 15 s deadline (DRAIN_MS_DEFAULT) that in-flight work gets. */
export const SETTLE_MS_MAX=1_000;

/** Waits until the event loop has had a free turn to read what is waiting on its sockets. The
 * timer that ends the grace runs before the loop polls, so bytes that arrived while the process
 * was busy (a handler holding the loop) are not read yet when it fires. A 10 ms wait that ends
 * on time means the loop polled its sockets while waiting; one that ends late means it was busy
 * again, so it waits once more, until `limitMs` (SETTLE_MS_MAX) has passed in all. */
async function settled(limitMs=SETTLE_MS_MAX):Promise<void> {
 const until=performance.now()+limitMs;
 while(performance.now()<until){
  const at=performance.now();await pause(10);
  if(performance.now()-at<30)return;
 }
}

/** Settles once `count()` reaches zero, checked every 25 ms. */
function empty(count:()=>number):Promise<void> {
 return new Promise(resolve=>{
  if(count()===0){resolve();return;}
  const timer=setInterval(()=>{if(count()===0){clearInterval(timer);resolve();}},25);
 });
}

/** Loopback names, plus a caller's own (`<id>.studio.localhost`), and an explicit bind address.
 * With `upgrade`, WebSocket upgrades it accepts are piped straight to their upstream: Bun's
 * node:http does not deliver bytes written to an upgraded socket, so a small TCP front reads
 * each connection's first request head and hands every other connection, byte for byte, to
 * the HTTP server on a private loopback port. A fixed port is always taken by this front, on
 * Bun.listen with SO_REUSEPORT, so a newer version's process can listen on the same port while
 * this one drains: Bun's node:http ignores `reusePort`, and Bun's node:net, which honours it,
 * takes every write whatever the client reads (write() always returns true), so it gives no
 * backpressure and no way to see a client that stopped reading. With `admit`, a connection whose
 * source address it refuses is closed before anything is read. Behind a front the check is made
 * twice on the client's own address: by the front as it accepts, and by the inner server for
 * every request, which looks the address up by the front's end of the loopback hop (`origins`)
 * and refuses a connection with no entry, so a connection made to the inner port directly,
 * bypassing the front, is refused whatever its source. `admit` needs a listener without
 * `upgrade`.
 *
 * `drain()` closes Realtime sockets at once (a socket never finishes on its own) and from then
 * on answers every request with `connection: close`, then closing its connection; kept-alive
 * connections stay open, since a request may already be on its way. It keeps accepting for the
 * grace (ACCEPT_GRACE_MS) and until the event loop has had a free turn (settled, at most
 * SETTLE_MS_MAX), then stops accepting, closes the connections with nothing in flight and
 * settles once every connection has closed; `stop(true)` then closes what is left.
 *
 * A client that stops reading, in normal running and in a drain, is bounded by `stallMs`
 * (FRONT_STALL_MS): while something waits for it in the front, it may take none of it for that
 * long. Once nothing waits in the front, its connection ends as soon as the inner end has: Bun
 * closes an ended socket whose bytes are all with the kernel even if the client never reads them
 * (measured for answers up to 256 KB to a client that does not read). `flushMs` (PIPE_FLUSH_MS)
 * is for the drain only: a quiet connection the drain leaves to the inner server to close is
 * closed by the front ANSWER_CLOSE_MS plus `flushMs` after the grace, since a paused hop does
 * not report the inner end's close. */
export async function serveLocal(fetch:(request:Request)=>Response|Promise<Response>,port=0,
 options:{host?:string;hostnames?:(name:string)=>boolean;upgrade?:Upgrade;isUpgrade?:(path:string)=>boolean;
  admit?:(source:string)=>boolean;requestTimeout?:number;flushMs?:number;stallMs?:number;headMs?:number}={}) {
 const bind=options.host??'127.0.0.1';
 if(options.admit&&options.upgrade)throw new Error('A source check needs a listener without upgrades');
 const fronted=!!options.upgrade||port!==0;
 // Behind a front the HTTP server listens on a private loopback port.
 const admit=options.admit,innerHost=fronted?'127.0.0.1':bind;
 // Behind a front with `admit`: the client's address for each loopback hop the front opened,
 // keyed by the hop's address and port as the inner server sees them.
 const origins=new Map<string,string>();
 const source=(socket:Socket|undefined):string|undefined=>!socket?undefined
  :fronted?origins.get(`${plain(socket.remoteAddress)}:${socket.remotePort}`):socket.remoteAddress;
 const sockets=new Set<Socket>();
 // Requests in flight per connection.
 const busy=new Map<Socket,number>();
 const hopName=(socket:Socket)=>`${plain(socket.remoteAddress)}:${socket.remotePort}`;
 // Behind a front: tells the front that the hop finished an answer (frontListener.answered).
 let answered=(_hop:string)=>{};
 let draining=false;
 const closeAfterAnswer=(socket:Socket)=>{socket.end();setTimeout(()=>socket.destroy(),ANSWER_CLOSE_MS).unref();};
 const server=createServer(async(incoming,outgoing)=>{
  // Checked again per request, in case a runtime delivers a request on a refused connection.
  // Behind a front this is the client's address, and a hop the front did not open has none.
  if(admit){const from=source(incoming.socket as Socket|undefined);if(from===undefined||!admit(from)){incoming.socket?.destroy();return;}}
  const socket=incoming.socket as Socket|undefined;
  if(socket){
   busy.set(socket,(busy.get(socket)??0)+1);
   let settled=false;
   // Bun reports neither the answer's 'finish' nor its 'close' when the client hangs up first,
   // so the connection's own close settles the request too; otherwise it stays in `busy`.
   const settle=()=>{
    if(settled)return;settled=true;socket.off('close',settle);
    if(fronted)answered(hopName(socket));
    const left=(busy.get(socket)??1)-1;
    if(left>0){busy.set(socket,left);return;}
    busy.delete(socket);
    if(draining)closeAfterAnswer(socket);
   };
   outgoing.once('finish',settle);outgoing.once('close',settle);socket.once('close',settle);
  }
  const abort=new AbortController();
  const disconnect=()=>{if(!outgoing.writableEnded)abort.abort();};
  incoming.once('aborted',disconnect);outgoing.once('close',disconnect);
  let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
  try {
   const headers=new Headers();
   for(let i=0;i<incoming.rawHeaders.length;i+=2)headers.append(incoming.rawHeaders[i]!,incoming.rawHeaders[i+1]!);
   const target=new URL(incoming.url??'/',`http://${incoming.headers.host??'127.0.0.1'}`);
   if(!['127.0.0.1','localhost',bind].includes(target.hostname)&&!options.hostnames?.(target.hostname)){
    outgoing.writeHead(403,{'content-type':'text/plain'});outgoing.end('Invalid host');return;
   }
   const method=incoming.method??'GET';
   // HTTP/1.1 frames a request body only with Content-Length or Transfer-Encoding.
   // Attaching the socket stream to every POST gave body-less requests a body, and
   // the key routes, which refuse any body, answered 400 to the console's key
   // issuance over the real listener. Found by the empty-VM first-project check.
   const length=incoming.headers['content-length'];
   const framed=incoming.headers['transfer-encoding']!==undefined||(length!==undefined&&length!=='0');
   const hasBody=!['GET','HEAD'].includes(method)&&framed;
   const request=new Request(target,{method,headers,signal:abort.signal,
    ...(hasBody?{body:requestBody(incoming),duplex:'half'}:{})} as RequestInit);
   const response=await fetch(request);
   if(abort.signal.aborted){void response.body?.cancel().catch(()=>{});return;}
   // A small declared body the handler never read, as when admission refuses a REST call with
   // 429 before reading it, is read and discarded here, as Node's own server does. The
   // connection then stays reusable instead of being closed for every refusal; a client
   // whose pool ignores `connection: close` (Bun's fetch) otherwise races the close.
   if(hasBody&&!incoming.readableEnded&&!request.bodyUsed&&length!==undefined&&Number(length)<=DISCARD_BODY){
    let timer:ReturnType<typeof setTimeout>|undefined;
    await Promise.race([request.arrayBuffer().catch(()=>{}),new Promise(resolve=>{timer=setTimeout(resolve,DISCARD_WAIT_MS);})]);
    clearTimeout(timer);
    if(abort.signal.aborted){void response.body?.cancel().catch(()=>{});return;}
   }
   const output:Record<string,string|string[]>={};
   // A draining listener answers what it has and takes no further request on the connection.
   if(draining){output['connection']='close';outgoing.shouldKeepAlive=false;}
   // An answer before the body was read in full (a refused upload): the rest of the body is
   // still on the connection, so it cannot carry another request. Tell the client, drain what
   // is left for a moment so it reads the answer, then close.
   if(hasBody&&!incoming.readableEnded){
    output['connection']='close';outgoing.shouldKeepAlive=false;
    outgoing.once('finish',()=>{
     incoming.removeAllListeners('data');
     // Once the client has sent everything nothing unread is left, so closing cannot cut off
     // the answer; a client that keeps sending is closed after a second.
     incoming.once('end',()=>incoming.socket?.destroy());incoming.resume();
     // The close must happen now, not only after the drain. Bun's node:http sends
     // `connection: close` yet keeps the connection open and serves further requests on it,
     // and Bun's fetch reuses such a connection from its pool. Left open, a later request
     // landed on it and was cut off mid-flight by the one-second destroy below: a request
     // admitted to a slow upstream died about 150 ms in with no status
     // (docs/engineering/RESOURCE-POLICY.md, section 5.0). Ending it here sends the client
     // end of stream right after the answer, and no later request is served on it.
     incoming.socket?.end();
     setTimeout(()=>incoming.socket?.destroy(),1_000).unref();
    });
   }
   for(const [name,value] of response.headers)if(!HOP_BY_HOP.has(name)&&name!=='set-cookie')output[name]=value;
   const cookies=response.headers.getSetCookie();if(cookies.length)output['set-cookie']=cookies;
   outgoing.writeHead(response.status,output);
   if(!response.body||method==='HEAD'){
    void response.body?.cancel().catch(()=>{});outgoing.end();return;
   }
   reader=response.body.getReader();
   // Deadline/error must break a pending drain wait for a slow downstream.
   void reader.closed.catch(()=>{outgoing.destroy();});
   while(!abort.signal.aborted){
    const part=await reader.read();
    if(part.done){outgoing.end();break;}
    if(!outgoing.write(part.value))await writable(outgoing);
   }
  }catch{
   if(outgoing.headersSent)outgoing.destroy();
   else if(!outgoing.destroyed){outgoing.writeHead(502,{'content-type':'application/json','cache-control':'no-store'});outgoing.end(JSON.stringify({message:'Response unavailable'}));}
  }finally{
   if(reader)void reader.cancel().catch(()=>{});
   incoming.off('aborted',disconnect);
   // Keep close observation until the final bytes are handed off or disconnected.
  }
 });
 server.on('connection',socket=>{
  // Behind a front the hop's entry may not be recorded yet; every request is checked above.
  if(admit&&!fronted&&!admit(socket.remoteAddress??'')){socket.destroy();return;}
  sockets.add(socket);socket.once('close',()=>sockets.delete(socket));
 });
 server.maxConnections=256;server.maxHeadersCount=100;
 server.headersTimeout=10_000;server.requestTimeout=options.requestTimeout??30_000;server.keepAliveTimeout=5_000;
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);
  // Behind a front, the HTTP server itself listens on a private loopback port.
  server.listen(fronted?0:port,innerHost,()=>{server.off('error',reject);resolve();});});
 const address=server.address();if(!address||typeof address==='string')throw new Error('Local listener address unavailable');
 // A kept-alive connection is not closed when the drain starts: a request its client already
 // sent may be waiting, unread, in the connection. Through the grace every request is answered
 // with `connection: close` and its connection then closes. At the end of the grace the
 // connections with nothing in flight and nothing new from their client close, so a quiet
 // client cannot hold the drain open.
 //
 // Behind a front, the front sees every byte, so it closes only a connection with no request in
 // flight (the hops in `busyHops`) and no complete request head received since its last answer
 // (frontListener.close); one whose head is complete stays and is answered. Bun's node:http
 // reports a connection only once a whole request head has arrived, so the inner server could
 // not tell these apart. Without a front (ephemeral ports: the lab and tests) the connections it
 // knows with nothing in flight close at the end of the grace, and the rest are left to stop(true).
 // What remains is a request still on its way when its idle connection closes at the end of the
 // grace: measured on the development host with 200 kept-alive clients pausing up to 600 ms
 // between requests, 2 and then 4 of about 20,600 and 20,900 requests over two series of 10
 // stops got no answer (at most 2 in one stop). Clients sending back to back lost none.
 const busyHops=()=>new Set([...busy.keys()].map(hopName));
 if(!fronted)return {port:address.port,connections:()=>sockets.size,inFlight:()=>busy.size,
  async drain(graceMs=ACCEPT_GRACE_MS){
   draining=true;await pause(graceMs);await settled();server.close();
   for(const socket of sockets)if(!busy.has(socket))socket.destroy();
   await empty(()=>sockets.size);
  },
  stop(force=false){if(force)server.closeAllConnections();server.close();}};
 let front:Awaited<ReturnType<typeof frontListener>>;
 try {
  front=await frontListener(port,bind,innerHost,address.port,options.upgrade,options.isUpgrade??(()=>false),options.flushMs??PIPE_FLUSH_MS,
   name=>['127.0.0.1','localhost',bind].includes(name)||!!options.hostnames?.(name),options.admit,admit?origins:undefined,options.stallMs,options.headMs);
 } catch(error) {
  // The port could not be taken: the inner server, listening already, must not stay behind.
  // Functions Storage and the Studio listener retry a failed bind every 2 s.
  server.closeAllConnections();server.close();
  throw error;
 }
 answered=front.answered;
 return {port:front.port,connections:()=>front.connections(),
  /** The inner server's loopback port, for tests and diagnostics: nothing but the front should reach it. */
  innerPort:address.port,
  /** Client connections tracked by hop in the front (empty once all have closed), for tests and diagnostics. */
  trackedHops:()=>front.trackedHops(),
  /** The hops with a recorded client address (`origins`), for tests and diagnostics. */
  admittedHops:()=>[...origins.keys()],
  /** Connections with a request in flight, for tests and diagnostics. */
  inFlight:()=>busy.size,
  /** Closes the inner server only, so the front's hops cannot connect: a test hook. */
  closeInnerForTest:()=>{server.close();},
  // The inner server keeps listening: a connection the front accepted before the drain still reaches it.
  async drain(graceMs=ACCEPT_GRACE_MS){
   front.retire();draining=true;await pause(graceMs);await settled();
   const kept=front.close(busyHops());
   // A quiet connection that has had an answer closes from this end, after what it was sent:
   // the last answer may still be on its way through the front, which a close there would cut.
   for(const socket of sockets)if(!busy.has(socket)&&!kept.has(hopName(socket)))closeAfterAnswer(socket);
   await empty(()=>front.connections()+sockets.size);
  },
  stop(force=false){front.stop();if(force)server.closeAllConnections();server.close();}};
}

/** The request body as a stream. Cancelling it (a handler refusing an upload) stops reading
 * without destroying the connection, so the handler's answer still reaches the client;
 * Readable.toWeb destroys the request, which the listener took for a client hanging up. */
function requestBody(incoming:import('node:http').IncomingMessage):ReadableStream<Uint8Array> {
 let done=false;
 return new ReadableStream<Uint8Array>({
  start(controller){
   incoming.on('data',(chunk:Buffer)=>{
    if(done)return;
    controller.enqueue(new Uint8Array(chunk.buffer,chunk.byteOffset,chunk.byteLength));
    if((controller.desiredSize??1)<=0)incoming.pause();
   });
   incoming.once('end',()=>{if(!done){done=true;controller.close();}});
   incoming.once('error',error=>{if(!done){done=true;controller.error(error);}});
   incoming.once('aborted',()=>{if(!done){done=true;controller.error(new Error('Request aborted'));}});
  },
  pull(){incoming.resume();},
  cancel(){done=true;incoming.pause();},
 },{highWaterMark:4,size:()=>1});
}

function reply(socket:Client,status:number,message:string) {
 const body=JSON.stringify({message});
 const reason:Record<number,string>={400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found'};
 socket.end(`HTTP/1.1 ${status} ${reason[status]??'Service Unavailable'}\r\ncontent-type: application/json\r\ncache-control: no-store\r\n`+
  `access-control-allow-origin: *\r\ncontent-length: ${Buffer.byteLength(body)}\r\nconnection: close\r\n\r\n${body}`);
}

/** In a drain, how long after the inner server's close (ANSWER_CLOSE_MS) a quiet connection left
 * to it may keep the front's end open (frontListener.close). */
const PIPE_FLUSH_MS=5_000;
/** How long the inner server waits, after ending a connection, before it destroys it (closeAfterAnswer). */
const ANSWER_CLOSE_MS=1_000;
/** How long a client connection may take none of what is waiting for it before it is closed. A
 * client that reads, however slowly, takes some bytes in that time; one that takes none for as
 * long as the inner server allows a request to arrive (requestTimeout, 30 s) is gone or not
 * reading, and its connection must not hold its answer here. */
const FRONT_STALL_MS=30_000;
/** The most client connections one front holds, as its node:net predecessor's maxConnections. */
export const MAX_FRONT_CONNECTIONS=1024;
/** Above this many bytes waiting for a client, the front stops reading from the inner server. */
const CLIENT_HIGH_WATER=256*1024;

type Closable={destroy():void;once(event:'close',listener:()=>void):unknown};
/** Destroys `socket` after `ms` unless it closed by then. */
function closeBy(socket:Closable,ms:number) {
 const timer=setTimeout(()=>socket.destroy(),ms);timer.unref();
 socket.once('close',()=>clearTimeout(timer));
}

/** One client connection of the front, on Bun's own TCP socket. Bun's node:net takes every
 * write into an unbounded buffer of its own and reports neither its size nor its progress, so a
 * client that stops reading would hold its whole answer here unseen. Bun's socket says how much
 * each write handed to the kernel; the rest waits in `queue`, which is what `writableLength`
 * reports, and `progressAt` is the last time the client took any of it. The events and methods
 * are the subset of a node:net Socket that the front uses. */
class Client extends EventEmitter {
 private queue:Buffer[]=[];private queued=0;private ending=false;
 destroyed=false;progressAt=performance.now();
 constructor(private socket:import('bun').Socket<Client>){super();}
 get remoteAddress():string{return this.socket.remoteAddress;}
 get writableLength():number{return this.queued;}
 /** False once more than CLIENT_HIGH_WATER waits; 'drain' follows when it is taken. */
 write(chunk:Buffer|string):boolean {
  if(this.destroyed||this.ending)return false;
  const data=typeof chunk==='string'?Buffer.from(chunk,'latin1'):chunk;
  if(!this.queued){
   // The wait for the client starts now, whatever happened on this connection before.
   const sent=this.socket.write(data);
   this.progressAt=performance.now();
   if(sent<data.length){this.queue.push(data.subarray(Math.max(0,sent)));this.queued+=data.length-Math.max(0,sent);}
  }else{this.queue.push(data);this.queued+=data.length;}
  return this.queued<=CLIENT_HIGH_WATER;
 }
 /** Hands over what waits, as far as the kernel takes it: on Bun's drain, and on each stall check. */
 flush() {
  const before=this.queued;
  while(this.queue.length){
   const head=this.queue[0]!;
   const sent=this.socket.write(head);
   if(sent>0){this.progressAt=performance.now();this.queued-=sent;}
   if(sent<head.length){this.queue[0]=head.subarray(Math.max(0,sent));break;}
   this.queue.shift();
  }
  if(!this.queued&&this.ending)this.socket.end();
  if(before>CLIENT_HIGH_WATER&&this.queued<=CLIENT_HIGH_WATER)this.emit('drain');
 }
 /** Ends after what waits: a FIN behind the last byte, never a reset. */
 end(chunk?:string|Buffer) {
  if(chunk!==undefined)this.write(chunk);
  if(this.ending||this.destroyed)return;
  this.ending=true;
  if(!this.queued)this.socket.end();
 }
 pause(){this.socket.pause();}
 resume(){this.socket.resume();}
 destroy(){if(this.destroyed)return;this.destroyed=true;this.queue=[];this.queued=0;this.socket.terminate();this.closed();}
 /** Bun's close, or our own destroy: 'close' is emitted once. */
 closed(){this.destroyed=true;if(this.listenerCount('close')||!this.reported){this.reported=true;this.emit('close');}}
 private reported=false;
}

/** Forwards between a client and a node:net socket (the inner hop, or a Realtime upstream).
 * What comes from `b` waits in the client's queue, and `b` stops being read while that queue is
 * above CLIENT_HIGH_WATER, so the inner server's own backpressure applies. When `b` closes, the
 * client's end is ended, not reset, after what was already written to it: a client reading late
 * or slowly still gets the whole of an answer whose connection then closes. While anything waits
 * in the front the stall check bounds a client that takes nothing (a paused `b` does not report
 * its end at all). */
function pipe(a:Client,b:Socket) {
 const close=()=>{a.destroy();b.destroy();};
 a.on('data',(chunk:Buffer)=>{b.write(chunk);});
 a.once('end',()=>b.end());
 b.on('data',(chunk:Buffer)=>{if(!a.write(chunk))b.pause();});
 a.on('drain',()=>b.resume());
 b.once('end',()=>a.end());
 // `b` ending ends the client's side (above); a reset of either side closes both.
 a.once('close',()=>b.destroy());
 a.once('error',close);b.once('error',close);
}

/** An address without the IPv4-mapped IPv6 prefix. */
function plain(address:string|undefined):string {return (address??'').replace(/^::ffff:/i,'');}

/** The TCP front on the listener's port, on Bun.listen (with SO_REUSEPORT), so each client's
 * pending bytes are known (Client). Without `upgrade` it passes every connection straight to the
 * inner HTTP server. */
async function frontListener(port:number,bind:string,innerHost:string,inner:number,upgrade:Upgrade|undefined,isUpgrade:(path:string)=>boolean,flushMs:number,
 hostAllowed:(name:string)=>boolean,admit?:(source:string)=>boolean,origins?:Map<string,string>,stallMs=FRONT_STALL_MS,headMs=10_000) {
 const sockets=new Set<Client>(),upgraded=new Set<Client>(),reading=new Set<Client>();
 // Each client connection's loopback hop to the inner server, named as the inner server sees it,
 // and the reverse, so the inner server's answers reach the right connection's state.
 const hops=new WeakMap<Client,string>(),byHop=new Map<string,Client>();
 const hopOf=(socket:Client,http:Socket)=>{
  const hop=`${plain(http.localAddress)}:${http.localPort}`;hops.set(socket,hop);byHop.set(hop,socket);
  http.once('close',()=>{if(byHop.get(hop)===socket)byHop.delete(hop);});
  return hop;
 };
 // Whether a complete request head arrived from the client since its last answer: such a
 // request is waiting to be read and must not be closed at the end of the grace. The last three
 // bytes are kept, so a head split across chunks is still seen.
 const waiting=new WeakMap<Client,{head:boolean;tail:Buffer;answered:boolean}>();
 const track=(socket:Client,head:boolean)=>{
  waiting.set(socket,{head,tail:Buffer.alloc(0),answered:false});
  socket.on('data',(chunk:Buffer)=>{
   const state=waiting.get(socket)!;if(state.head)return;
   const seen=Buffer.concat([state.tail,chunk]);
   state.head=seen.includes('\r\n\r\n');state.tail=seen.subarray(Math.max(0,seen.length-3));
  });
 };
 const accepted=(socket:Client)=>{
  // At most MAX_FRONT_CONNECTIONS at once: further ones close as they are accepted.
  if(sockets.size>=MAX_FRONT_CONNECTIONS||admit&&!admit(socket.remoteAddress??'')){socket.destroy();return;}
  sockets.add(socket);socket.once('close',()=>{sockets.delete(socket);upgraded.delete(socket);reading.delete(socket);});
  if(!upgrade){
   // What arrives before the inner connection is up is kept and written first.
   track(socket,false);
   const early:Buffer[]=[];const keep=(chunk:Buffer)=>{early.push(chunk);};
   socket.on('data',keep);
   const http=connect(inner,innerHost,()=>{
    // Recorded before the first byte reaches the inner server, which checks it per request.
    const hop=hopOf(socket,http);if(origins)origins.set(hop,socket.remoteAddress??'');
    http.once('close',()=>origins?.delete(hop));
    socket.off('data',keep);for(const chunk of early)http.write(chunk);
    pipe(socket,http);
   });
   http.once('error',()=>socket.destroy());socket.once('error',()=>http.destroy());socket.once('close',()=>http.destroy());
   return;
  }
  reading.add(socket);
  let buffer=Buffer.alloc(0);
  // A connection that has not sent its request head within `headMs` (10 s) closes.
  const timer=setTimeout(()=>socket.destroy(),headMs);
  socket.once('close',()=>clearTimeout(timer));
  const read=async(chunk:Buffer)=>{
   buffer=Buffer.concat([buffer,chunk]);
   const end=buffer.indexOf('\r\n\r\n');
   if(end<0){if(buffer.length>MAX_HEAD){clearTimeout(timer);socket.off('data',read);reply(socket,400,'Request head too large');}return;}
   socket.off('data',read);socket.pause();clearTimeout(timer);reading.delete(socket);
   const lines=buffer.subarray(0,end).toString('latin1').split('\r\n');
   const [method,target]=(lines[0]??'').split(' ');
   const headers=new Headers();
   for(const line of lines.slice(1)){const at=line.indexOf(':');if(at>0)try{headers.append(line.slice(0,at).trim(),line.slice(at+1).trim());}catch{}}
   const socketUpgrade=method==='GET'&&headers.get('upgrade')?.toLowerCase()==='websocket'&&!!target&&isUpgrade(target);
   if(!socketUpgrade){
    // The head just read is a complete request, waiting for its answer.
    track(socket,true);
    const http=connect(inner,innerHost,()=>{hopOf(socket,http);http.write(buffer);pipe(socket,http);socket.resume();});
    http.once('error',()=>socket.destroy());socket.once('close',()=>http.destroy());
    return;
   }
   const hostname=(headers.get('host')??'').replace(/:\d+$/,'');
   if(!hostAllowed(hostname)){reply(socket,403,'Invalid host');return;}
   let decision:UpgradeDecision;
   try{decision=await upgrade(target!,headers);}catch{decision={ok:false,status:503,message:'Realtime unavailable'};}
   if(!decision.ok){reply(socket,decision.status,decision.message);return;}
   if(draining){reply(socket,503,'Sbarbase is restarting; try again shortly');return;}
   if(socket.destroyed)return;
   upgraded.add(socket);
   const head=decision.head;
   const upstream=connect(decision.port,decision.host,()=>{
    upstream.write(head);
    if(decision.onOpen){const release=decision.onOpen();if(socket.destroyed)release();else socket.once('close',release);}
    const rest=buffer.subarray(end+4);if(rest.length)upstream.write(rest);
    pipe(socket,upstream);socket.resume();
   });
   upstream.once('error',()=>{if(!socket.destroyed)reply(socket,503,'Realtime unavailable');});
   socket.once('close',()=>upstream.destroy());
  };
  socket.on('data',read);
  socket.once('error',()=>socket.destroy());
 };
 let draining=false;
 // `reusePort` is honoured by Bun.listen on Linux though its types do not list it yet.
 const listenOptions={hostname:bind,port,reusePort:true,socket:{
  open(raw){const client=new Client(raw);raw.data=client;accepted(client);},
  data(raw,chunk){raw.data?.emit('data',Buffer.from(chunk));},
  drain(raw){raw.data?.flush();},
  end(raw){raw.data?.emit('end');},
  close(raw){raw.data?.closed();},
  error(raw,error){raw.data?.emit('error',error);},
 } satisfies import('bun').SocketHandler<Client>};
 const listener=Bun.listen<Client>(listenOptions);
 // A client that takes none of what waits for it within `stallMs` is closed, with its hop.
 // Bun reports drain only once much of the kernel's buffer is free (every 750 ms for a client
 // reading about 1 MB/s, measured), so each check also offers the client more: the kernel takes
 // some as soon as the client read any, which is the progress that counts.
 const stalls=setInterval(()=>{
  const now=performance.now();
  for(const socket of sockets){
   if(!socket.writableLength)continue;
   socket.flush();
   if(socket.writableLength>0&&now-socket.progressAt>stallMs)socket.destroy();
  }
 },Math.max(10,Math.min(1_000,stallMs/4)));
 stalls.unref();
 return {port:listener.port,connections:()=>sockets.size,
  /** Connections tracked by hop, for tests and diagnostics: empty once all have closed. */
  trackedHops:()=>byHop.size,
  // Closes Realtime sockets and refuses new ones; plain connections close once the inner server is done with them.
  retire(){draining=true;for(const socket of upgraded)socket.destroy();},
  /** The inner server finished an answer on this hop: what the client sends next is new. */
  answered(hop:string){const state=waiting.get(byHop.get(hop)!);if(state){state.head=false;state.tail=Buffer.alloc(0);state.answered=true;}},
  /** Stops accepting and returns the hops to keep: those with a request in flight (`busy`) or a
   * complete request head received since their last answer. Every other connection had the
   * grace and closes: here when it has had no answer (it never sent a request, or its head is
   * still arriving), and from the inner server's end otherwise, so its last answer, which may
   * still be passing through here, reaches the client first. */
  close(busy:Set<string>):Set<string>{
   listener.stop(false);
   const kept=new Set<string>();
   for(const socket of sockets){
    const hop=hops.get(socket),state=waiting.get(socket);
    if(!reading.has(socket)&&(hop&&busy.has(hop)||state?.head)){if(hop)kept.add(hop);}
    else if(reading.has(socket)||!state?.answered)socket.destroy();
    // Left to the inner server, which ends the hop. A client that does not read keeps the hop
    // paused, and a paused hop does not report that end here, so the same flush bound applies.
    else closeBy(socket,ANSWER_CLOSE_MS+flushMs);
   }
   return kept;
  },
  stop(){clearInterval(stalls);for(const socket of [...sockets])socket.destroy();listener.stop(true);}};
}
