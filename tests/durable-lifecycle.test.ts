import {test,expect} from 'bun:test';
import {randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managementHandler} from '../src/control/http';
import {runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import {DURABLE_COVERAGE,InventoryRefusal,validateLifecycleCoverage,validateDurableInventory,type LifecycleResource} from '../src/control/lifecycle-contract';
import {durableAdapter,durableInventoryDeriver,runNextDurableOperation,type PythonRun} from '../src/control/durable-lifecycle';

const INSTALLATION='11111111-2222-4333-8444-555555555555';
/** What lab/durable_inventory.py derives for one environment: its containers and its database, logins and tenant. */
function inventoryOf(runtime:string,installation=INSTALLATION,optional:string[]=[]):LifecycleResource[] {
 const container=(service:string):LifecycleResource=>({kind:'durable-container',id:`sbarbase-durable-${runtime}-${service}`,resource:randomUUID(),installation,runtime,service:service as 'auth'});
 return [...['auth','rest',...optional].map(container),{kind:'durable-tenant',id:runtime,resource:randomUUID(),installation,runtime,
  durable:{database:runtime,roles:['auth','rest','storage'].map(role=>`${runtime}_${role}`),storageTenant:runtime}}];
}
function provisioned(catalog:Catalog,project:string,name:string) {
 const environment=catalog.createEnvironment('alice',project,name),job=catalog.claimProvision()!;
 catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
 return {environment,runtime:job.runtime};
}
function fixture(path=':memory:') {
 const catalog=new Catalog(path),installation=catalog.initializeInstallation('bootstrap','alice','Installation');
 const organization=catalog.createOrganization('alice','Customer'),project=catalog.createProject('alice',organization,'Project');
 return {catalog,installation,organization,project,...provisioned(catalog,project,'production')};
}
const stub:LifecycleAdapter=async input=>({outcome:input.action==='inspect'?'present':input.action==='quarantine'?'quarantined':
 ['restore','readiness'].includes(input.action)?'restored':'purged',reclaimedBytes:0,observedBytes:5});
const handlerFor=(catalog:Catalog,keys:KeyStore,actor='alice')=>{
 const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.',keys);
 return (method:string,path:string,body?:unknown)=>handler(new Request(`http://local/management/v1${path}`,{method,
  headers:{authorization:actor,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
};
/** One attempt of the worker, whose failure it logs and survives. */
async function once(catalog:Catalog,adapter:LifecycleAdapter) {
 const errors=console.error;console.error=()=>{};
 try{await runNextDurableOperation(catalog,adapter,new Map(),{retryMs:0});}finally{console.error=errors;}
}
async function drain(catalog:Catalog,adapter:LifecycleAdapter) {
 const attempts=new Map<string,number>();
 while(await runNextDurableOperation(catalog,adapter,attempts,{retryMs:0}));
}

test('a new environment: inventory recorded at provisioning, delete, soft retention, restore, and purge stays refused',async()=>{
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  f.catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime));
  expect(f.catalog.lifecycle('alice',f.environment)!.coverage).toBe(DURABLE_COVERAGE);
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('active');
  const key=keys.issue(f.runtime),call=handlerFor(f.catalog,keys);
  const deleted=await call('DELETE',`/environments/${f.environment}`);
  expect(deleted.status).toBe(200);
  const body=await deleted.json();expect(body.deleted).toBe(true);expect(body.retentionUntil).toBeNumber();
  expect(f.catalog.runtimeReady(f.runtime)).toBe(false);expect(f.catalog.runtimeEpoch(f.runtime)).toBe(1);
  expect(keys.list(f.runtime).find(record=>record.id===key.id)?.revoked_at).toBeNumber();
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleting');
  const calls:string[]=[];
  await drain(f.catalog,async input=>{calls.push(`${input.action}:${input.resource.kind}`);return stub(input);});
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
  // Containers are stopped before the tenant's logins close.
  expect(calls.filter(call=>call.startsWith('quarantine'))).toEqual(['quarantine:durable-container','quarantine:durable-container','quarantine:durable-tenant']);
  expect(f.catalog.retainedEnvironments('alice',f.project).length).toBe(1);
  const operation=randomUUID();
  expect((await call('POST',`/environments/${f.environment}/purge`,{operation})).status).toBe(409);
  expect((await (await call('POST',`/environments/${f.environment}/purge`,{operation:randomUUID()})).json()).message).toBe('Shared writer isolation unavailable');
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
  expect((await call('POST',`/environments/${f.environment}/restore`,{operation:randomUUID()})).status).toBe(202);
  const restored:string[]=[];
  await drain(f.catalog,async input=>{restored.push(`${input.action}:${input.resource.kind}`);return stub(input);});
  expect(restored.filter(call=>call.startsWith('restore'))).toEqual(['restore:durable-tenant','restore:durable-container','restore:durable-container']);
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('active');
  expect(f.catalog.runtimeReady(f.runtime)).toBe(true);
 }finally{keys.close();f.catalog.close();}
});

test('an old environment without a recorded inventory derives it from durable state at delete time',async()=>{
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  const asked:string[][]=[];
  f.catalog.durableInventory=async(runtime,installation)=>{asked.push([runtime,installation]);return inventoryOf(runtime,installation);};
  expect(f.catalog.lifecycle('alice',f.environment)).toBeNull();
  const response=await handlerFor(f.catalog,keys)('DELETE',`/environments/${f.environment}`);
  expect(response.status).toBe(200);
  expect(asked).toEqual([[f.runtime,f.installation]]);
  const row=f.catalog.lifecycle('alice',f.environment)!;
  expect(row.coverage).toBe(DURABLE_COVERAGE);expect(row.resources.length).toBe(3);expect(row.state).toBe('deleting');
  await drain(f.catalog,stub);
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
 }finally{keys.close();f.catalog.close();}
});

test('a delete without a deriver is refused as before and changes nothing',async()=>{
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  const key=keys.issue(f.runtime),response=await handlerFor(f.catalog,keys)('DELETE',`/environments/${f.environment}`);
  expect(response.status).toBe(409);
  expect(await response.json()).toEqual({message:'Exact ownership inventory unavailable'});
  expect(f.catalog.lifecycle('alice',f.environment)).toBeNull();expect(f.catalog.runtimeReady(f.runtime)).toBe(true);
  expect(keys.list(f.runtime).find(record=>record.id===key.id)?.revoked_at).toBeNull();
 }finally{keys.close();f.catalog.close();}
});

for(const [name,derive,reason] of [
 ['a foreign container',async()=>{throw new InventoryRefusal('container_foreign:rest');},'container_foreign:rest'],
 ['an ambiguous login',async()=>{throw new InventoryRefusal('role_unknown');},'role_unknown'],
 ['an unreachable observer',async()=>{throw new Error('docker unavailable with a secret');},'observation_unavailable'],
 ['an inventory naming another environment',async(runtime:string)=>inventoryOf('e_'+'f'.repeat(24)),'inventory_invalid'],
 ['an inventory with an unknown service',async(runtime:string)=>{const items=inventoryOf(runtime);items[0]={...items[0]!,service:'db' as any};return items;},'inventory_invalid'],
] as [string,(runtime:string)=>Promise<LifecycleResource[]>,string][])
 test(`refuses with a precise reason and changes nothing: ${name}`,async()=>{
  const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
  try{
   const neighbor=provisioned(f.catalog,f.project,'neighbor');
   f.catalog.registerDurableInventory(neighbor.runtime,inventoryOf(neighbor.runtime));
   const before=JSON.stringify(f.catalog.lifecycle('alice',neighbor.environment));
   f.catalog.durableInventory=derive;const key=keys.issue(f.runtime);
   const response=await handlerFor(f.catalog,keys)('DELETE',`/environments/${f.environment}`);
   expect(response.status).toBe(409);
   expect(await response.json()).toEqual({message:'Exact ownership inventory unavailable',reason});
   expect(f.catalog.lifecycle('alice',f.environment)).toBeNull();
   expect(f.catalog.runtimeReady(f.runtime)).toBe(true);expect(f.catalog.runtimeEpoch(f.runtime)).toBe(0);
   expect(keys.list(f.runtime).find(record=>record.id===key.id)?.revoked_at).toBeNull();
   expect(JSON.stringify(f.catalog.lifecycle('alice',neighbor.environment))).toBe(before);
  }finally{keys.close();f.catalog.close();}
 });

test('a recorded inventory does not outlive the facts: a refusal at delete time wins, a changed one is replaced, an equal one is kept',async()=>{
 const f=fixture();
 try{
  const recorded=inventoryOf(f.runtime);f.catalog.registerDurableInventory(f.runtime,recorded);
  f.catalog.durableInventory=async()=>{throw new InventoryRefusal('container_foreign:auth');};
  await expect(f.catalog.prepareDeletion('alice',f.environment)).rejects.toThrow('Exact ownership inventory unavailable');
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('active');
  f.catalog.durableInventory=async()=>recorded;
  await f.catalog.prepareDeletion('alice',f.environment);
  expect(f.catalog.lifecycle('alice',f.environment)!.resources).toEqual(recorded);
  const grown=inventoryOf(f.runtime,INSTALLATION,['realtime']);
  f.catalog.durableInventory=async()=>grown;
  await f.catalog.prepareDeletion('alice',f.environment);
  expect(f.catalog.lifecycle('alice',f.environment)!.resources.length).toBe(4);
  f.catalog.deleteEnvironment('alice',f.environment);
  expect(()=>f.catalog.registerDurableInventory(f.runtime,recorded)).toThrow('Lifecycle operation is active');
 }finally{f.catalog.close();}
});

test('an inventory of another coverage is never replaced by a derived one',async()=>{
 const f=fixture();
 try{
  f.catalog.registerLifecycleResources(f.runtime,[{kind:'container',id:'a'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:f.runtime}],'disposable-fixture');
  expect(()=>f.catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime))).toThrow('Ownership inventory is immutable');
  let asked=0;f.catalog.durableInventory=async()=>{asked++;return inventoryOf(f.runtime);};
  await f.catalog.prepareDeletion('alice',f.environment);
  expect(asked).toBe(0);expect(f.catalog.lifecycle('alice',f.environment)!.coverage).toBe('disposable-fixture');
 }finally{f.catalog.close();}
});

test('prepareDeletion refuses what deleteEnvironment refuses, before any derivation',async()=>{
 const f=fixture();
 try{
  let asked=0;f.catalog.durableInventory=async()=>{asked++;return inventoryOf(f.runtime);};
  const queued=f.catalog.createEnvironment('alice',f.project,'queued');
  await expect(f.catalog.prepareDeletion('alice',queued)).rejects.toThrow('Provisioning is active');
  await expect(f.catalog.prepareDeletion('bob',f.environment)).rejects.toThrow('Forbidden');
  f.catalog.requestRealtime('alice',f.environment,true);
  await expect(f.catalog.prepareDeletion('alice',f.environment)).rejects.toThrow('Environment services are still on');
  expect(asked).toBe(0);expect(f.catalog.lifecycle('alice',f.environment)).toBeNull();
 }finally{f.catalog.close();}
});

test('a crash at every adapter call of delete and of restore leaves a revoked, resumable environment, never a half deleted one',async()=>{
 const root=mkdtempSync(join(tmpdir(),'durable-lifecycle-')),path=join(root,'catalog.sqlite');
 try{
  const probe=fixture(join(root,'probe.sqlite'));
  probe.catalog.registerDurableInventory(probe.runtime,inventoryOf(probe.runtime));probe.catalog.deleteEnvironment('alice',probe.environment);
  let total=0;await drain(probe.catalog,async input=>{total++;return stub(input);});probe.catalog.close();
  expect(total).toBe(6);
  for(let crash=1;crash<=total;crash++)for(const phase of ['before','after'] as const){
   const f=fixture(join(root,`c${crash}${phase}.sqlite`));let catalog=f.catalog;
   try{
    catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime));catalog.deleteEnvironment('alice',f.environment);
    let calls=0;const effects:string[]=[];
    const crashing:LifecycleAdapter=async input=>{
     calls++;if(calls===crash&&phase==='before')throw new Error('crash');
     const result=await stub(input);if(input.action!=='inspect')effects.push(input.resource.resource);
     if(calls===crash&&phase==='after')throw new Error('crash');return result;
    };
    await once(catalog,crashing);
    // After the crash: still deleting, access revoked, not deleted, and the intent names the same operation.
    const stuck=catalog.lifecycle('alice',f.environment)!;
    expect(stuck.state).toBe('deleting');expect(catalog.runtimeDeleted(f.runtime)).toBe(true);expect(catalog.runtimeReady(f.runtime)).toBe(false);
    // The process dies and restarts on the same file.
    const operation=stuck.operation;catalog.close();catalog=new Catalog(join(root,`c${crash}${phase}.sqlite`));
    expect(catalog.lifecycle('alice',f.environment)!.operation).toBe(operation);
    await drain(catalog,async input=>{const result=await stub(input);if(input.action!=='inspect')effects.push(input.resource.resource);return result;});
    const done=catalog.lifecycle('alice',f.environment)!;
    expect(done.state).toBe('deleted');expect(done.operation).toBe(operation);
    expect(done.effects.every(effect=>effect.state==='done')).toBe(true);expect(done.effects.length).toBe(3);
    // Restore, interrupted the same way, resumes to active.
    catalog.restoreEnvironment('alice',f.environment,randomUUID());
    let restoreCalls=0;
    await once(catalog,async input=>{restoreCalls++;if(restoreCalls===crash)throw new Error('crash');return stub(input);});
    expect(['restoring','active']).toContain(catalog.lifecycle('alice',f.environment)!.state);
    await drain(catalog,stub);
    expect(catalog.lifecycle('alice',f.environment)!.state).toBe('active');
   }finally{catalog.close();}
  }
 }finally{rmSync(root,{recursive:true});}
},120_000);

test('other environments are untouched: only the deleted runtime reaches an adapter, and the neighbor stays active',async()=>{
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  const neighbor=provisioned(f.catalog,f.project,'neighbor');
  f.catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime));f.catalog.registerDurableInventory(neighbor.runtime,inventoryOf(neighbor.runtime));
  const before=JSON.stringify(f.catalog.lifecycle('alice',neighbor.environment)),neighborKey=keys.issue(neighbor.runtime);
  expect((await handlerFor(f.catalog,keys)('DELETE',`/environments/${f.environment}`)).status).toBe(200);
  const seen=new Set<string>(),names:string[]=[];
  await drain(f.catalog,async input=>{seen.add(input.runtime);names.push(input.resource.id);return stub(input);});
  expect([...seen]).toEqual([f.runtime]);
  expect(names.every(name=>name.includes(f.runtime))).toBe(true);expect(names.some(name=>name.includes(neighbor.runtime))).toBe(false);
  expect(JSON.stringify(f.catalog.lifecycle('alice',neighbor.environment))).toBe(before);
  expect(f.catalog.runtimeReady(neighbor.runtime)).toBe(true);expect(f.catalog.runtimeEpoch(neighbor.runtime)).toBe(0);
  expect(keys.list(neighbor.runtime).find(record=>record.id===neighborKey.id)?.revoked_at).toBeNull();
  expect(f.catalog.listEnvironments('alice',f.project).map(item=>item.id)).toEqual([neighbor.environment]);
 }finally{keys.close();f.catalog.close();}
});

test('each worker takes only its own operations: durable and other coverage are separate queues',()=>{
 const f=fixture();
 try{
  const other=provisioned(f.catalog,f.project,'fixture');
  f.catalog.registerLifecycleResources(other.runtime,[{kind:'container',id:'b'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:other.runtime}],'disposable-fixture');
  f.catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime));
  f.catalog.deleteEnvironment('alice',other.environment);f.catalog.deleteEnvironment('alice',f.environment);
  expect(f.catalog.nextLifecycleOperation('durable')!.environment).toBe(f.environment);
  expect(f.catalog.nextLifecycleOperation('other')!.environment).toBe(other.environment);
 }finally{f.catalog.close();}
});

test('a blocked durable operation keeps its failure and is asked again only after the retry window',async()=>{
 const f=fixture();
 try{
  f.catalog.registerDurableInventory(f.runtime,inventoryOf(f.runtime));f.catalog.deleteEnvironment('alice',f.environment);
  let calls=0,time=1000;const attempts=new Map<string,number>(),broken:LifecycleAdapter=async()=>{calls++;throw new Error('Resource adapter refused');};
  const errors=console.error;console.error=()=>{};
  try{
   expect(await runNextDurableOperation(f.catalog,broken,attempts,{retryMs:60_000,now:()=>time})).toBe(true);
   expect(f.catalog.lifecycle('alice',f.environment)!.failure).toBe('Resource reconciliation required');
   expect(await runNextDurableOperation(f.catalog,broken,attempts,{retryMs:60_000,now:()=>time+59_000})).toBe(false);
   expect(calls).toBe(1);
   expect(await runNextDurableOperation(f.catalog,broken,attempts,{retryMs:60_000,now:()=>time+61_000})).toBe(true);
   expect(calls).toBe(2);expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleting');
  }finally{console.error=errors;}
 }finally{f.catalog.close();}
});

test('the durable inventory contract admits exactly one environment\'s containers and tenant',()=>{
 const runtime='e_'+'a'.repeat(24),other='e_'+'b'.repeat(24),good=inventoryOf(runtime);
 expect(()=>validateLifecycleCoverage(runtime,good,DURABLE_COVERAGE)).not.toThrow();
 const bad:[string,LifecycleResource[]][]=[
  ['another environment\'s container',[{...good[0]!,id:`sbarbase-durable-${other}-auth`},...good.slice(1)]],
  ['a container of another runtime',[{...good[0]!,runtime:other},...good.slice(1)]],
  ['a duplicated service',[good[0]!,{...good[0]!,resource:randomUUID()},good[2]!]],
  ['an unknown service',[good[0]!,{...good[1]!,service:'db' as any,id:`sbarbase-durable-${runtime}-db`},good[2]!]],
  ['a missing rest container',[good[0]!,good[2]!,{...good[0]!,service:'realtime',id:`sbarbase-durable-${runtime}-realtime`,resource:randomUUID()}]],
  ['two tenants',[...good,{...good[2]!,resource:randomUUID()}]],
  ['no tenant',good.slice(0,2).concat(inventoryOf(runtime,INSTALLATION,['realtime','functions']).slice(2,4))],
  ['a login of another environment',[good[0]!,good[1]!,{...good[2]!,durable:{...good[2]!.durable!,roles:[...good[2]!.durable!.roles,`${other}_auth`]}}]],
  ['a tenant of another database',[good[0]!,good[1]!,{...good[2]!,durable:{...good[2]!.durable!,database:other}}]],
  ['a missing required login',[good[0]!,good[1]!,{...good[2]!,durable:{...good[2]!.durable!,roles:[`${runtime}_auth`]}}]],
  ['an extra field',[good[0]!,good[1]!,{...good[2]!,marker:'x'} as any]],
  ['two installations',[good[0]!,{...good[1]!,installation:randomUUID()},good[2]!]],
  ['a plain container kind',[{kind:'container',id:'a'.repeat(64),installation:INSTALLATION,resource:randomUUID(),runtime},...good.slice(1)]],
 ];
 for(const [name,resources] of bad)expect(()=>validateDurableInventory(runtime,resources),name).toThrow('Invalid ownership inventory');
 // The other coverages admit none of these kinds.
 expect(()=>validateLifecycleCoverage(runtime,good,'dedicated-resources')).toThrow();
 expect(()=>validateLifecycleCoverage(runtime,good,'disposable-fixture')).toThrow();
});

test('the deriver and adapter speak to the python tools in the contract, refusing anything else',async()=>{
 const runtime='e_'+'a'.repeat(24),good=inventoryOf(runtime);
 const reply=(value:unknown,code=0):PythonRun=>async()=>({code,stdout:typeof value==='string'?value:JSON.stringify(value),stderr:''});
 const derive=(run:PythonRun)=>durableInventoryDeriver('.',run)(runtime,INSTALLATION);
 expect(await derive(reply({ok:true,coverage:DURABLE_COVERAGE,resources:good}))).toEqual(good);
 for(const [value,expected] of [[{ok:false,reason:'container_foreign:rest'},'container_foreign:rest'],[{ok:false,reason:'Bearer secret value'},'observation_unavailable'],
  ['not json','observation_unavailable'],[{ok:true,coverage:'dedicated-resources',resources:good},'observation_unavailable'],[null,'observation_unavailable']] as const)
  await expect(derive(reply(value as any))).rejects.toMatchObject({reason:expected});
 let seen:string[]=[];
 const adapter=(run:PythonRun)=>durableAdapter('.','/catalog.sqlite',run);
 const input={action:'quarantine' as const,resource:good[0]!,runtime,epoch:1,operation:randomUUID()};
 expect(await adapter(async(script,args,body)=>{seen=[script,...args,body!];return {code:0,stdout:'{"outcome":"quarantined","reclaimedBytes":0}',stderr:''};})(input)).toEqual({outcome:'quarantined',reclaimedBytes:0});
 expect(seen[0]!.endsWith('/lab/durable_lifecycle.py')).toBe(true);expect(seen.slice(1,3)).toEqual(['--catalog','/catalog.sqlite']);expect(JSON.parse(seen[3]!).resource).toEqual(good[0]);
 await expect(adapter(reply('{}',1))(input)).rejects.toThrow('Resource adapter refused');
 await expect(adapter(reply({outcome:'deleted'}))(input)).rejects.toThrow('Invalid resource adapter result');
});

test('the python deriver and the TypeScript contract agree on the same inventory',()=>{
 const runtime='e_'+'a1'.repeat(12);
 const script=`import json, durable_inventory as i, test_durable_inventory as t\nhost=t.FakeHost(('${runtime}',))\nhost.add_container(i.container_name('${runtime}','realtime'),running=False)\nprint(json.dumps(i.derive('${runtime}','${INSTALLATION}',host)))`;
 const child=spawnSync('/usr/bin/python3',['-c',script],{cwd:join(import.meta.dir,'../lab'),encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',TMPDIR:'/tmp'}});
 expect(child.status).toBe(0);
 const value=JSON.parse(child.stdout);
 expect(value.coverage).toBe(DURABLE_COVERAGE);
 expect(()=>validateLifecycleCoverage(runtime,value.resources,value.coverage)).not.toThrow();
 expect(value.resources.map((item:LifecycleResource)=>item.service??item.kind)).toEqual(['auth','rest','realtime','durable-tenant']);
});
