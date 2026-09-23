import {createServer,type ServerResponse} from 'node:http';
import {Readable} from 'node:stream';
import type {Socket} from 'node:net';

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

export async function serveLocal(fetch:(request:Request)=>Response|Promise<Response>,port=0) {
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
   if(!['127.0.0.1','localhost'].includes(target.hostname)){
    outgoing.writeHead(403,{'content-type':'text/plain'});outgoing.end('Invalid host');return;
   }
   const method=incoming.method??'GET';
   const request=new Request(target,{method,headers,signal:abort.signal,
    ...(!['GET','HEAD'].includes(method)?{body:Readable.toWeb(incoming) as unknown as ReadableStream<Uint8Array>,duplex:'half'}:{})} as RequestInit);
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
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',()=>{server.off('error',reject);resolve();});});
 const address=server.address();if(!address||typeof address==='string')throw new Error('Local listener address unavailable');
 return {port:address.port,connections:()=>sockets.size,stop(force=false){if(force)server.closeAllConnections();server.close();}};
}
