import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {realtimeHandler} from '../src/control/realtime';
import {functionsHandler} from '../src/control/functions';
import {installationServicesHandler} from '../src/control/services';
import {parseServiceChange} from '../src/control/service-mode-http';

function ready() {
 const catalog=new Catalog(':memory:');
 catalog.initializeInstallation('op-1','alice','Home');
 catalog.setInstallationServices('alice',{defaultMode:'off',realtimeOnDemand:true});
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 return {catalog,environment,runtime:job.runtime};
}

test('the body of a mode change: the old enabled form, or a mode with an optional quiet period, and nothing else',()=>{
 expect(parseServiceChange({enabled:true})).toEqual({kind:'enabled',enabled:true});
 expect(parseServiceChange({enabled:false})).toEqual({kind:'enabled',enabled:false});
 expect(parseServiceChange({mode:'on_demand',idle_minutes:30})).toEqual({kind:'mode',mode:'on_demand',idle:30});
 expect(parseServiceChange({mode:'off'})).toEqual({kind:'mode',mode:'off',idle:undefined});
 for(const bad of [null,[],'on',{},{enabled:'true'},{enabled:true,mode:'on'},{mode:'sometimes'},{mode:'on',extra:1},{mode:'on_demand',idle_minutes:4},{mode:'on_demand',idle_minutes:'30'},{mode:'on_demand',idle_minutes:99999},{mode:'on_demand',idle_minutes:7.5}])
  expect(parseServiceChange(bad)).toBeNull();
});

test('Realtime: the old on and off body still works for old clients, a mode is chosen with the new body, and the answer carries it',async()=>{
 const {catalog,environment,runtime}=ready();
 try{
  const handler=realtimeHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor:string,method:string,body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/realtime`,{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const first=await (await call('alice','GET')).json() as {data:{mode:string;idle_minutes:number;kill_switch:boolean}};
  expect(first.data).toMatchObject({mode:'off',idle_minutes:30,kill_switch:false});
  expect((await call('alice','PUT',{mode:'on_demand',idle_minutes:12})).status).toBe(202);
  const second=await (await call('alice','GET')).json() as {data:{mode:string;idle_minutes:number;state:string}};
  expect(second.data).toMatchObject({mode:'on_demand',idle_minutes:12,state:'off'});         // nothing was started
  expect((await call('carol','PUT',{mode:'on'})).status).toBe(403);
  expect((await call('alice','PUT',{mode:'on_demand',idle_minutes:2})).status).toBe(400);
  expect((await call('alice','PUT',{mode:'on',enabled:true})).status).toBe(400);
  // An old client that sends enabled:true makes the choice On, as it always has.
  expect((await call('alice','PUT',{enabled:true})).status).toBe(202);
  expect(catalog.serviceMode(runtime,'realtime').mode).toBe('on');
  expect((await call('alice','PUT',{mode:'off'})).status).toBe(409);                            // a change is already in progress
 }finally{catalog.close();}
});

test('Realtime On demand is refused while an operator has not allowed it',async()=>{
 const {catalog,environment}=ready();
 try{
  catalog.setInstallationServices('alice',{realtimeOnDemand:false});
  const handler=realtimeHandler(catalog,async()=> 'alice');
  const response=await handler(new Request(`http://local/management/v1/environments/${environment}/realtime`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'on_demand'})}));
  expect(response.status).toBe(400);
  const off=await handler(new Request(`http://local/management/v1/environments/${environment}/realtime`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'on'})}));
  expect(off.status).toBe(202);
 }finally{catalog.close();}
});

test('Edge Functions: a deploy turns them on only when they are Off, reports it, and never changes an On demand choice',async()=>{
 const {catalog,environment,runtime}=ready();
 const root=mkdtempSync(join(tmpdir(),'fn-mode-'));
 try{
  const handler=functionsHandler(catalog,async()=> 'alice',join(root,'code'),join(root,'secrets'));
  const deploy=(name:string)=>handler(new Request(`http://local/management/v1/environments/${environment}/functions/${name}`,{method:'PUT',headers:{'content-type':'application/json'},
   body:JSON.stringify({files:{'index.ts':'Deno.serve(()=>new Response("hi"))'}})}));
  const first=await deploy('one');
  expect(first.status).toBe(201);
  expect(((await first.json()) as {data:{deployed:{turned_on:boolean}}}).data.deployed.turned_on).toBe(true);
  expect(catalog.serviceMode(runtime,'functions').mode).toBe('on');
  catalog.db.query("UPDATE functions_settings SET state='off',desired='off'").run();
  catalog.setServiceMode('alice',environment,'functions','on_demand');
  const second=await deploy('two');
  expect(((await second.json()) as {data:{deployed:{turned_on:boolean}}}).data.deployed.turned_on).toBe(false);
  expect(catalog.serviceMode(runtime,'functions').mode).toBe('on_demand');
  expect(catalog.db.query<{state:string;desired:string},[]>('SELECT state,desired FROM functions_settings').get()).toEqual({state:'off',desired:'off'});
  const state=await (await handler(new Request(`http://local/management/v1/environments/${environment}/functions`,{headers:{authorization:'alice'}}))).json() as {data:{mode:string}};
  expect(state.data.mode).toBe('on_demand');
  const changed=await handler(new Request(`http://local/management/v1/environments/${environment}/functions`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'on_demand',idle_minutes:25})}));
  expect(changed.status).toBe(202);
  expect(catalog.serviceMode(runtime,'functions').idleMinutes).toBe(25);
  expect((await handler(new Request(`http://local/management/v1/environments/${environment}/functions`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'sometimes'})}))).status).toBe(400);
 }finally{rmSync(root,{recursive:true,force:true});catalog.close();}
});

test('the installation route takes only the known settings in the right shape, for operators only',async()=>{
 const {catalog}=ready();
 try{
  const handler=installationServicesHandler(catalog,async request=>request.headers.get('authorization'));
  const put=(actor:string,body:unknown)=>handler(new Request('http://local/management/v1/installation/services',{method:'PUT',headers:{authorization:actor,'content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put('carol',{killSwitch:true})).status).toBe(403);
  for(const bad of [{},{killSwitch:'yes'},{defaultMode:'always'},{idleMinutes:{realtime:'30'}},{idleMinutes:{cron:30}},{idleMinutes:[]},{realtimeOnDemand:1},{other:1},{idleMinutes:{realtime:4}},{unusedDays:0}])
   expect([400]).toContain((await put('alice',bad)).status);
  const good=await put('alice',{killSwitch:true,defaultMode:'on_demand',realtimeOnDemand:false,idleMinutes:{realtime:45,functions:20},unusedDays:10});
  expect(good.status).toBe(200);
  expect(((await good.json()) as {data:Record<string,unknown>}).data).toMatchObject({killSwitch:true,defaultMode:'on_demand',realtimeOnDemand:false,idleMinutes:{realtime:45,functions:20},unusedDays:10});
 }finally{catalog.close();}
});
