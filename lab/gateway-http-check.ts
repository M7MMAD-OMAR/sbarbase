import {connect} from 'node:net';
import {serveLocal} from '../src/http/local-server';
import {createGateway} from '../src/gateway/handler';
import {ConcurrencyGate} from '../src/gateway/concurrency';
const checks:string[]=[];
function check(name:string,ok:boolean){if(!ok)throw new Error(name);checks.push(name);}
async function until(predicate:()=>boolean){const end=Date.now()+3000;while(!predicate()&&Date.now()<end)await Bun.sleep(10);check('expected transport state observed',predicate());}
let count=0,open=false,release!:()=>void;
const hold=new Promise<void>(resolve=>{release=resolve;});
const streams:AbortController[]=[];
const upstream=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(request){
 if(new URL(request.url).pathname==='/backpressure')return new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(8*1024*1024));}}),{headers:{'content-length':String(16*1024*1024)}});
 if(new URL(request.url).pathname==='/gzip'){const bytes=Bun.gzipSync('compressed payload');return new Response(bytes,{headers:{'content-encoding':'gzip','content-length':String(bytes.length)}});}
 if(new URL(request.url).pathname==='/stream')return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('first'));}}));
 count++;if(!open)await hold;return Response.json({source:'a'});
}});
const neighbor=Bun.serve({hostname:'127.0.0.1',port:0,fetch:()=>Response.json({source:'b'})});
const route=(port:number)=>({auth:`http://127.0.0.1:${port}`,rest:`http://127.0.0.1:${port}`,keys:['key'],anonymousToken:'anon',enabled:true});
const proxy=await serveLocal(createGateway(new Map([['first',route(upstream.port!)],['second',route(neighbor.port!)]])));
const url=(env='first',path='/')=>`http://127.0.0.1:${proxy.port}/${env}/rest/v1${path}`;
const headers={apikey:'key'};
let pending:Promise<Response>[]=[];
try{
 pending=Array.from({length:8},()=>fetch(url(),{headers}));
 await until(()=>count===8);
 const denied=await fetch(url(),{headers});check('ninth HTTP request receives 429 and Retry-After',denied.status===429&&denied.headers.get('retry-after')==='1');await denied.text();
 check('HTTP rejection never reaches controlled upstream',count===8);
 const other=await fetch(url('second'),{headers});check('neighbor HTTP response stays correct during saturation',other.ok&&(await other.json()).source==='b');
 open=true;release();const responses=await Promise.all(pending);
 check('admitted HTTP responses preserve bytes',(await Promise.all(responses.map(r=>r.json()))).every(value=>value.source==='a'));
 const recovered=await fetch(url(),{headers});check('HTTP route recovers after drain',recovered.ok&&(await recovered.json()).source==='a');
 const compressed=await fetch(url('first','/gzip'),{headers,signal:AbortSignal.timeout(3000)});
 check('compressed upstream payload arrives intact',compressed.ok&&await compressed.text()==='compressed payload');
 for(let i=0;i<8;i++){
  const controller=new AbortController();streams.push(controller);
  const response=await fetch(url('first','/stream'),{headers,signal:controller.signal});
  const reader=response.body!.getReader();void reader.closed.catch(()=>{});
  const chunk=await reader.read();check('stream headers and first bytes delivered',new TextDecoder().decode(chunk.value)==='first');
 }
 const full=await fetch(url(),{headers});check('active HTTP response streams retain all tenant slots',full.status===429);await full.text();
 streams[0]!.abort();
 let freed=false;
 const end=Date.now()+3000;
 while(Date.now()<end){const response=await fetch(url(),{headers});if(response.ok){freed=(await response.json()).source==='a';break;}await response.text();await Bun.sleep(20);}
 check('HTTP disconnect releases streaming slot',freed);
 const timed=await serveLocal(createGateway(new Map([['deadline',route(upstream.port!)]]),fetch,undefined,1000,new ConcurrencyGate(1,1,30)));
 try{
  let failed=false;
  try{const response=await fetch(`http://127.0.0.1:${timed.port}/deadline/rest/v1/stream`,{headers});await response.text();}catch{failed=true;}
  check('connected client detects response deadline as failure',failed);
  const next=await fetch(`http://127.0.0.1:${timed.port}/deadline/rest/v1/`,{headers});
  check('deadline releases slot for next HTTP request',next.ok&&(await next.json()).source==='a');
 }finally{timed.stop(true);}
 const slow=await serveLocal(createGateway(new Map([['slow',route(upstream.port!)]]),fetch,undefined,1000,new ConcurrencyGate(1,1,200)));
 const socket=connect(slow.port,'127.0.0.1');socket.on('error',()=>{});socket.pause();
 try{
  await new Promise<void>(resolve=>socket.once('connect',resolve));
  socket.write('GET /slow/rest/v1/backpressure HTTP/1.1\r\nHost: 127.0.0.1\r\napikey: key\r\n\r\n');
  await until(()=>slow.connections()===1);
  check('paused downstream socket registered',slow.connections()===1);
  const end=Date.now()+3000;let count=1;
  while(count&&Date.now()<end){await Bun.sleep(20);count=await slow.connections();}
  check('deadline destroys socket even while downstream does not read',count===0);
 }finally{socket.destroy();slow.stop(true);}
}finally{
 open=true;release();for(const stream of streams)stream.abort();await Promise.allSettled(pending);
 proxy.stop(true);upstream.stop(true);neighbor.stop(true);
}
await Bun.write('docs/evidence/gateway-http-checks.json',JSON.stringify({scope:'Real loopback HTTP and native fetch with controlled Bun upstreams, not Supabase or PostgreSQL. Tenant rejection, neighboring route, drain and disconnect tested. No Docker services started.',checks,count:checks.length},null,2)+'\n');
console.log(`${checks.length} live HTTP lifecycle checks passed.`);
