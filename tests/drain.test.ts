import {test,expect} from 'bun:test';
import {ConcurrencyGate} from '../src/gateway/concurrency';

const request=()=>new Request('http://local');
test('pause rejects new work and waits through response consumption without blocking a neighbor',async()=>{
 const gate=new ConcurrencyGate();let controller!:ReadableStreamDefaultController<Uint8Array>;
 const existing=await gate.run('source',request(),async()=>new Response(new ReadableStream({start(c){controller=c;}})));
 const lease=gate.pause('source');let forwarded=false;
 const refused=await gate.run('source',request(),async()=>{forwarded=true;return new Response();});
 expect(refused.status).toBe(503);expect(refused.headers.get('retry-after')).toBe('1');expect(forwarded).toBe(false);
 const neighbor=await gate.run('neighbor',request(),async()=>new Response('ok'));
 expect(await neighbor.text()).toBe('ok');
 expect(await lease.waitForDrain(5)).toBe(false);
 const draining=lease.waitForDrain(1000);controller.enqueue(new TextEncoder().encode('complete'));controller.close();
 expect(await existing.text()).toBe('complete');expect(await draining).toBe(true);
 expect((await gate.run('source',request(),async()=>new Response())).status).toBe(503);
 lease.resume();expect((await gate.run('source',request(),async()=>new Response())).status).toBe(200);
});

test('only the active pause lease can resume and early resume does not certify draining',async()=>{
 const gate=new ConcurrencyGate();let finish!:(response:Response)=>void;
 const work=gate.run('source',request(),()=>new Promise(resolve=>{finish=resolve;}));
 const first=gate.pause('source');expect(()=>gate.pause('source')).toThrow();
 const waiting=first.waitForDrain(1000);first.resume();expect(await waiting).toBe(false);
 const second=gate.pause('source');expect(()=>first.resume()).toThrow();
 finish(new Response());await work;expect(await second.waitForDrain(100)).toBe(true);second.resume();
});

test('failed upstream settles drain waiters but timeout leaves admission paused',async()=>{
 const gate=new ConcurrencyGate();let reject!:(error:Error)=>void;
 const work=gate.run('source',request(),()=>new Promise((_,fail)=>{reject=fail;})).catch(()=>undefined);
 const lease=gate.pause('source');expect(await lease.waitForDrain(5)).toBe(false);
 const draining=lease.waitForDrain(1000);reject(new Error('upstream failed'));await work;
 expect(await draining).toBe(true);expect((await gate.run('source',request(),async()=>new Response())).status).toBe(503);lease.resume();
});

test('idle pause drains immediately and rejects invalid deadlines',async()=>{
 const lease=new ConcurrencyGate().pause('idle');expect(()=>lease.waitForDrain(0)).toThrow();
 expect(await lease.waitForDrain(100)).toBe(true);lease.resume();expect(()=>lease.waitForDrain(100)).toThrow();
});

test('managed factories share pause and resolve new placement only after an explicit drained switch',async()=>{
 const {Catalog}=await import('../src/control/catalog');const {KeyStore}=await import('../src/control/keys');
 const {managedGateway,pauseManagedEnvironment}=await import('../src/gateway/managed');
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const org=catalog.createOrganization('owner','O'),project=catalog.createProject('owner',org,'P');
 const environment=catalog.createEnvironment('owner',project,'E'),job=catalog.claimProvision()!;
 catalog.finishProvision(environment,job.claim!,true);const key=keys.issue(job.runtime);
 let placement='http://source.invalid',complete!:(value:Response)=>void;const destinations:string[]=[];
 const route=()=>({auth:placement,rest:placement,keys:[],anonymousToken:'anon',enabled:true});
 const transport=(async(input:RequestInfo|URL)=>{
  destinations.push(String(input));
  if(destinations.length===1)return new Promise<Response>(resolve=>{complete=resolve;});
  return new Response(null,{status:204});
 }) as typeof fetch;
 const first=managedGateway(catalog,keys,route,transport),second=managedGateway(catalog,keys,route,transport);
 const req=()=>new Request(`http://local/${job.runtime}/rest/v1/items`,{headers:{apikey:key.token}});
 const pending=first(req());const pause=pauseManagedEnvironment(job.runtime);
 try {
  expect((await second(req())).status).toBe(503);expect(destinations.length).toBe(1);
  expect(await pause.waitForDrain(5)).toBe(false);
  complete(new Response(null,{status:204}));await pending;expect(await pause.waitForDrain(100)).toBe(true);
  placement='http://target.invalid';pause.resume();
  expect((await second(req())).status).toBe(204);
  expect(destinations).toEqual(['http://source.invalid/items','http://target.invalid/items']);
 }finally{try{pause.resume();}catch{}catalog.close();keys.close();}
});
