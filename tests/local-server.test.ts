import {describe,expect,test} from 'bun:test';
import {consolePort,serveLocal} from '../src/http/local-server';
import {connect} from 'node:net';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import {ConcurrencyGate} from '../src/gateway/concurrency';

describe('console port',()=>{
 test('an unset port stays ephemeral, as the lab expects',()=>{
  expect(consolePort(undefined)).toBe(0);
  expect(consolePort('')).toBe(0);
 });
 test('a server pins a valid unprivileged port',()=>{
  expect(consolePort('8787')).toBe(8787);
 });
 test('anything else refuses instead of binding somewhere unexpected',()=>{
  for(const value of ['80','0','65536','87 87','8787x','-1','0x2000'])expect(()=>consolePort(value)).toThrow('SBARBASE_CONSOLE_PORT');
 });
 test('the listener binds the pinned port on loopback',async()=>{
  const probe=await serveLocal(()=>new Response('ok'));const free=probe.port;probe.stop(true);
  const server=await serveLocal(()=>new Response('ok'),free);
  try {
   expect(server.port).toBe(free);
   expect(await (await fetch(`http://127.0.0.1:${free}/`)).text()).toBe('ok');
  } finally {server.stop(true);}
 });
});

describe('request bodies over the real listener',()=>{
 test('a POST without a body reaches the handler without one',async()=>{
  const seen:(boolean|string)[]=[];
  const server=await serveLocal(async request=>{seen.push(request.body===null?true:await request.text());return new Response('ok');});
  try {
   await fetch(`http://127.0.0.1:${server.port}/keys`,{method:'POST'});
   await fetch(`http://127.0.0.1:${server.port}/keys/1`,{method:'DELETE'});
   await fetch(`http://127.0.0.1:${server.port}/projects`,{method:'POST',body:'{"name":"x"}'});
   expect(seen).toEqual([true,true,'{"name":"x"}']);
  } finally {server.stop(true);}
 });
});
test('a refused upload answers with its own status and does not hold up the next request',async()=>{
 // A handler that refuses without reading the body (declared too large), and one that stops
 // reading part way (the gateway's upload limit). Neither may turn into a 200, and the unread
 // rest of the body must not stall the client's next request on the same connection.
 for(const cancel of [false,true]) {
  const server=await serveLocal(async request=>{
   if(request.method==='POST'){if(cancel)void request.body?.cancel();return new Response('too large',{status:413});}
   return new Response('ok');
  },0);
  try {
   const first=await fetch(`http://127.0.0.1:${server.port}/upload`,{method:'POST',body:new Uint8Array(8*1024*1024),signal:AbortSignal.timeout(8000)});
   expect([first.status,await first.text(),first.headers.get('connection')]).toEqual([413,'too large','close']);
   const started=Date.now();
   const next=await fetch(`http://127.0.0.1:${server.port}/list`,{signal:AbortSignal.timeout(8000)});
   expect(await next.text()).toBe('ok');
   expect(Date.now()-started).toBeLessThan(3000);
  } finally {server.stop(true);}
 }
},30000);

// Regression for the sustained arrival failure in docs/engineering/RESOURCE-POLICY.md 5.0: a
// refused REST call was answered with `connection: close`, but Bun's node:http kept the
// connection open and served later requests on it, Bun's fetch reused it, and the listener's
// one-second destroy then cut off whatever long request had landed on it.
describe('connections after an early answer',()=>{
 const post=(path:string,body:string)=>`POST ${path} HTTP/1.1\r\nhost: 127.0.0.1\r\ncontent-type: application/json\r\ncontent-length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;
 const statuses=(raw:string)=>(raw.match(/HTTP\/1\.1 \d{3}/g)??[]).map(line=>Number(line.slice(9)));
 const open=async(port:number)=>{
  const socket=connect(port,'127.0.0.1');
  const state={raw:'',closedAt:0};
  socket.on('data',chunk=>{state.raw+=chunk;});socket.on('close',()=>{state.closedAt=Date.now();});socket.on('error',()=>{});
  await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
  return {socket,state};
 };
 const until=async(done:()=>boolean,ms:number)=>{const deadline=Date.now()+ms;while(!done()&&Date.now()<deadline)await Bun.sleep(10);};

 // The real server listens behind the WebSocket front (lab/upstream-server.ts), so both paths are covered.
 const listeners=[['plain',{}],['behind the WebSocket front',{upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false}]] as const;
 for(const [name,options] of listeners){
 test(`${name}: a slow request reusing the connection of a refused one is answered, not cut off`,async()=>{
  const server=await serveLocal(async request=>{
   // Admission refuses without reading the body; an admitted call then takes longer than a second.
   if(new URL(request.url).pathname==='/refused')return Response.json({message:'busy'},{status:429,headers:{'retry-after':'1'}});
   await request.text();await Bun.sleep(1_500);return Response.json(1);
  },0,options);
  const {socket,state}=await open(server.port);
  try {
   socket.write(post('/refused','{}'));
   await until(()=>statuses(state.raw).length>0||!!state.closedAt,2_000);
   expect(statuses(state.raw)).toEqual([429]);
   // A pooled client reuses the connection, as Bun's fetch does.
   socket.write(post('/slow','{}'));
   await until(()=>statuses(state.raw).length>1||!!state.closedAt,4_000);
   expect(statuses(state.raw)).toEqual([429,200]);
   // A small refused body is read and discarded, so the connection stays reusable.
   expect(state.raw.toLowerCase()).not.toContain('connection: close');
  } finally {socket.destroy();server.stop(true);}
 },10_000);

 test(`${name}: a large unread body closes the connection right after the answer, with the answer intact`,async()=>{
  const answer='too large '.repeat(20_000);
  const server=await serveLocal(()=>new Response(answer,{status:413}),0,options);
  const {socket,state}=await open(server.port);
  const started=Date.now();
  try {
   socket.write(post('/upload','x'.repeat(128*1024)));
   await until(()=>!!state.closedAt,3_000);
   expect(statuses(state.raw)).toEqual([413]);
   expect(state.raw.toLowerCase()).toContain('connection: close');
   // The whole answer arrived: all of it, then the final chunk, before the close.
   expect(state.raw.length).toBeGreaterThan(answer.length);
   expect(state.raw.endsWith('0\r\n\r\n')).toBe(true);
   // Closed at once, not left open to serve another request until the one-second backstop.
   expect(state.closedAt-started).toBeLessThan(500);
  } finally {socket.destroy();server.stop(true);}
 },10_000);
 }

 test('sustained arrivals at a full REST budget get 200 or 429, never a dropped connection',async()=>{
  // The shape of `lab/gateway-overload-check.ts --sustained`, shortened: a REST budget of three,
  // two-second upstream calls, twenty arrivals a second through the real listener and Bun's fetch.
  const upstream=Bun.serve({port:0,hostname:'127.0.0.1',async fetch(request){await request.text();await Bun.sleep(2_000);return Response.json(1);}});
  const route:EnvironmentRoute={auth:`http://127.0.0.1:${upstream.port}`,rest:`http://127.0.0.1:${upstream.port}`,
   keys:['key'],anonymousToken:'anon',enabled:true,serviceConcurrency:{rest:3}};
  const server=await serveLocal(createGateway(new Map([['target',route]]),fetch,undefined,10_000,new ConcurrencyGate()));
  const outcomes:(number|string)[]=[];const pending:Promise<void>[]=[];
  try {
   const start=performance.now();
   for(let tick=0;tick<100;tick++){
    await Bun.sleep(Math.max(0,start+tick*50-performance.now()));
    pending.push(fetch(`http://127.0.0.1:${server.port}/target/rest/v1/rpc/slow`,{method:'POST',
     headers:{apikey:'key','content-type':'application/json'},body:'{}',signal:AbortSignal.timeout(8_000)})
     .then(async response=>{await response.text();outcomes.push(response.status);},error=>{outcomes.push(String(error));}));
   }
   await Promise.all(pending);
   expect(outcomes.filter(outcome=>outcome!==200&&outcome!==429)).toEqual([]);
   // Three slots, refilled as each two-second batch ends: at least two full batches succeed.
   expect(outcomes.filter(outcome=>outcome===200).length).toBeGreaterThanOrEqual(6);
  } finally {server.stop(true);upstream.stop(true);}
 },20_000);
});
