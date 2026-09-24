import {createServer,type ServerResponse} from 'node:http';
import {Readable} from 'node:stream';
import {connect,createServer as createTcpServer,type Socket,type Server as TcpServer} from 'node:net';

/** What the listener does with a WebSocket upgrade: connect it to host:port and send `head`,
 * or answer it with a status. */
export type UpgradeDecision={ok:true;host:string;port:number;head:string}|{ok:false;status:number;message:string};
export type Upgrade=(path:string,headers:Headers)=>UpgradeDecision|Promise<UpgradeDecision>;
const MAX_HEAD=16384;

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

/** Loopback names, plus a caller's own (`<id>.studio.localhost`), and an explicit bind address.
 * With `upgrade`, WebSocket upgrades it accepts are piped straight to their upstream: Bun's
 * node:http does not deliver bytes written to an upgraded socket, so a small TCP front reads
 * each connection's first request head and hands every other connection, byte for byte, to
 * the HTTP server on a private loopback port. */
export async function serveLocal(fetch:(request:Request)=>Response|Promise<Response>,port=0,
 options:{host?:string;hostnames?:(name:string)=>boolean;upgrade?:Upgrade;isUpgrade?:(path:string)=>boolean}={}) {
 const bind=options.host??'127.0.0.1';
 const sockets=new Set<Socket>();
 const server=createServer(async(incoming,outgoing)=>{
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
   const request=new Request(target,{method,headers,signal:abort.signal,
    ...(!['GET','HEAD'].includes(method)&&framed?{body:Readable.toWeb(incoming) as unknown as ReadableStream<Uint8Array>,duplex:'half'}:{})} as RequestInit);
   const response=await fetch(request);
   if(abort.signal.aborted){void response.body?.cancel().catch(()=>{});return;}
   const output:Record<string,string|string[]>={};
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
 server.on('connection',socket=>{sockets.add(socket);socket.once('close',()=>sockets.delete(socket));});
 server.maxConnections=256;server.maxHeadersCount=100;
 server.headersTimeout=10_000;server.requestTimeout=30_000;server.keepAliveTimeout=5_000;
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);
  // Behind a front, the HTTP server itself listens on a private loopback port.
  server.listen(options.upgrade?0:port,options.upgrade?'127.0.0.1':bind,()=>{server.off('error',reject);resolve();});});
 const address=server.address();if(!address||typeof address==='string')throw new Error('Local listener address unavailable');
 if(!options.upgrade)return {port:address.port,connections:()=>sockets.size,stop(force=false){if(force)server.closeAllConnections();server.close();}};
 const front=await frontListener(port,bind,address.port,options.upgrade,options.isUpgrade??(()=>false),
  name=>['127.0.0.1','localhost',bind].includes(name)||!!options.hostnames?.(name));
 return {port:front.port,connections:()=>front.connections(),stop(force=false){
  front.stop();if(force)server.closeAllConnections();server.close();}};
}

function reply(socket:Socket,status:number,message:string) {
 const body=JSON.stringify({message});
 const reason:Record<number,string>={400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found'};
 socket.end(`HTTP/1.1 ${status} ${reason[status]??'Service Unavailable'}\r\ncontent-type: application/json\r\ncache-control: no-store\r\n`+
  `access-control-allow-origin: *\r\ncontent-length: ${Buffer.byteLength(body)}\r\nconnection: close\r\n\r\n${body}`);
}

function pipe(a:Socket,b:Socket) {
 a.pipe(b);b.pipe(a);
 const close=()=>{a.destroy();b.destroy();};
 a.once('close',close);b.once('close',close);a.once('error',close);b.once('error',close);
}

async function frontListener(port:number,bind:string,inner:number,upgrade:Upgrade,isUpgrade:(path:string)=>boolean,
 hostAllowed:(name:string)=>boolean) {
 const sockets=new Set<Socket>();
 const server:TcpServer=createTcpServer(socket=>{
  sockets.add(socket);socket.once('close',()=>sockets.delete(socket));
  let buffer=Buffer.alloc(0);
  const timer=setTimeout(()=>socket.destroy(),10_000);
  const read=async(chunk:Buffer)=>{
   buffer=Buffer.concat([buffer,chunk]);
   const end=buffer.indexOf('\r\n\r\n');
   if(end<0){if(buffer.length>MAX_HEAD){clearTimeout(timer);socket.off('data',read);reply(socket,400,'Request head too large');}return;}
   socket.off('data',read);socket.pause();clearTimeout(timer);
   const lines=buffer.subarray(0,end).toString('latin1').split('\r\n');
   const [method,target]=(lines[0]??'').split(' ');
   const headers=new Headers();
   for(const line of lines.slice(1)){const at=line.indexOf(':');if(at>0)try{headers.append(line.slice(0,at).trim(),line.slice(at+1).trim());}catch{}}
   const socketUpgrade=method==='GET'&&headers.get('upgrade')?.toLowerCase()==='websocket'&&!!target&&isUpgrade(target);
   if(!socketUpgrade){
    const http=connect(inner,'127.0.0.1',()=>{http.write(buffer);pipe(socket,http);socket.resume();});
    http.once('error',()=>socket.destroy());
    return;
   }
   const hostname=(headers.get('host')??'').replace(/:\d+$/,'');
   if(!hostAllowed(hostname)){reply(socket,403,'Invalid host');return;}
   let decision:UpgradeDecision;
   try{decision=await upgrade(target!,headers);}catch{decision={ok:false,status:503,message:'Realtime unavailable'};}
   if(!decision.ok){reply(socket,decision.status,decision.message);return;}
   const head=decision.head;
   const upstream=connect(decision.port,decision.host,()=>{
    upstream.write(head);
    const rest=buffer.subarray(end+4);if(rest.length)upstream.write(rest);
    pipe(socket,upstream);socket.resume();
   });
   upstream.once('error',()=>{if(!socket.destroyed)reply(socket,503,'Realtime unavailable');});
  };
  socket.on('data',read);
  socket.once('error',()=>socket.destroy());
 });
 server.maxConnections=1024;
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(port,bind,()=>{server.off('error',reject);resolve();});});
 const address=server.address();if(!address||typeof address==='string')throw new Error('Local listener address unavailable');
 return {port:address.port,connections:()=>sockets.size,stop(){for(const socket of sockets)socket.destroy();server.close();}};
}
