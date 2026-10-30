import {test,expect} from 'bun:test';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {composeServices,readStats,servicesHandler,type Stats} from '../src/control/services';
import {ServiceActivity} from '../src/gateway/activity';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {managedGateway} from '../src/gateway/managed';

const MIB=1024*1024;
function ready() {
 const catalog=new Catalog(':memory:');
 catalog.initializeInstallation('op-1','alice','Home');
 catalog.setInstallationServices('alice',{defaultMode:'off'});
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');catalog.setMember('alice',org,'dave','admin');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 return {catalog,environment,runtime:job.runtime};
}
const stats=(runtime:string,at:number):Stats=>({capturedAt:at,containers:{[`${runtime}-realtime`]:{memoryBytes:183*MIB,limitBytes:320*MIB},
 [`${runtime}-functions`]:{memoryBytes:31*MIB,limitBytes:384*MIB}},costs:{realtime:320*MIB,functions:384*MIB}});

test('every member reads the Services state, only owners and admins are told they can change it',()=>{
 const {catalog,environment}=ready();
 try{
  expect(catalog.services('alice',environment).actorCanChange).toBe(true);
  expect(catalog.services('dave',environment).actorCanChange).toBe(true);
  expect(catalog.services('carol',environment).actorCanChange).toBe(false);
  expect(()=>catalog.services('mallory',environment)).toThrow();
  const state=catalog.services('carol',environment);
  for(const name of ['realtime','functions','database','cron','studio'] as const)expect(state[name]).toMatchObject({state:'off',desired:'off',failure:null});
 }finally{catalog.close();}
});

test('a request in progress reads as starting or stopping by what was asked for, a refusal as failed',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.requestRealtime('alice',environment,true);
  const view=(now=Date.now())=>composeServices({state:catalog.services('alice',environment),stats:stats(runtime,now),now});
  expect(view().services.find(item=>item.id==='realtime')).toMatchObject({state:'starting'});
  catalog.db.query("UPDATE realtime_settings SET state='on' WHERE runtime=?").run(runtime);
  expect(view().services.find(item=>item.id==='realtime')).toMatchObject({state:'on',memoryBytes:183*MIB,memoryLimitBytes:320*MIB,costBytes:320*MIB});
  catalog.requestRealtime('alice',environment,false);
  expect(view().services.find(item=>item.id==='realtime')).toMatchObject({state:'stopping'});
  catalog.db.query("UPDATE realtime_settings SET state='failed',failure='Not enough room.' WHERE runtime=?").run(runtime);
  expect(view().services.find(item=>item.id==='realtime')).toMatchObject({state:'failed',failure:'Not enough room.'});
  // A failure text is shown only while the state is failed.
  catalog.db.query("UPDATE realtime_settings SET state='off' WHERE runtime=?").run(runtime);
  expect(view().services.find(item=>item.id==='realtime')!.failure).toBeNull();
 }finally{catalog.close();}
});

test('Studio, direct database access and scheduled jobs are listed beside Realtime and Functions',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.touchStudio(runtime,12345);
  const answer=composeServices({state:catalog.services('alice',environment),stats:stats(runtime,1),now:2});
  expect(answer.services.map(item=>item.id)).toEqual(['realtime','functions','database','cron','studio']);
  expect(answer.services.find(item=>item.id==='studio')!.lastUsedAt).toBe(12345);
  // No container of their own: no memory, and their use is not tracked.
  for(const id of ['database','cron'])expect(answer.services.find(item=>item.id===id)).toMatchObject({memoryBytes:null,costBytes:null,tracked:false});
 }finally{catalog.close();}
});

test('a sample older than three minutes is stale and its numbers are not shown as current',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  const now=10*60_000;
  const state=catalog.services('alice',environment);
  const fresh=composeServices({state,stats:stats(runtime,now-60_000),now});
  expect(fresh.stats).toEqual({capturedAt:now-60_000,stale:false});
  expect(fresh.services[0]!.memoryBytes).toBe(183*MIB);
  const old=composeServices({state,stats:stats(runtime,now-4*60_000),now});
  expect(old.stats.stale).toBe(true);
  expect(old.services.every(item=>item.memoryBytes===null)).toBe(true);
  const none=composeServices({state,now});
  expect(none.stats).toEqual({capturedAt:null,stale:true});
 }finally{catalog.close();}
});

test('the sampler file is read with a size cap and only what it should hold',()=>{
 const directory=mkdtempSync(join(tmpdir(),'stats-'));
 try{
  const path=join(directory,'stats.json');
  const E='e_'+'a'.repeat(24);
  writeFileSync(path,JSON.stringify({version:1,capturedAt:5,containers:{[`${E}-realtime`]:{memoryBytes:7,limitBytes:9},'db':{memoryBytes:1,limitBytes:2},
   [`${E}-functions`]:{memoryBytes:3,limitBytes:4},'../etc':{memoryBytes:1,limitBytes:2},[`${E}-rest`]:{memoryBytes:-1,limitBytes:2},[`${E}-auth`]:{memoryBytes:'x',limitBytes:2}},costs:{realtime:10,functions:'no'}}));
  expect(readStats(path)).toEqual({capturedAt:5,containers:{[`${E}-realtime`]:{memoryBytes:7,limitBytes:9},[`${E}-functions`]:{memoryBytes:3,limitBytes:4},db:{memoryBytes:1,limitBytes:2}},costs:{realtime:10,functions:null}});
  writeFileSync(path,JSON.stringify({version:2,capturedAt:5,containers:{}}));expect(readStats(path)).toBeUndefined();
  writeFileSync(path,'not json');expect(readStats(path)).toBeUndefined();
  writeFileSync(path,JSON.stringify({version:1,capturedAt:5,containers:{},pad:'x'.repeat(300*1024)}));expect(readStats(path)).toBeUndefined();
  expect(readStats(join(directory,'missing.json'))).toBeUndefined();
 }finally{rmSync(directory,{recursive:true,force:true});}
});

test('the Services route answers every member, refuses other methods and strangers, and never carries a secret',async()=>{
 const {catalog,environment,runtime}=ready();
 try{
  const handler=servicesHandler(catalog,async request=>request.headers.get('authorization'),{stats:()=>stats(runtime,Date.now()),
   memory:()=>({availableBytes:2*1024*MIB,totalBytes:16*1024*MIB})});
  const call=(actor:string,method='GET')=>handler(new Request(`http://local/management/v1/environments/${environment}/services`,{method,headers:{authorization:actor}}));
  const viewer=await call('carol');
  expect(viewer.status).toBe(200);
  const body=await viewer.json() as {data:{canChange:boolean;capacity:{availableBytes:number};services:{id:string}[]}};
  expect(body.data.canChange).toBe(false);
  expect(body.data.capacity.availableBytes).toBe(2*1024*MIB);
  expect(JSON.stringify(body)).not.toMatch(/secret|password|token|key/i);
  expect((await call('mallory')).status).toBe(403);
  expect((await call('alice','PUT')).status).toBe(405);
  expect((await servicesHandler(catalog,async()=>null)(new Request(`http://local/management/v1/environments/${environment}/services`))).status).toBe(401);
 }finally{catalog.close();}
});

test('the Services route answers 409 for an environment that is not ready',async()=>{
 const catalog=new Catalog(':memory:');
 try{
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=servicesHandler(catalog,async()=> 'alice');
  expect((await handler(new Request(`http://local/management/v1/environments/${environment}/services`))).status).toBe(409);
 }finally{catalog.close();}
});

test('open sockets and quiet time: a service is never quiet while a socket is open or within the window of a use, nor right after a restart',()=>{
 let now=1_000_000;
 const activity=new ServiceActivity(undefined,()=>now);
 const R='e_'+'a'.repeat(24),window=30*60_000;
 expect(activity.quietFor(R,'realtime',window)).toBe(false);          // this process has not been up that long
 now+=window+1;
 expect(activity.quietFor(R,'realtime',window)).toBe(true);
 const close=activity.socketOpened(R);
 expect(activity.openSockets(R)).toBe(1);
 now+=10*window;
 expect(activity.quietFor(R,'realtime',window)).toBe(false);          // open for ten windows: not quiet
 close();close();                                                        // a second close changes nothing
 expect(activity.openSockets(R)).toBe(0);
 expect(activity.quietFor(R,'realtime',window)).toBe(false);          // the close is the last use
 now+=window;
 expect(activity.quietFor(R,'realtime',window)).toBe(true);
 activity.touch(R,'functions');
 expect(activity.lastUsed(R,'functions')).toBe(now);
 expect(activity.lastUsed(R,'realtime')).not.toBeNull();
});

test('Edge Functions calls in flight keep the service awake, by the gateway own count',()=>{
 let now=1_000_000,inFlight=1;
 const activity=new ServiceActivity(undefined,()=>now,()=>inFlight);
 const R='e_'+'b'.repeat(24);
 now+=10*60_000;
 expect(activity.quietFor(R,'functions',5*60_000)).toBe(false);
 inFlight=0;
 expect(activity.quietFor(R,'functions',5*60_000)).toBe(true);
});

test('use is written to the catalog at most once a minute and an earlier time never replaces a later one',()=>{
 const {catalog,runtime}=ready();
 try{
  let now=5_000;
  const activity=new ServiceActivity({load:r=>catalog.serviceActivity(r),save:rows=>catalog.saveServiceActivity(rows)},()=>now);
  activity.touch(runtime,'functions');
  expect(catalog.serviceActivity(runtime).functions).toBeUndefined();
  activity.flush();
  expect(catalog.serviceActivity(runtime).functions).toBe(5_000);
  catalog.saveServiceActivity([{runtime,service:'functions',at:1}]);
  expect(catalog.serviceActivity(runtime).functions).toBe(5_000);
  // A later process reads what an earlier one wrote.
  expect(new ServiceActivity({load:r=>catalog.serviceActivity(r),save:()=>{}},()=>now).lastUsed(runtime,'functions')).toBe(5_000);
  // A flush with nothing new writes nothing.
  let writes=0;
  const quiet=new ServiceActivity({load:()=>({}),save:()=>{writes++;}},()=>now);
  quiet.flush();expect(writes).toBe(0);
  quiet.touch(runtime,'realtime');quiet.flush();quiet.flush();expect(writes).toBe(1);
 }finally{catalog.close();}
});

test('the gateway counts a Functions call as use only after the API key was verified, and the gate counts it in flight',async()=>{
 const {catalog,runtime}=ready();
 const keys=new KeyStore(':memory:');
 try{
  const gate=new ConcurrencyGate();
  const activity=new ServiceActivity(undefined,Date.now,r=>gate.inFlight(r,'functions'));
  let release!:(response:Response)=>void;
  const transport:typeof fetch=Object.assign(()=>new Promise<Response>(resolve=>{release=resolve;}),{preconnect:()=>{}});
  const gateway=managedGateway(catalog,keys,()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',functions:{url:'http://functions.invalid'},keys:[],
   anonymousToken:'anon',serviceToken:'service',enabled:true}),transport,gate,activity);
  const bad=await gateway(new Request(`http://local/${runtime}/functions/v1/hello`,{headers:{apikey:'wrong'}}));
  expect(bad.status).toBe(401);
  expect(activity.lastUsed(runtime,'functions')).toBeNull();
  // A preflight and a call without any key reach the runtime or are answered, but nobody verified anything: neither counts as use.
  const preflight=gateway(new Request(`http://local/${runtime}/functions/v1/hello`,{method:'OPTIONS'}));
  expect((await preflight).status).toBe(204);
  const keyless=gateway(new Request(`http://local/${runtime}/functions/v1/hello`,{method:'POST',body:'{}'}));
  await Bun.sleep(20);release(new Response('webhook'));await (await keyless).text();
  expect(activity.lastUsed(runtime,'functions')).toBeNull();
  const key=keys.issue(runtime).token;
  const pending=gateway(new Request(`http://local/${runtime}/functions/v1/hello`,{headers:{apikey:key}}));
  await Bun.sleep(20);
  expect(activity.lastUsed(runtime,'functions')).not.toBeNull();
  expect(activity.inFlightCalls(runtime)).toBe(1);
  release(new Response('ok'));
  expect((await (await pending).text())).toBe('ok');
  await Bun.sleep(5);
  expect(activity.inFlightCalls(runtime)).toBe(0);
 }finally{keys.close();catalog.close();}
});

test('an accepted Realtime socket counts as open until it closes, a refused one never does',async()=>{
 const {serveLocal}=await import('../src/http/local-server');
 const upstream=Bun.serve({port:0,fetch(request,server){if(server.upgrade(request))return;return new Response('no',{status:400});},
  websocket:{open(ws){ws.send('hello');},message(){}}});
 const activity=new ServiceActivity();
 const R='e_'+'c'.repeat(24);
 const listener=await serveLocal(async()=>new Response('plain'),0,{
  isUpgrade:path=>path.includes('/realtime/v1/websocket'),
  upgrade:(path,headers)=>path.includes('apikey=good')?{ok:true,host:'127.0.0.1',port:upstream.port!,onOpen:()=>activity.socketOpened(R),
   head:`GET /socket/websocket HTTP/1.1\r\nHost: tenant.realtime\r\nupgrade: websocket\r\nconnection: Upgrade\r\nsec-websocket-key: ${headers.get('sec-websocket-key')}\r\nsec-websocket-version: 13\r\n\r\n`}
   :{ok:false,status:401,message:'Invalid API key'}});
 try{
  const open=await new Promise<WebSocket>((resolve,reject)=>{
   const socket=new WebSocket(`ws://127.0.0.1:${listener.port}/${R}/realtime/v1/websocket?apikey=good`);
   socket.onmessage=()=>resolve(socket);socket.onerror=()=>reject(new Error('socket failed'));setTimeout(()=>reject(new Error('timeout')),3000);
  });
  expect(activity.openSockets(R)).toBe(1);
  const refused=await new Promise<boolean>(resolve=>{
   const socket=new WebSocket(`ws://127.0.0.1:${listener.port}/${R}/realtime/v1/websocket?apikey=bad`);
   socket.onopen=()=>resolve(false);socket.onerror=()=>resolve(true);socket.onclose=()=>resolve(true);
  });
  expect(refused).toBe(true);
  expect(activity.openSockets(R)).toBe(1);
  open.close();
  for(let wait=0;wait<50&&activity.openSockets(R);wait++)await Bun.sleep(20);
  expect(activity.openSockets(R)).toBe(0);
 }finally{listener.stop(true);upstream.stop(true);}
});

test('without a gateway counter the page says nothing is known, not zero, and a future sample is stale',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  const state=catalog.services('alice',environment);
  const answer=composeServices({state,stats:stats(runtime,10*60_000+10*MIB),now:10*60_000});
  expect(answer.services.find(item=>item.id==='realtime')!.inUse).toBeNull();
  expect(answer.services.find(item=>item.id==='functions')!.inUse).toBeNull();
  expect(answer.stats.stale).toBe(true);
 }finally{catalog.close();}
});

test('Studio failure text reaches a member only as a fixed sentence, never the exception text behind it',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.db.query("INSERT INTO studio_sessions(runtime,desired,state,failure,actor,updated_at) VALUES (?,'running','failed','docker: image sha256:abcdef not found at registry.example',?,?)").run(runtime,'alice',1);
  const failure=catalog.services('carol',environment).studio.failure!;
  expect(failure).not.toContain('sha256');
  expect(failure).toContain('Studio could not start');
 }finally{catalog.close();}
});

test('a socket held open keeps refreshing its last use, so a restart or handover never reads it as idle',()=>{
 const {catalog,runtime}=ready();
 try{
  let now=1_000;
  const activity=new ServiceActivity({load:r=>catalog.serviceActivity(r),save:rows=>catalog.saveServiceActivity(rows)},()=>now);
  const close=activity.socketOpened(runtime);
  now+=8*86_400_000;activity.flush();
  expect(catalog.serviceActivity(runtime).realtime).toBe(now);
  close();
 }finally{catalog.close();}
});
