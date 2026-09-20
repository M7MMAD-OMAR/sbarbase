import {test,expect} from 'bun:test';
import {ConcurrencyGate} from '../src/gateway/concurrency';
const request=()=>new Request('http://localhost/');
const empty=async()=>new Response(null,{status:204});

test('one saturated environment leaves room for a neighbor and rejects without forwarding',async()=>{
 const gate=new ConcurrencyGate(1,2);
 let complete!:(response:Response)=>void;
 const pending=gate.run('a',request(),()=>new Promise(resolve=>{complete=resolve;}));
 let forwarded=false;
 const denied=await gate.run('a',request(),async()=>{forwarded=true;return new Response('wrong');});
 expect(denied.status).toBe(429);expect(denied.headers.get('retry-after')).toBe('1');expect(forwarded).toBe(false);
 expect((await gate.run('b',request(),empty)).status).toBe(204);
 complete(new Response(null,{status:204}));await pending;
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('response headers do not release the slot, consuming or cancelling body does',async()=>{
 const gate=new ConcurrencyGate(1,2);
 const response=await gate.run('a',request(),async()=>new Response('held'));
 expect((await gate.run('a',request(),empty)).status).toBe(429);
 expect(await response.text()).toBe('held');
 const second=await gate.run('a',request(),async()=>new Response('cancel'));
 await second.body!.cancel();
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('global ceiling and thrown forward recover without leaked slots',async()=>{
 const gate=new ConcurrencyGate(2,1);
 const response=await gate.run('a',request(),async()=>new Response('held'));
 expect((await gate.run('b',request(),empty)).status).toBe(503);
 await response.body!.cancel();
 await expect(gate.run('b',request(),async()=>{throw new Error('failed');})).rejects.toThrow('failed');
 expect((await gate.run('b',request(),empty)).status).toBe(204);
});

test('stalled response timeout and request abort cancel upstream body and release once',async()=>{
 const gate=new ConcurrencyGate(1,2,20);
 let cancellations=0;
 const stalled=()=>new Response(new ReadableStream({pull:()=>new Promise(()=>{}),cancel(){cancellations++;}}));
 const response=await gate.run('a',request(),async()=>stalled());
 await Bun.sleep(40);
 await expect(response.text()).rejects.toThrow();
 expect(cancellations).toBe(1);
 expect((await gate.run('a',request(),empty)).status).toBe(204);
 const abort=new AbortController();
 const second=await gate.run('a',new Request('http://localhost/',{signal:abort.signal}),async()=>stalled());
 abort.abort();expect(await second.text()).toBe('');
 expect(cancellations).toBe(2);
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('already aborted work never forwards, hanging cancellation cannot hold a slot',async()=>{
 const gate=new ConcurrencyGate(1,1,10),abort=new AbortController();abort.abort();let calls=0;
 expect((await gate.run('a',new Request('http://localhost/',{signal:abort.signal}),async()=>{calls++;return new Response('bad');})).status).toBe(408);
 expect(calls).toBe(0);
 const response=await gate.run('a',request(),async()=>new Response(new ReadableStream({cancel:()=>new Promise(()=>{})})));
 await response.body!.cancel();
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('stream errors release capacity and response metadata survives wrapping',async()=>{
 const gate=new ConcurrencyGate(1,1);
 const response=await gate.run('a',request(),async()=>new Response(new ReadableStream({pull(controller){controller.error(new Error('stream failed'));}}),{status:206,headers:{'x-test':'preserved'}}));
 expect(response.status).toBe(206);expect(response.headers.get('x-test')).toBe('preserved');
 await expect(response.text()).rejects.toThrow('stream failed');
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('separate managed factories share tenant capacity across keys and services',async()=>{
 const {Catalog}=await import('../src/control/catalog');const {KeyStore}=await import('../src/control/keys');const {managedGateway}=await import('../src/gateway/managed');
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const org=catalog.createOrganization('owner','Org'),project=catalog.createProject('owner',org,'Project');
 const environment=catalog.createEnvironment('owner',project,'env'),job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const first=keys.issue(job.runtime),second=keys.issue(job.runtime);
 const complete:((r:Response)=>void)[]=[];
 const transport=(()=>new Promise<Response>(resolve=>complete.push(resolve))) as typeof fetch;
 const route=()=>({auth:'http://upstream',rest:'http://upstream',storage:{url:'http://upstream',tenantHost:'owned.storage'},keys:[],anonymousToken:'anon',enabled:true});
 const a=managedGateway(catalog,keys,route,transport),b=managedGateway(catalog,keys,route,transport);
 const req=(key:string,service='rest')=>new Request(`http://localhost/${job.runtime}/${service}/v1/`,{headers:{apikey:key}});
 const pending=Array.from({length:8},(_,i)=>(i%2?a:b)(req(i%2?first.token:second.token,i%2?'auth':'rest')));
 try {
  expect(complete.length).toBe(8);
  expect((await a(req(first.token))).status).toBe(429);
  expect((await b(new Request(`http://localhost/${job.runtime}/storage/v1/object/public/bucket/file`))).status).toBe(429);
  expect((await a(req('wrong'))).status).toBe(401);
 }finally{
  for(const finish of complete)finish(new Response(null,{status:204}));await Promise.all(pending);catalog.close();keys.close();
 }
});

test('slow upload consumes a slot and abort does not accidentally forward a cancelled body',async()=>{
 const {createGateway}=await import('../src/gateway/handler');
 const gate=new ConcurrencyGate(1,1);let calls=0;
 const transport=(async()=>{calls++;return new Response(null,{status:204});}) as typeof fetch;
 const handler=createGateway(new Map([['owned',{auth:'http://upstream',rest:'http://upstream',keys:['key'],anonymousToken:'anon',enabled:true}]]),transport,undefined,1000,gate);
 const abort=new AbortController();
 const pending=handler(new Request('http://localhost/owned/rest/v1/',{method:'POST',headers:{apikey:'key'},body:new ReadableStream({pull:()=>new Promise(()=>{}),cancel:()=>new Promise(()=>{})}),signal:abort.signal,duplex:'half'} as RequestInit));
 const make=()=>new Request('http://localhost/owned/rest/v1/',{headers:{apikey:'key'}});
 expect((await handler(make())).status).toBe(429);
 abort.abort();const failed=await pending;expect(failed.status).toBe(408);expect((await failed.json()).message).toBe('Request cancelled');
 expect(calls).toBe(0);expect((await handler(make())).status).toBe(204);expect(calls).toBe(1);
});


test('pre-header deadline frees capacity and cancels a late response from uncooperative transport',async()=>{
 const gate=new ConcurrencyGate(1,1,1000,20);
 let complete!:(response:Response)=>void;
 let upstream:AbortSignal|undefined;
 const pending=gate.run('a',request(),signal=>{upstream=signal;return new Promise(resolve=>{complete=resolve;});});
 expect((await gate.run('a',request(),empty)).status).toBe(429);
 expect((await pending).status).toBe(504);
 expect(upstream?.aborted).toBe(true);
 const held=await gate.run('a',request(),async()=>new Response('next'));
 let cancelled=false;
 complete(new Response(new ReadableStream({cancel(){cancelled=true;}})));
 await Bun.sleep(0);
 expect(cancelled).toBe(true);
 expect((await gate.run('a',request(),empty)).status).toBe(429);
 await held.text();
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('pre-header client abort releases slot even if transport never settles',async()=>{
 const gate=new ConcurrencyGate(1,1,1000,1000);
 const client=new AbortController();let upstream:AbortSignal|undefined;
 const pending=gate.run('a',new Request('http://localhost/',{signal:client.signal}),signal=>{
  upstream=signal;return new Promise(()=>{});
 });
 client.abort();expect((await pending).status).toBe(408);
 expect(upstream?.aborted).toBe(true);
 expect((await gate.run('a',request(),empty)).status).toBe(204);
});

test('service budget rejects atomically while preserving other services and neighbors',async()=>{
 const gate=new ConcurrencyGate(3,4);
 const rest={service:'rest',maximum:1};
 const held=await gate.run('a',request(),async()=>new Response('held'),rest);
 let forwarded=false;
 const rejected=await gate.run('a',request(),async()=>{forwarded=true;return new Response('wrong');},rest);
 expect(rejected.status).toBe(429);expect(forwarded).toBe(false);
 const auth=await gate.run('a',request(),async()=>new Response('auth'),{service:'auth',maximum:2});
 const storage=await gate.run('a',request(),async()=>new Response('storage'),{service:'storage',maximum:2});
 expect((await gate.run('a',request(),empty)).status).toBe(429);
 const neighbor=await gate.run('b',request(),async()=>new Response('neighbor'),rest);
 expect((await gate.run('c',request(),empty,rest)).status).toBe(503);
 await held.body!.cancel();
 expect((await gate.run('a',request(),empty,rest)).status).toBe(204);
 await Promise.all([auth.text(),storage.text(),neighbor.text()]);
 expect((await gate.run('a',request(),empty,rest)).status).toBe(204);
 expect((await gate.run('a',request(),empty,{service:'rest',maximum:0})).status).toBe(503);
});

test('service cap persists across managed factories and API keys',async()=>{
 const {Catalog}=await import('../src/control/catalog');const {KeyStore}=await import('../src/control/keys');const {managedGateway}=await import('../src/gateway/managed');
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const org=catalog.createOrganization('owner','Org'),project=catalog.createProject('owner',org,'Project');
 const environment=catalog.createEnvironment('owner',project,'env'),job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const first=keys.issue(job.runtime),second=keys.issue(job.runtime);
 const complete:((r:Response)=>void)[]=[];
 const transport=(()=>new Promise<Response>(resolve=>complete.push(resolve))) as typeof fetch;
 const route=()=>({auth:'http://upstream',rest:'http://upstream',keys:[],anonymousToken:'anon',enabled:true,serviceConcurrency:{rest:1}});
 const a=managedGateway(catalog,keys,route,transport),b=managedGateway(catalog,keys,route,transport);
 const req=(key:string,service='rest')=>new Request(`http://localhost/${job.runtime}/${service}/v1/`,{headers:{apikey:key}});
 const pending=[a(req(first.token))];
 try{
  expect((await b(req(second.token))).status).toBe(429);
  expect(complete.length).toBe(1);
  pending.push(b(req(second.token,'auth')));expect(complete.length).toBe(2);
 }finally{for(const finish of complete)finish(new Response(null,{status:204}));await Promise.all(pending);catalog.close();keys.close();}
});

test('service budget releases after pre-header and response deadlines',async()=>{
 const gate=new ConcurrencyGate(8,32,10,10),budget={service:'rest',maximum:1};
 expect((await gate.run('a',request(),()=>new Promise(()=>{}),budget)).status).toBe(504);
 const response=await gate.run('a',request(),async()=>new Response(new ReadableStream()),budget);
 await expect(response.text()).rejects.toThrow('Response stream deadline exceeded');
 expect((await gate.run('a',request(),empty,budget)).status).toBe(204);
});
