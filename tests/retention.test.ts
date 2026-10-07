import {test,expect} from 'bun:test';
import {randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {enrollLifecycleResources,runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import {RETENTION_MS,validateLifecycleCoverage,type LifecycleResource} from '../src/control/lifecycle-contract';

function fixture(path=':memory:'){
 const catalog=new Catalog(path);catalog.initializeInstallation('bootstrap','alice','Installation');
 const organization=catalog.createOrganization('alice','Customer'),project=catalog.createProject('alice',organization,'Project');
 const environment=catalog.createEnvironment('alice',project,'production'),job=catalog.claimProvision()!;
 catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
 const resource:LifecycleResource={kind:'container',id:'a'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:job.runtime};
 catalog.registerLifecycleResources(job.runtime,[resource],'disposable-fixture');
 return {catalog,organization,project,environment,job,resource};
}
const adapter:LifecycleAdapter=async input=>({outcome:input.action==='inspect'?'present':input.action==='quarantine'?'quarantined':
 ['restore','readiness'].includes(input.action)?'restored':'purged',reclaimedBytes:0,observedBytes:17});

async function deletedSibling(catalog:Catalog,actor:string){
 const organization=catalog.createOrganization(actor,'Sibling organization');
 const project=catalog.createProject(actor,organization,'Sibling project');
 const environment=catalog.createEnvironment(actor,project,'Sibling environment'),job=catalog.claimProvision()!;
 catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
 catalog.registerLifecycleResources(job.runtime,[{kind:'container',id:'b'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:job.runtime}],'disposable-fixture');
 catalog.deleteEnvironment(actor,environment);
 await runLifecycleOperation(catalog,adapter,catalog.lifecycle(actor,environment)!);
 return environment;
}

for(const actor of ['alice','bob'])test(`pending lifecycle operation identity cannot be reused by ${actor} in another environment`,async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const sibling=await deletedSibling(f.catalog,actor),operation=randomUUID();
  const first=f.catalog.restoreEnvironment('alice',f.environment,operation);
  expect(first.effects).toHaveLength(0);
  expect(f.catalog.restoreEnvironment('alice',f.environment,operation).operation).toBe(operation);
  expect(()=>f.catalog.restoreEnvironment(actor,sibling,operation)).toThrow('Lifecycle operation identity was already used');
  expect(f.catalog.lifecycle(actor,sibling)!.state).toBe('deleted');
  expect(f.catalog.lifecycle(actor,sibling)!.operation).not.toBe(operation);
 }finally{f.catalog.close();}
});

test('pending lifecycle operation identity remains reserved after Catalog restart',async()=>{
 const root=mkdtempSync(join(tmpdir(),'lifecycle-operation-')),path=join(root,'catalog.sqlite');
 const f=fixture(path);let catalog=f.catalog;
 try{
  catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(catalog,adapter,catalog.lifecycle('alice',f.environment)!);
  const sibling=await deletedSibling(catalog,'bob'),operation=randomUUID();
  catalog.restoreEnvironment('alice',f.environment,operation);catalog.close();catalog=new Catalog(path);
  expect(catalog.lifecycle('alice',f.environment)!.effects).toHaveLength(0);
  expect(catalog.restoreEnvironment('alice',f.environment,operation).operation).toBe(operation);
  expect(()=>catalog.restoreEnvironment('bob',sibling,operation)).toThrow('Lifecycle operation identity was already used');
  expect(catalog.lifecycle('bob',sibling)!.state).toBe('deleted');
 }finally{catalog.close();rmSync(root,{recursive:true});}
});

test('soft deletion retains hierarchy and receipts, blocks controls, preserves a seven day retention',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  const row=f.catalog.lifecycle('alice',f.environment)!;
  expect(row.epoch).toBe(1);expect(row.retain_until!-row.deleted_at!).toBe(RETENTION_MS);
  expect(f.catalog.listEnvironments('alice',f.project)).toEqual([]);
  expect(f.catalog.retainedEnvironments('alice',f.project).length).toBe(1);
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(false);
  expect(()=>f.catalog.requestRealtime('alice',f.environment,true)).toThrow('Forbidden');
  expect(()=>f.catalog.deleteProject('alice',f.project)).toThrow('Project has environments');
  expect(()=>f.catalog.applyProvisionReceipt(f.environment,f.job.runtime,f.job.claim!,f.job.attempt,0)).not.toThrow();
  await runLifecycleOperation(f.catalog,adapter,row);
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
 }finally{f.catalog.close();}
});

test('restore remains unavailable until adapter readiness passes and old runtime epoch stays revoked',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const operation=randomUUID(),row=f.catalog.restoreEnvironment('alice',f.environment,operation);
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(false);
  const broken:LifecycleAdapter=async input=>{if(input.action==='restore')throw new Error('unhealthy');return adapter(input);};
  await expect(runLifecycleOperation(f.catalog,broken,row)).rejects.toThrow();
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(false);
  expect(()=>f.catalog.purgeEnvironment('alice',f.environment,randomUUID())).toThrow('Lifecycle operation is active');
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(true);
  expect(f.catalog.runtimeEpoch(f.job.runtime)).toBe(1);
  expect(f.catalog.restoreEnvironment('alice',f.environment,operation).state).toBe('active');
  f.catalog.deleteEnvironment('alice',f.environment);expect(f.catalog.runtimeEpoch(f.job.runtime)).toBe(2);
 }finally{f.catalog.close();}
});

test('quarantine crash reconciliation resumes the exact operation and refuses absent retained data',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);const row=f.catalog.lifecycle('alice',f.environment)!;
  const interrupted:LifecycleAdapter=async input=>{if(input.action==='quarantine')throw new Error('crash');return adapter(input);};
  await expect(runLifecycleOperation(f.catalog,interrupted,row)).rejects.toThrow();
  const pending=f.catalog.lifecycle('alice',f.environment)!;
  expect(pending.operation).toBe(row.operation);expect(pending.effects[0]?.state).toBe('pending');
  const absent:LifecycleAdapter=async()=>({outcome:'absent',reclaimedBytes:0});
  await expect(runLifecycleOperation(f.catalog,absent,pending)).rejects.toThrow('Retained resource is missing');
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
 }finally{f.catalog.close();}
});

test('purge refuses before retention expires even with exact resource enrollment',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect(()=>f.catalog.purgeEnvironment('alice',f.environment,randomUUID())).toThrow('Retention has not expired');
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
 }finally{f.catalog.close();}
});

test('restoration cannot revive old managed API keys after a skipped separate store revocation',async()=>{
 const {KeyStore}=await import('../src/control/keys');
 const {managedGateway}=await import('../src/gateway/managed');
 const f=fixture(),keys=new KeyStore(':memory:');
 const gateway=managedGateway(f.catalog,keys,()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',keys:[],anonymousToken:'anon',enabled:true}),
  Object.assign(async()=>new Response('{}'),{preconnect:()=>{}}));
 const request=(key:string)=>gateway(new Request(`http://local/${f.job.runtime}/rest/v1/items`,{headers:{apikey:key}}));
 try{
  const old=keys.issue(f.job.runtime).token;expect((await request(old)).status).toBe(200);
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  f.catalog.restoreEnvironment('alice',f.environment,randomUUID());
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect((await request(old)).status).toBe(401);
  const fresh=keys.issue(f.job.runtime).token;expect((await request(fresh)).status).toBe(200);
 }finally{keys.close();f.catalog.close();}
});

test('an in-flight upstream wait loses its captured epoch across delete and restore',async()=>{
 const {KeyStore}=await import('../src/control/keys');
 const {managedGateway}=await import('../src/gateway/managed');
 const f=fixture(),keys=new KeyStore(':memory:');let release!:(response:Response)=>void,entered!:()=>void;
 const started=new Promise<void>(resolve=>{entered=resolve;});
 const transport:typeof fetch=Object.assign(async()=>{entered();return new Promise<Response>(resolve=>{release=resolve;});},{preconnect:()=>{}});
 const gateway=managedGateway(f.catalog,keys,()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',keys:[],anonymousToken:'anon',enabled:true}),transport);
 try{
  const key=keys.issue(f.job.runtime).token;
  const pending=gateway(new Request(`http://local/${f.job.runtime}/rest/v1/items`,{headers:{apikey:key}}));
  await started;f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  f.catalog.restoreEnvironment('alice',f.environment,randomUUID());
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  release(new Response('private'));expect((await pending).status).toBe(401);
 }finally{keys.close();f.catalog.close();}
});

test('revoked initiating owner authority blocks queued resource effects',async()=>{
 const f=fixture();try{
  f.catalog.setMember('alice',f.organization,'bob','owner');
  f.catalog.deleteEnvironment('alice',f.environment);const row=f.catalog.lifecycle('alice',f.environment)!;
  f.catalog.setMember('bob',f.organization,'alice',null);
  let calls=0;const observed:LifecycleAdapter=async input=>{calls++;return adapter(input);};
  await expect(runLifecycleOperation(f.catalog,observed,row)).rejects.toThrow('Lifecycle authority revoked');
  expect(calls).toBe(0);
  expect(f.catalog.lifecycle('bob',f.environment)!.failure).toBe('Lifecycle authority revoked');
 }finally{f.catalog.close();}
});

test('revoked management epoch blocks queued effects even when organization ownership remains',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);const row=f.catalog.lifecycle('alice',f.environment)!;
  f.catalog.managementSecurity.revoke('alice');let calls=0;
  const observed:LifecycleAdapter=async input=>{calls++;return adapter(input);};
  await expect(runLifecycleOperation(f.catalog,observed,row)).rejects.toThrow('Lifecycle authority revoked');
  expect(calls).toBe(0);
 }finally{f.catalog.close();}
});

test('unenrolled deletion refuses before revoking readiness or runtime epoch',()=>{
 const catalog=new Catalog(':memory:');try{
  const organization=catalog.createOrganization('alice','A'),project=catalog.createProject('alice',organization,'P');
  const environment=catalog.createEnvironment('alice',project,'production'),job=catalog.claimProvision()!;
  catalog.finishProvision(environment,job.claim!,true);
  expect(()=>catalog.deleteEnvironment('alice',environment)).toThrow('Exact ownership inventory unavailable');
  expect(catalog.runtimeReady(job.runtime)).toBe(true);expect(catalog.runtimeEpoch(job.runtime)).toBe(0);
  expect(catalog.lifecycle('alice',environment)).toBeNull();
 }finally{catalog.close();}
});

test('an unauthorized admin restore cannot mutate key revocation metadata',async()=>{
 const {KeyStore}=await import('../src/control/keys');
 const {managementHandler}=await import('../src/control/http');
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  f.catalog.setMember('alice',f.organization,'adam','admin');
  const old=keys.issue(f.job.runtime);
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const handler=managementHandler(f.catalog,async request=>request.headers.get('authorization'),'.',keys);
  const response=await handler(new Request(`http://local/management/v1/environments/${f.environment}/restore`,{method:'POST',
   headers:{authorization:'adam','content-type':'application/json'},body:JSON.stringify({operation:randomUUID()})}));
  expect(response.status).toBe(403);
  expect(keys.list(f.job.runtime).find(record=>record.id===old.id)?.revoked_at).toBeNull();
  expect(f.catalog.lifecycle('alice',f.environment)!.state).toBe('deleted');
 }finally{keys.close();f.catalog.close();}
});

test('a duplicate completed restore preserves newly issued keys from the active epoch',async()=>{
 const {KeyStore}=await import('../src/control/keys');
 const {managementHandler}=await import('../src/control/http');
 const f=fixture(),keys=new KeyStore(':memory:');keys.useRuntimeEpoch(runtime=>f.catalog.runtimeEpoch(runtime));
 try{
  const old=keys.issue(f.job.runtime);
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const operation=randomUUID(),handler=managementHandler(f.catalog,async request=>request.headers.get('authorization'),'.',keys);
  const restore=()=>handler(new Request(`http://local/management/v1/environments/${f.environment}/restore`,{method:'POST',
   headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify({operation})}));
  expect((await restore()).status).toBe(202);
  expect(keys.list(f.job.runtime).find(record=>record.id===old.id)?.revoked_at).toBeNumber();
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const fresh=keys.issue(f.job.runtime);
  expect(keys.resolve(f.job.runtime,fresh.token)).toBe('publishable');
  expect((await restore()).status).toBe(202);
  expect(keys.list(f.job.runtime).find(record=>record.id===fresh.id)?.revoked_at).toBeNull();
  expect(keys.resolve(f.job.runtime,fresh.token)).toBe('publishable');
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(true);
 }finally{keys.close();f.catalog.close();}
});

test('retired gateway shares remain reserved through restoration',async()=>{
 const f=fixture();try{
  const neighbor=f.catalog.createEnvironment('alice',f.project,'neighbor'),job=f.catalog.claimProvision()!;
  f.catalog.finishProvision(neighbor,job.claim!,true);
  f.catalog.setGatewayShare('alice',f.environment,24);
  expect(f.catalog.gatewayShareState('alice',neighbor).allocated).toBe(32);
  f.catalog.deleteEnvironment('alice',f.environment);
  expect(()=>f.catalog.setGatewayShare('alice',neighbor,24)).toThrow('Shares exceed gateway capacity');
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect(f.catalog.gatewayShareState('alice',neighbor).allocated).toBe(32);
  f.catalog.restoreEnvironment('alice',f.environment,randomUUID());
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  expect(f.catalog.gatewayShareState('alice',neighbor).allocated).toBe(32);
  expect(f.catalog.gatewayShareState('alice',f.environment).share).toBe(24);
 }finally{f.catalog.close();}
});

test('restore establishes effects once and finishes with read-only resource readiness checks',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('alice',f.environment)!);
  const calls:string[]=[],observed:LifecycleAdapter=async input=>{calls.push(input.action);return adapter(input);};
  f.catalog.restoreEnvironment('alice',f.environment,randomUUID());
  await runLifecycleOperation(f.catalog,observed,f.catalog.lifecycle('alice',f.environment)!);
  expect(calls).toEqual(['inspect','restore','readiness']);
 }finally{f.catalog.close();}
});

test('complete shared enrollment refuses before resource inspection or catalog enrollment',async()=>{
 const f=fixture();try{
  let calls=0;const observed:LifecycleAdapter=async input=>{calls++;return adapter(input);};
  await expect(enrollLifecycleResources(f.catalog,observed,f.job.runtime,[f.resource],'complete-shared-resources'))
   .rejects.toThrow('Shared writer isolation unavailable');
  expect(calls).toBe(0);
  expect(f.catalog.lifecycle('alice',f.environment)!.coverage).toBe('disposable-fixture');
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(true);
 }finally{f.catalog.close();}
});

test('an unsupported shared operation refuses before adapter effects',async()=>{
 const f=fixture();try{
  f.catalog.deleteEnvironment('alice',f.environment);
  const row={...f.catalog.lifecycle('alice',f.environment)!,coverage:'complete-shared-resources'};
  let calls=0;const observed:LifecycleAdapter=async input=>{calls++;return adapter(input);};
  await expect(runLifecycleOperation(f.catalog,observed,row)).rejects.toThrow('Shared writer isolation unavailable');
  expect(calls).toBe(0);
  expect(f.catalog.lifecycle('alice',f.environment)!.effects).toEqual([]);
 }finally{f.catalog.close();}
});

test('dedicated completeness requires each explicit database and API service once',()=>{
 const runtime='e_'+'a'.repeat(24),installation=randomUUID();
 const containers:LifecycleResource[]=(['database','auth','rest','storage'] as const).map((service,index)=>({
  kind:'container',id:String(index+1).repeat(64),resource:randomUUID(),installation,runtime,service}));
 const resources:LifecycleResource[]=[...containers,{kind:'volume',id:'exact-volume',createdAt:'2026-10-06T00:00:00Z',
  resource:randomUUID(),installation,runtime},{kind:'directory',id:'/owned/'+runtime,device:1,inode:2,
  marker:'.sbarbase-lifecycle-owner.json',resource:randomUUID(),installation,runtime}];
 expect(()=>validateLifecycleCoverage(runtime,resources,'dedicated-resources')).not.toThrow();
 for(const replacement of [undefined,'auth'] as const){
  const arbitrary=resources.map(item=>item.service==='storage'?{...item,service:replacement}:item);
  expect(()=>validateLifecycleCoverage(runtime,arbitrary,'dedicated-resources')).toThrow('Complete dedicated ownership inventory required');
 }
 expect(()=>validateLifecycleCoverage(runtime,resources.slice(1),'dedicated-resources')).toThrow('Complete dedicated ownership inventory required');
});
