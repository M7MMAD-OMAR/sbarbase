import {test,expect,spyOn} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {signStudio,studioHost,studioProxy,STUDIO_COOKIE} from '../src/control/studio';

const key=Buffer.alloc(32,9);
const bytes=(value:string)=>new TextEncoder().encode(value);

function fixture(transport:typeof fetch,limits:{requestBodyLimit?:number;requestBodyTimeoutMs?:number}={}) {
 const catalog=new Catalog(':memory:');
 const organization=catalog.createOrganization('owner','Example');
 catalog.setMember('owner',organization,'alice','admin');
 const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'Shop'),'Production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 catalog.requestStudio('alice',environment,'running');
 const runtime=catalog.getProvision('alice',environment).runtime;
 const origin='http://'+studioHost(runtime);
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,value)=>catalog.studioAllowed(actor,value),
  upstream:()=> 'http://vendor.invalid:3000',transport,navigation:(...args)=>catalog.studioNavigation(...args),selection:(actor,id)=>catalog.studio(actor,id),...limits});
 const request=(method='GET',body?:ReadableStream<Uint8Array>|string,signal?:AbortSignal,expires=Date.now()+60_000,path='/api/platform/pg-meta/default/query')=>
  new Request(origin+path,{method,headers:{cookie:STUDIO_COOKIE+'='+signStudio(key,{runtime,actor:'alice',epoch:0,kind:'session',expires}),origin,
   'content-type':'application/json'},...(body===undefined?{}:{body,duplex:'half'}),signal} as RequestInit);
 return {catalog,proxy,request,environment,revoke:(role:'viewer'|null)=>catalog.changeMember('owner',organization,'alice',role)};
}

for(const role of [null,'viewer'] as const)for(const method of ['POST','PATCH','DELETE']) {
 test(`Studio ${method} withholds a delayed body when its admin becomes ${role??'removed'}`,async()=>{
  let calls=0,controller!:ReadableStreamDefaultController<Uint8Array>;
  const transport=(async(input:URL|RequestInfo,init?:RequestInit)=>{
   calls++;await new Request(input,init).text();return new Response('mutated');
  }) as unknown as typeof fetch;
  const f=fixture(transport);
  try{
   const stream=new ReadableStream<Uint8Array>({start(value){controller=value;}});
   const pending=f.proxy(f.request(method,stream));
   f.revoke(role);controller.enqueue(bytes('{"query":"select 1"}'));controller.close();
   const response=await pending;
   expect(response.status).toBe(403);expect(calls).toBe(0);
  }finally{f.catalog.close();}
 });
}

for(const role of [null,'viewer'] as const) {
 test(`Studio withholds a deferred response when its admin becomes ${role??'removed'}`,async()=>{
  let entered!:()=>void,finish!:()=>void,cancelled=false;
  const started=new Promise<void>(resolve=>{entered=resolve;});
  const wait=new Promise<void>(resolve=>{finish=resolve;});
  const transport=(async()=>{entered();await wait;
   return new Response(new ReadableStream<Uint8Array>({start(controller){controller.enqueue(bytes('private rows'));},cancel(){cancelled=true;}}));
  }) as unknown as typeof fetch;
  const f=fixture(transport);
  try{
   const pending=f.proxy(f.request());await started;f.revoke(role);finish();
   const response=await pending;
   expect(response.status).toBe(403);expect(await response.text()).not.toContain('private rows');
   expect(cancelled).toBe(true);
  }finally{f.catalog.close();}
 });
}

test('Studio cancels withheld streaming bytes after revocation',async()=>{
 let source!:ReadableStreamDefaultController<Uint8Array>,cancelled=false;
 const f=fixture((async()=>new Response(new ReadableStream<Uint8Array>({start(controller){source=controller;},cancel(){cancelled=true;}}))) as unknown as typeof fetch);
 try{
  const response=await f.proxy(f.request()),reader=response.body!.getReader();
  const first=reader.read();source.enqueue(bytes('visible before revocation'));
  expect(new TextDecoder().decode((await first).value)).toBe('visible before revocation');
  const next=reader.read();f.revoke(null);source.enqueue(bytes('private after revocation'));
  await expect(next).rejects.toThrow();expect(cancelled).toBe(true);
  reader.releaseLock();
 }finally{f.catalog.close();}
});

test('Studio refuses an oversized body before sending any upstream bytes',async()=>{
 let calls=0,cancelled=false;
 const f=fixture((async()=>{calls++;return new Response('mutated');}) as unknown as typeof fetch,{requestBodyLimit:4});
 try{
  const body=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(bytes('12345'));},cancel(){cancelled=true;}});
  expect((await f.proxy(f.request('POST',body))).status).toBe(413);
  expect(calls).toBe(0);expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio body deadline refuses a stalled client and cancels its reader',async()=>{
 let calls=0,cancelled=false;
 const f=fixture((async()=>{calls++;return new Response('mutated');}) as unknown as typeof fetch,{requestBodyTimeoutMs:20});
 try{
  const body=new ReadableStream<Uint8Array>({cancel(){cancelled=true;}});
  expect((await f.proxy(f.request('POST',body))).status).toBe(408);
  expect(calls).toBe(0);expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio abort during body acquisition never dispatches upstream',async()=>{
 let calls=0,cancelled=false;
 const f=fixture((async()=>{calls++;return new Response('mutated');}) as unknown as typeof fetch);
 try{
  const abort=new AbortController(),body=new ReadableStream<Uint8Array>({cancel(){cancelled=true;}});
  const pending=f.proxy(f.request('POST',body,abort.signal));abort.abort();
  expect((await pending).status).toBe(400);expect(calls).toBe(0);expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio body read failure never dispatches upstream',async()=>{
 let calls=0;
 const f=fixture((async()=>{calls++;return new Response('mutated');}) as unknown as typeof fetch);
 try{
  const body=new ReadableStream<Uint8Array>({start(controller){controller.error(new Error('disconnected'));}});
  expect((await f.proxy(f.request('POST',body))).status).toBe(400);expect(calls).toBe(0);
 }finally{f.catalog.close();}
});

test('Studio forwards the exact bounded body and preserves vendor headers',async()=>{
 const seen:Request[]=[];
 const f=fixture((async(input:URL|RequestInfo,init?:RequestInit)=>{
  seen.push(new Request(input,init));return new Response('result',{headers:{'set-cookie':'studio_theme=dark; Path=/','content-type':'text/plain'}});
 }) as unknown as typeof fetch,{requestBodyLimit:4});
 try{
  const response=await f.proxy(f.request('POST','1234'));
  expect(response.status).toBe(200);expect(await response.text()).toBe('result');
  expect(await seen[0]!.text()).toBe('1234');expect(seen[0]!.headers.get('cookie')).toBeNull();
  expect(response.headers.getSetCookie()).toEqual(['studio_theme=dark; Path=/']);
 }finally{f.catalog.close();}
});

test('Studio preserves a bodyless vendor response',async()=>{
 const f=fixture((async()=>new Response(null,{status:204})) as unknown as typeof fetch);
 try{const response=await f.proxy(f.request('POST'));expect(response.status).toBe(204);expect(response.body).toBeNull();}
 finally{f.catalog.close();}
});

for(const boundary of ['body','response'] as const) {
 test(`Studio rechecks session expiry after awaiting its ${boundary}`,async()=>{
  let calls=0,controller!:ReadableStreamDefaultController<Uint8Array>,finish!:()=>void,entered!:()=>void;
  const wait=new Promise<void>(resolve=>{finish=resolve;}),started=new Promise<void>(resolve=>{entered=resolve;});
  const f=fixture((async()=>{calls++;entered();await wait;return new Response('private result');}) as unknown as typeof fetch);
  try{
   const stream=new ReadableStream<Uint8Array>({start(value){controller=value;}});
   const pending=f.proxy(f.request(boundary==='body'?'POST':'GET',boundary==='body'?stream:undefined,undefined,Date.now()+60));
   if(boundary==='response')await started;
   await Bun.sleep(80);
   if(boundary==='body'){controller.enqueue(bytes('query'));controller.close();}else finish();
   const response=await pending;
   expect(response.status).toBe(403);expect(await response.text()).not.toContain('private result');
   expect(calls).toBe(boundary==='body'?0:1);
  }finally{f.catalog.close();}
 });
}

test('Studio refusal does not wait for a stalled cancellation promise',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch,{requestBodyLimit:1});
 try{
  const body=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(bytes('xx'));},cancel(){cancelled=true;return new Promise<void>(()=>{});}});
  expect((await f.proxy(f.request('POST',body))).status).toBe(413);expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio downstream cancellation cancels the vendor without waiting indefinitely',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response(new ReadableStream<Uint8Array>({cancel(){cancelled=true;return new Promise<void>(()=>{});}}))) as unknown as typeof fetch);
 try{
  const response=await f.proxy(f.request()),reader=response.body!.getReader();
  await reader.cancel();expect(cancelled).toBe(true);reader.releaseLock();
 }finally{f.catalog.close();}
});

test('Studio abort while waiting for response bytes cancels the vendor',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response(new ReadableStream<Uint8Array>({cancel(){cancelled=true;}}))) as unknown as typeof fetch);
 try{
  const abort=new AbortController(),response=await f.proxy(f.request('GET',undefined,abort.signal));
  const reader=response.body!.getReader(),pending=reader.read();abort.abort();
  await expect(pending).rejects.toThrow();expect(cancelled).toBe(true);reader.releaseLock();
 }finally{f.catalog.close();}
});

test('Studio HEAD discards a vendor body and preserves response status',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response(new ReadableStream<Uint8Array>({cancel(){cancelled=true;}}),{status:200})) as unknown as typeof fetch);
 try{const response=await f.proxy(f.request('HEAD'));expect(response.status).toBe(200);expect(response.body).toBeNull();expect(cancelled).toBe(true);}
 finally{f.catalog.close();}
});

test('Studio preserves a not-modified vendor response',async()=>{
 const f=fixture((async()=>new Response(null,{status:304,headers:{etag:'test'}})) as unknown as typeof fetch);
 try{const response=await f.proxy(f.request());expect(response.status).toBe(304);expect(response.body).toBeNull();expect(response.headers.get('etag')).toBe('test');}
 finally{f.catalog.close();}
});

test('Studio maps a failed vendor connection to a bounded proxy error',async()=>{
 const f=fixture((async()=>{throw new Error('private vendor details');}) as unknown as typeof fetch);
 try{const response=await f.proxy(f.request());expect(response.status).toBe(502);expect(await response.text()).not.toContain('private vendor details');}
 finally{f.catalog.close();}
});

test('Studio switching cannot renew a session that expires during the body read',async()=>{
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  const stream=new ReadableStream<Uint8Array>({start(value){controller=value;}});
  const pending=f.proxy(f.request('POST',stream,undefined,Date.now()+60,'/__sbarbase/switch'));
  await Bun.sleep(80);controller.enqueue(bytes(JSON.stringify({environment:f.environment})));controller.close();
  const response=await pending;expect(response.status).toBe(403);expect(await response.text()).not.toContain('ticket=');
 }finally{f.catalog.close();}
});

test('Studio switching times out a stalled body and cancels it',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch,{requestBodyTimeoutMs:20});
 try{
  const stream=new ReadableStream<Uint8Array>({cancel(){cancelled=true;}});
  expect((await f.proxy(f.request('POST',stream,undefined,undefined,'/__sbarbase/switch'))).status).toBe(408);
  expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio switching rejects excess bytes without waiting for cancellation',async()=>{
 let cancelled=false;
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  const stream=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(bytes('x'.repeat(513)));},cancel(){cancelled=true;return new Promise<void>(()=>{});}});
  expect((await f.proxy(f.request('POST',stream,undefined,undefined,'/__sbarbase/switch'))).status).toBe(413);
  expect(cancelled).toBe(true);
 }finally{f.catalog.close();}
});

test('Studio byte accounting ignores empty chunks and preserves fragmented input',async()=>{
 const seen:string[]=[];
 const f=fixture((async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(await new Request(input,init).text());return new Response('result');}) as unknown as typeof fetch,{requestBodyLimit:4});
 try{
  const stream=new ReadableStream<Uint8Array>({start(controller){
   for(const text of ['','1','','2','3','','4',''])controller.enqueue(bytes(text));controller.close();
  }});
  expect((await f.proxy(f.request('POST',stream))).status).toBe(200);expect(seen).toEqual(['1234']);
 }finally{f.catalog.close();}
});

test('Studio checks its monotonic deadline even before the timer callback runs',async()=>{
 let calls=0,cancelled=false,tick=0;
 const f=fixture((async()=>{calls++;return new Response('unexpected');}) as unknown as typeof fetch,{requestBodyTimeoutMs:10});
 const clock=spyOn(performance,'now').mockImplementation(()=>tick++*5);
 try{
  const stream=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new Uint8Array());},cancel(){cancelled=true;}});
  expect((await f.proxy(f.request('POST',stream))).status).toBe(408);expect(calls).toBe(0);expect(cancelled).toBe(true);
 }finally{clock.mockRestore();f.catalog.close();}
});

test('Studio withholds unread workspace metadata after source access is revoked',async()=>{
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  const response=await f.proxy(f.request('GET',undefined,undefined,undefined,'/__sbarbase/navigation'));
  expect(response.status).toBe(200);f.revoke(null);
  await expect(response.text()).rejects.toThrow('Studio access revoked');
 }finally{f.catalog.close();}
});

test('Studio withholds an unread switch ticket after source access is revoked',async()=>{
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  (f.catalog as unknown as {db:{query:(sql:string)=>{run:()=>unknown}}}).db.query("UPDATE studio_sessions SET state='running'").run();
  const response=await f.proxy(f.request('POST',JSON.stringify({environment:f.environment}),undefined,undefined,'/__sbarbase/switch'));
  expect(response.status).toBe(200);f.revoke(null);
  await expect(response.text()).rejects.toThrow('Studio access revoked');
 }finally{f.catalog.close();}
});

test('Studio withholds selected organization metadata after that membership is revoked',async()=>{
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  const other=f.catalog.createOrganization('bob','Private other organization');
  f.catalog.setMember('bob',other,'alice','admin');
  const project=f.catalog.createProject('bob',other,'Private project');
  f.catalog.createEnvironment('bob',project,'Production');
  const response=await f.proxy(f.request('GET',undefined,undefined,undefined,'/__sbarbase/navigation?organization='+other));
  expect(response.status).toBe(200);f.catalog.changeMember('bob',other,'alice',null);
  await expect(response.text()).rejects.toThrow('Studio access revoked');
 }finally{f.catalog.close();}
});

test('Studio withholds a revoked organization from an unread navigation inventory',async()=>{
 const f=fixture((async()=>new Response('unexpected')) as unknown as typeof fetch);
 try{
  const other=f.catalog.createOrganization('bob','Private other organization');
  f.catalog.setMember('bob',other,'alice','admin');
  const response=await f.proxy(f.request('GET',undefined,undefined,undefined,'/__sbarbase/navigation'));
  expect(response.status).toBe(200);f.catalog.changeMember('bob',other,'alice',null);
  await expect(response.text()).rejects.toThrow('Studio access revoked');
 }finally{f.catalog.close();}
});
