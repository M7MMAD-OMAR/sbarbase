import {test,expect} from 'bun:test';
import {inSubnet,functionsStorage,functionsStorageWanted,functionsStorageListeners} from '../src/control/functions-storage';
import {serveLocal} from '../src/http/local-server';

const A='e_'+'a'.repeat(24),B='e_'+'b'.repeat(24);

test('a source is admitted only from inside its environment network',()=>{
 expect(inSubnet('172.30.4.7','172.30.4.0/24')).toBe(true);
 expect(inSubnet('::ffff:172.30.4.7','172.30.4.0/24')).toBe(true);
 expect(inSubnet('172.30.4.1','172.30.4.0/24')).toBe(true);
 // Another environment's network, the outbound network, the runtime network, loopback.
 for(const source of ['172.30.5.7','172.31.0.2','10.0.0.3','127.0.0.1','::1','',
  '172.30.4','172.30.4.7.1','172.30.4.256','172.30.04.7','fe80::1'])
  expect(inSubnet(source,'172.30.4.0/24')).toBe(false);
 expect(inSubnet('172.30.4.7','172.30.0.0/16')).toBe(true);
 for(const subnet of ['172.30.4.0','172.30.4.0/33','172.30.4.0/24/1','fd00::/64','x/24'])
  expect(inSubnet('172.30.4.7',subnet)).toBe(false);
});

test('only well formed entries of environments with Functions on are listened for',()=>{
 const storage={host:'172.30.4.1',port:25433,subnet:'172.30.4.0/24'};
 const wanted=functionsStorageWanted({[A]:{functions:{url:'x',storage}},[B]:{storage:{url:'s'}},
  'e_bad':{functions:{storage}},['e_'+'c'.repeat(24)]:{functions:{storage:{...storage,host:'172.30.5.1'}}},
  ['e_'+'d'.repeat(24)]:{functions:{storage:{...storage,port:'25433'}}}});
 expect([...wanted]).toEqual([[A,storage]]);
});

test('the tenant is set by the listener, never by the caller',async()=>{
 const seen:Request[]=[];
 const handler=functionsStorage({runtime:A,active:()=>true,storage:()=>({url:'http://storage:5000',tenantHost:A+'.storage.internal'}),
  transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));
   return Response.json({ok:true},{headers:{'x-forwarded-host':'leak'}});}) as typeof fetch});
 const response=await handler(new Request('http://172.30.4.1:25433/object/public/b/x.png?download=1',{headers:{
  'x-forwarded-host':B+'.storage.internal',forwarded:'host='+B+'.storage.internal','x-forwarded-for':'1.2.3.4',authorization:'Bearer t',apikey:'k'}}));
 expect(response.status).toBe(200);
 expect(response.headers.get('x-forwarded-host')).toBeNull();
 const sent=seen[0]!;
 expect(sent.url).toBe('http://storage:5000/object/public/b/x.png?download=1');
 expect(sent.headers.get('x-forwarded-host')).toBe(A+'.storage.internal');
 expect(sent.headers.get('forwarded')).toBeNull();
 expect(sent.headers.get('x-forwarded-for')).toBeNull();
 expect(sent.headers.get('authorization')).toBe('Bearer t');
 expect(sent.headers.get('apikey')).toBe('k');
});

test('an inactive environment, or a published tenant for another environment, is refused',async()=>{
 let sent=0;
 const transport=(async()=>{sent++;return new Response('x');}) as unknown as typeof fetch;
 const request=()=>new Request('http://172.30.4.1:25433/bucket');
 expect((await functionsStorage({runtime:A,active:()=>false,transport,storage:()=>({url:'http://s:5000',tenantHost:A+'.storage.internal'})})(request())).status).toBe(503);
 expect((await functionsStorage({runtime:A,active:()=>true,transport,storage:()=>({url:'http://s:5000',tenantHost:B+'.storage.internal'})})(request())).status).toBe(503);
 expect((await functionsStorage({runtime:A,active:()=>true,transport,storage:()=>undefined})(request())).status).toBe(503);
 expect(sent).toBe(0);
});

test('a listener closes a connection from outside its subnet before reading a request',async()=>{
 let handled=0;
 const refused=await serveLocal(()=>{handled++;return new Response('ok');},0,{admit:source=>inSubnet(source,'172.30.4.0/24')});
 try {
  await expect(fetch(`http://127.0.0.1:${refused.port}/bucket`)).rejects.toThrow();
  expect(handled).toBe(0);
 } finally {refused.stop(true);}
 const admitted=await serveLocal(()=>{handled++;return new Response('ok');},0,{admit:source=>inSubnet(source,'127.0.0.0/8')});
 try {
  expect((await fetch(`http://127.0.0.1:${admitted.port}/bucket`)).status).toBe(200);
  expect(handled).toBe(1);
 } finally {admitted.stop(true);}
 await expect(serveLocal(()=>new Response('x'),0,{admit:()=>true,upgrade:()=>({ok:false,status:404,message:'x'})})).rejects.toThrow();
});

test('listeners follow the published endpoints',async()=>{
 const storage={host:'172.30.4.1',port:25433,subnet:'172.30.4.0/24'};
 let endpoints:Record<string,any>={[A]:{functions:{storage}}};
 const opened:string[]=[],stopped:string[]=[];
 const listeners=functionsStorageListeners({read:()=>endpoints,open:async(runtime,at)=>{
  if(at.host==='172.30.9.1')throw new Error('address not available');
  opened.push(`${runtime}@${at.host}`);return {stop:()=>{stopped.push(`${runtime}@${at.host}`);}};}});
 await listeners.reconcile();await listeners.reconcile();
 expect(opened).toEqual([A+'@172.30.4.1']);
 endpoints={[A]:{functions:{storage:{...storage,host:'172.30.6.1',subnet:'172.30.6.0/24'}}},[B]:{functions:{storage:{...storage,host:'172.30.9.1',subnet:'172.30.9.0/24'}}}};
 await listeners.reconcile();
 expect(stopped).toEqual([A+'@172.30.4.1']);
 expect(listeners.runtimes()).toEqual([A]);
 endpoints={[A]:{}};
 await listeners.reconcile();
 expect(listeners.runtimes()).toEqual([]);
 expect(stopped).toEqual([A+'@172.30.4.1',A+'@172.30.6.1']);
});
