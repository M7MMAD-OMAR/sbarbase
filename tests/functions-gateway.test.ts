import {test,expect} from 'bun:test';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {RequestLog,observedGateway} from '../src/gateway/observe';
import {FUNCTION_CONCURRENCY,FUNCTION_DEADLINE_MS,GATEWAY} from '../src/gateway/shares';

const base:EnvironmentRoute={auth:'http://auth',rest:'http://rest',functions:{url:'http://functions'},keys:['key'],anonymousToken:'anon',enabled:true};
type Held={url:string;finish:(response:Response)=>void;signal?:AbortSignal};

/** A gateway whose upstream answers only when the test says so, counted like the application does. */
function setup(serviceConcurrency?:EnvironmentRoute['serviceConcurrency'],gate=new ConcurrencyGate()) {
 const held:Held[]=[],log=new RequestLog();
 const transport=((url:unknown,init:RequestInit)=>new Promise<Response>((resolve,reject)=>{
  const entry:Held={url:String(url),finish:resolve,signal:init.signal??undefined};held.push(entry);
  init.signal?.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});
 })) as typeof fetch;
 const registry=new Map([['one',{...base,serviceConcurrency}],['two',{...base,serviceConcurrency}]]);
 const handler=observedGateway(createGateway(registry,transport,undefined,1000,gate),log,runtime=>registry.has(runtime));
 const call=(environment:string,path:string,signal?:AbortSignal,apikey='key')=>
  handler(new Request(`http://local/${environment}${path}`,{headers:{apikey},signal}));
 return {held,log,call};
}
const done=(status=200)=>new Response(null,{status});
const fn=(name='hello',query='')=>`/functions/v1/${name}${query}`;

test('function calls are counted like the other services, errors and latency included',async()=>{
 const {held,log,call}=setup();
 const results=[call('one',fn()),call('one',fn('broken')),call('one',fn('missing')),call('one','/rest/v1/todos')];
 await Bun.sleep(0);
 held[0]!.finish(done(200));held[1]!.finish(done(500));held[2]!.finish(done(404));held[3]!.finish(done(200));
 await Promise.all(results);
 const {window}=log.metrics('one');
 expect(window).toMatchObject({requests:4,clientErrors:1,serverErrors:1,services:{functions:3,rest:1}});
 expect(window.p50).not.toBeNull();
 const rows=log.recent('one',{service:'functions'});
 expect(rows.map(row=>[row.method,row.service,row.path,row.status]).sort()).toEqual(
  [['GET','functions','/broken',500],['GET','functions','/hello',200],['GET','functions','/missing',404]]);
 expect(log.recent('one',{errors:true,service:'functions'})).toHaveLength(2);
});

test('a function call keeps no query string, header or key in the log',async()=>{
 const {held,log,call}=setup();
 const pending=call('one',fn('hello/deep/path','?token=secret-token&apikey=sb_secret_x'));
 await Bun.sleep(0);
 held[0]!.finish(done());await pending;
 const rows=log.recent('one');
 expect(rows).toHaveLength(1);
 expect(rows[0]).toMatchObject({service:'functions',path:'/hello/deep/path'});
 const text=JSON.stringify([rows,log.metrics('one')]);
 for(const secret of ['secret-token','sb_secret_x','apikey','key','authorization'])expect(text).not.toContain(secret);
 expect(Object.keys(rows[0]!).sort()).toEqual(['at','method','ms','path','service','status']);
});

test('the default cap admits that many calls at once, refuses the next one and counts it',async()=>{
 const {held,log,call}=setup();
 const calls=Array.from({length:FUNCTION_CONCURRENCY},()=>call('one',fn()));
 await Bun.sleep(0);
 expect(held).toHaveLength(FUNCTION_CONCURRENCY);
 const refused=await call('one',fn());
 expect(refused.status).toBe(429);
 expect(refused.headers.get('retry-after')).toBe('1');
 expect(await refused.json()).toEqual({message:'Request capacity unavailable. Retry later.'});
 expect(held).toHaveLength(FUNCTION_CONCURRENCY);
 // The refusal reached Usage as a 4xx function call, though it never reached the runtime.
 expect(log.metrics('one').window).toMatchObject({requests:1,clientErrors:1,services:{functions:1}});
 for(const entry of held)entry.finish(done());
 await Promise.all(calls);
 expect(log.metrics('one').window.services.functions).toBe(FUNCTION_CONCURRENCY+1);
});

test('a finished call frees its slot, a route can set its own cap',async()=>{
 const {held,call}=setup({functions:1});
 const first=call('one',fn());await Bun.sleep(0);
 expect((await call('one',fn())).status).toBe(429);
 held[0]!.finish(done());expect((await first).status).toBe(200);
 const second=call('one',fn());await Bun.sleep(0);
 expect(held).toHaveLength(2);
 held[1]!.finish(done());expect((await second).status).toBe(200);
});

test('a client that goes away frees the slot, before and after the response starts',async()=>{
 const {held,call}=setup({functions:1});
 const early=new AbortController(),before=call('one',fn(),early.signal);
 await Bun.sleep(0);
 expect((await call('one',fn())).status).toBe(429);
 early.abort();
 expect((await before).status).toBe(408);
 // Slot free again: this one is admitted, answers with a streamed body, then the client cancels.
 const late=new AbortController(),streamed=call('one',fn(),late.signal);
 await Bun.sleep(0);
 expect(held).toHaveLength(2);
 held[1]!.finish(new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array([1]));}})));
 const response=await streamed;
 expect((await call('one',fn())).status).toBe(429);
 await response.body!.cancel();
 const after=call('one',fn());await Bun.sleep(0);
 expect(held).toHaveLength(3);
 held[2]!.finish(done());expect((await after).status).toBe(200);
});

test('a client abort after the response started frees the slot without a body cancel',async()=>{
 const {held,call}=setup({functions:1});
 const late=new AbortController(),streamed=call('one',fn(),late.signal);
 await Bun.sleep(0);
 held[0]!.finish(new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array([1]));}})));
 const response=await streamed;
 expect((await call('one',fn())).status).toBe(429);
 late.abort();await Bun.sleep(0);
 const after=call('one',fn());await Bun.sleep(0);
 expect(held).toHaveLength(2);
 held[1]!.finish(done());expect((await after).status).toBe(200);
 void response.body?.cancel().catch(()=>{});
});

test('a cancelled function call is counted as a client error',async()=>{
 const {held,log,call}=setup();
 const client=new AbortController(),pending=call('one',fn(),client.signal);
 await Bun.sleep(0);client.abort();
 expect((await pending).status).toBe(408);
 expect(held[0]!.signal?.aborted).toBe(true);
 expect(log.recent('one')).toMatchObject([{service:'functions',path:'/hello',status:408}]);
 expect(log.metrics('one').window).toMatchObject({requests:1,clientErrors:1,serverErrors:0,services:{functions:1}});
});

test('an invalid key is refused before admission and never takes a function slot',async()=>{
 const {held,log,call}=setup({functions:1});
 const bad=()=>call('one',fn(),undefined,'wrong-key');
 const holder=call('one',fn());await Bun.sleep(0);
 // The cap is full: a wrong key still hears 401, not the capacity refusal, and reaches no runtime.
 const refused=await bad();
 expect(refused.status).toBe(401);
 expect(held).toHaveLength(1);
 held[0]!.finish(done());await holder;
 for(let i=0;i<3;i++)expect((await bad()).status).toBe(401);
 const good=call('one',fn());await Bun.sleep(0);
 expect(held).toHaveLength(2);
 held[1]!.finish(done());expect((await good).status).toBe(200);
 expect(log.metrics('one').window.clientErrors).toBe(4);
});

test('saturated functions leave REST and Auth of the same environment working',async()=>{
 const {held,log,call}=setup({functions:2});
 const slow=[call('one',fn()),call('one',fn())];await Bun.sleep(0);
 expect((await call('one',fn())).status).toBe(429);
 const others=[call('one','/rest/v1/todos'),call('one','/auth/v1/settings')];await Bun.sleep(0);
 expect(held.map(entry=>entry.url)).toEqual(['http://functions/hello','http://functions/hello','http://rest/todos','http://auth/settings']);
 held[2]!.finish(done(200));held[3]!.finish(done(200));
 expect((await Promise.all(others)).map(response=>response.status)).toEqual([200,200]);
 for(const entry of held.slice(0,2))entry.finish(done());
 await Promise.all(slow);
 expect(log.metrics('one').window.services).toEqual({functions:3,rest:1,auth:1});
});

test('each environment has its own function cap',async()=>{
 const {held,call}=setup({functions:1});
 const first=call('one',fn());await Bun.sleep(0);
 expect((await call('one',fn())).status).toBe(429);
 const other=call('two',fn());await Bun.sleep(0);
 expect(held).toHaveLength(2);
 held[0]!.finish(done());held[1]!.finish(done());
 expect([(await first).status,(await other).status]).toEqual([200,200]);
});

test('the default function cap leaves room in the default share for REST and Auth',()=>{
 expect(FUNCTION_CONCURRENCY).toBe(4);
 expect(FUNCTION_CONCURRENCY).toBeLessThan(GATEWAY.share);
 expect(FUNCTION_CONCURRENCY).toBeGreaterThanOrEqual(1);
});

test('a function call outlives the gate deadline that still cuts a REST call, header wait and body alike',async()=>{
 // Scaled: the gate allows 30 ms, the function's own deadline stands in for the 150 s.
 const gate=new ConcurrencyGate(8,32,30,30),slow=<T>(value:T)=>new Promise<T>(resolve=>setTimeout(()=>resolve(value),80));
 const rest={service:'rest',maximum:4},fns={service:'functions',maximum:4,deadlineMs:300};
 expect((await gate.run('a',new Request('http://x'),()=>slow(new Response('ok')),rest)).status).toBe(504);
 const late=await gate.run('a',new Request('http://x'),()=>slow(new Response('ok')),fns);
 expect([late.status,await late.text()]).toEqual([200,'ok']);
 const stream=()=>new Response(new ReadableStream({async start(controller){await Bun.sleep(80);controller.enqueue(new TextEncoder().encode('done'));controller.close();}}));
 await expect((await gate.run('a',new Request('http://x'),async()=>stream(),rest)).text()).rejects.toThrow('Response stream deadline exceeded');
 expect(await (await gate.run('a',new Request('http://x'),async()=>stream(),fns)).text()).toBe('done');
 // A function over its own deadline is still cut and frees its slot.
 expect((await gate.run('b',new Request('http://x'),()=>new Promise(()=>{}),{...fns,deadlineMs:20})).status).toBe(504);
 expect((await gate.run('b',new Request('http://x'),async()=>done(204),{...fns,maximum:1})).status).toBe(204);
});

test('the gateway gives functions the 150 s deadline and no other service',async()=>{
 const budgets:Record<string,unknown>={};
 const gate={run:(_e:string,_r:unknown,forward:(s:AbortSignal)=>Promise<Response>,budget?:{service:string})=>{
  budgets[budget?.service??'none']=budget;return forward(new AbortController().signal);
 }} as unknown as ConcurrencyGate;
 const handler=createGateway(new Map([['one',{...base,serviceConcurrency:{auth:2}}]]),(async()=>done()) as unknown as typeof fetch,undefined,1000,gate);
 for(const path of [fn(),'/auth/v1/settings','/rest/v1/todos'])await handler(new Request('http://local/one'+path,{headers:{apikey:'key'}}));
 expect(FUNCTION_DEADLINE_MS).toBe(150_000);
 expect(budgets.functions).toMatchObject({maximum:FUNCTION_CONCURRENCY,deadlineMs:150_000});
 expect(budgets.auth).toEqual({service:'auth',maximum:2,drainOnCancel:false});
 expect(budgets.none).toBeUndefined();
});

test('the transport deadline is 150 s for a function call and 15 s for every other service',async()=>{
 const original=AbortSignal.timeout,asked:number[]=[];
 AbortSignal.timeout=(ms:number)=>{asked.push(ms);return original.call(AbortSignal,ms);};
 try {
  const registry=new Map([['one',{...base,storage:{url:'http://storage',tenantHost:'tenant'}}]]);
  const handler=createGateway(registry,(async()=>done()) as unknown as typeof fetch,undefined,1000,new ConcurrencyGate());
  const deadline=async(path:string)=>{
   asked.length=0;
   expect((await handler(new Request('http://local/one'+path,{headers:{apikey:'key'}}))).status).toBe(200);
   return asked.slice();
  };
  expect(await deadline(fn())).toEqual([150_000]);
  for(const path of ['/rest/v1/todos','/auth/v1/settings','/storage/v1/bucket'])expect(await deadline(path)).toEqual([15_000]);
 } finally {AbortSignal.timeout=original;}
});
