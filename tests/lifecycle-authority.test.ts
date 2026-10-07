import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,readFileSync,chmodSync,writeFileSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {LifecycleAuthorizationStore,NativeLifecycleAuthority} from '../src/control/lifecycle-authority';
import {runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import type {LifecycleResource} from '../src/control/lifecycle-contract';
import {managementToken,factorId,otherFactorId,sessionId,verifiedFactor} from './management-fixture';

const adapter:LifecycleAdapter=async input=>({outcome:input.action==='inspect'?'present':input.action==='quarantine'?'quarantined':
 ['restore','readiness'].includes(input.action)?'restored':'purged',reclaimedBytes:0,observedBytes:11});
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'lifecycle-session-'));chmodSync(root,0o700);
 const path=join(root,'catalog.sqlite');let catalog=new Catalog(path);catalog.initializeInstallation('bootstrap','owner','Installation');
 const organization=catalog.createOrganization('owner','Customer'),project=catalog.createProject('owner',organization,'Project');
 const environment=catalog.createEnvironment('owner',project,'production'),job=catalog.claimProvision()!;
 catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
 const resource:LifecycleResource={kind:'container',id:'a'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:job.runtime};
 catalog.registerLifecycleResources(job.runtime,[resource],'disposable-fixture');
 catalog.setMember('owner',organization,'neighbor','owner');
 let now=Math.floor(Date.now()/1000),factors=[verifiedFactor],nativeActor='owner',nativeOk=true,processIdentity='fixture-original';
 const clock=Date.now;Date.now=()=>now*1000;
 const realm=async()=>({auth:'http://original-management.invalid',key:'fixture-native-anon',process:processIdentity});
 const transport:typeof fetch=Object.assign(async()=>nativeOk?Response.json({id:nativeActor,is_anonymous:false,factors}):Response.json({message:'revoked'},{status:401}),{preconnect:()=>{}});
 function connect(){catalog.lifecycleAuthorizationStore=LifecycleAuthorizationStore.fixtureOnly(path,join(root,'private'),realm,transport,()=>nativeOk&&nativeActor==='owner'&&processIdentity==='fixture-original'?factors:null);}
 connect();
 let token=managementToken('owner','aal2',sessionId,now);
 catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+50000,0);
 const request=()=>new Request('http://local',{headers:{authorization:'Bearer '+token}});
 const authorize=()=>NativeLifecycleAuthority.request(catalog,catalog.lifecycleAuthorizationStore!,request(),transport);
 return {root,path,environment,organization,project,job,resource,authorize,request,
  get catalog(){return catalog;},get now(){return now;},set now(value:number){now=value;},
  set nativeOk(value:boolean){nativeOk=value;},set nativeActor(value:string){nativeActor=value;},
  set factors(value:typeof factors){factors=value;},set processIdentity(value:string){processIdentity=value;},
  async delete(){const authority=await authorize();authority.run(()=>catalog.deleteEnvironment('owner',environment));return catalog.lifecycle('owner',environment)!;},
  restart(){catalog.close();catalog=new Catalog(path);connect();},
  longBearer(){token=managementToken('owner','aal2',sessionId,now,{exp:now+100000});},
  renew(){token=managementToken('owner','aal2',randomUUID(),now);const claims=JSON.parse(Buffer.from(token.split('.')[1]!,'base64url').toString());
   catalog.managementSecurity.grant('owner',claims.session_id,factorId,now,now+50000,catalog.managementSecurity.epoch('owner'));},
  close(){Date.now=clock;catalog.close();rmSync(root,{recursive:true,force:true});}};
}

for(const boundary of ['inspect','quarantine'] as const){
 for(const fault of ['bearer-expiry','password-age','grant-revocation','factor-removal','native-session-revocation','native-actor-change','owner-change','realm-process-change'] as const){
  test(`lifecycle ${fault} after awaited ${boundary} refuses settlement and later effects`,async()=>{
   const f=fixture();try{
    if(fault==='password-age')f.longBearer();
    const row=await f.delete();let effects=0;
    const observed:LifecycleAdapter=async input=>{
     if(input.action==='quarantine')effects++;
     const result=await adapter(input);
     if(input.action===boundary){
      if(fault==='bearer-expiry')f.now+=3601;
      if(fault==='password-age'){
       f.now+=43195;
      }
      if(fault==='grant-revocation')f.catalog.managementSecurity.revoke('owner');
      if(fault==='factor-removal')f.factors=[];
      if(fault==='native-session-revocation')f.nativeOk=false;
      if(fault==='native-actor-change')f.nativeActor='other';
      if(fault==='owner-change')f.catalog.setMember('owner',f.organization,'owner','admin');
      if(fault==='realm-process-change')f.processIdentity='fixture-replacement';
     }
     return result;
    };
    await expect(runLifecycleOperation(f.catalog,observed,row)).rejects.toThrow();
    expect(effects).toBe(boundary==='inspect'?0:1);
    expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.state).toBe('deleting');
    expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.effects.some(item=>item.state==='done')).toBe(false);
   }finally{f.close();}
  });
 }
}

for(const fault of ['bearer-expiry','password-age','grant-revocation','factor-removal','native-session-revocation','native-actor-change','owner-change','realm-process-change'] as const){
 test(`final synchronous lifecycle publication refuses ${fault} after the last successful lookup`,async()=>{
  const f=fixture();try{
   if(fault==='password-age')f.longBearer();
   const row=await f.delete(),finish=f.catalog.finishLifecycle.bind(f.catalog);
   f.catalog.finishLifecycle=(current,failure)=>{
    if(fault==='bearer-expiry')f.now+=3601;
    if(fault==='password-age')f.now+=43195;
    if(fault==='grant-revocation')f.catalog.managementSecurity.revoke('owner');
    if(fault==='factor-removal')f.factors=[];
    if(fault==='native-session-revocation')f.nativeOk=false;
    if(fault==='native-actor-change')f.nativeActor='other';
    if(fault==='owner-change')f.catalog.setMember('owner',f.organization,'owner','admin');
    if(fault==='realm-process-change')f.processIdentity='fixture-replacement';
    finish(current,failure);
   };
   await expect(runLifecycleOperation(f.catalog,adapter,row)).rejects.toThrow();
   expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.state).toBe('deleting');
  }finally{f.close();}
 });
}

test('valid private authorization continues after Catalog close and standalone capability reconstruction',async()=>{
 const f=fixture();try{
  const row=await f.delete();f.restart();await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycleAuthorizationOperation(row.operation)!);
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('deleted');
  const authority=await f.authorize();const restored=authority.run(()=>f.catalog.restoreEnvironment('owner',f.environment,randomUUID()));
  f.restart();await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycleAuthorizationOperation(restored.operation)!);
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(true);
 }finally{f.close();}
});

test('expired original session requires explicit new authorization without rewriting operation or history',async()=>{
 const f=fixture();try{
  const row=await f.delete();f.now+=3601;
  await expect(runLifecycleOperation(f.catalog,adapter,row)).rejects.toThrow();
  const before=f.catalog.lifecycleAuthorizationOperation(row.operation)!;
  f.renew();const authority=await f.authorize();authority.run(()=>f.catalog.resumeLifecycleOperation('owner',f.environment,row.operation));
  const after=f.catalog.lifecycleAuthorizationOperation(row.operation)!;
  expect(after.operation).toBe(before.operation);expect(after.actor).toBe(before.actor);expect(after.management_epoch).toBe(before.management_epoch);
  expect(after.resources).toEqual(before.resources);expect(after.effects).toEqual(before.effects);expect(after.epoch).toBe(before.epoch);
  await runLifecycleOperation(f.catalog,adapter,after);
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('deleted');
 }finally{f.close();}
});

test('bearer material never appears in public lifecycle projection or journal and tampering refuses',async()=>{
 const f=fixture();try{
  const row=await f.delete();const publicText=JSON.stringify(row);
  expect(publicText).not.toContain('Bearer');expect(publicText).not.toContain(sessionId);expect(publicText).not.toContain('passwordAt');
  const file=join(f.root,'private',readdirSync(join(f.root,'private'))[0]!);
  const privateText=readFileSync(file,'utf8');expect(privateText).toContain('Bearer');
  writeFileSync(file,privateText.replace('owner','other'));
  await expect(runLifecycleOperation(f.catalog,adapter,row)).rejects.toThrow();
 }finally{f.close();}
});

for(const fault of ['bearer-expiry','password-age','grant-revocation','factor-removal','native-session-revocation','owner-change'] as const){
 test(`restore readiness callback refuses ${fault} before final publication`,async()=>{
  const f=fixture();try{
   const deleting=await f.delete();await runLifecycleOperation(f.catalog,adapter,deleting);
   if(fault==='password-age')f.longBearer();
   const authority=await f.authorize();const row=authority.run(()=>f.catalog.restoreEnvironment('owner',f.environment,randomUUID()));
   await expect(runLifecycleOperation(f.catalog,adapter,row,async()=>{
    if(fault==='bearer-expiry')f.now+=3601;
    if(fault==='password-age')f.now+=43195;
    if(fault==='grant-revocation')f.catalog.managementSecurity.revoke('owner');
    if(fault==='factor-removal')f.factors=[];
    if(fault==='native-session-revocation')f.nativeOk=false;
    if(fault==='owner-change')f.catalog.setMember('owner',f.organization,'owner','admin');
    return true;
   })).rejects.toThrow();
   expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.state).toBe('restoring');
   expect(f.catalog.runtimeReady(f.job.runtime)).toBe(false);
  }finally{f.close();}
 });
}

test('private authorization inode replacement and permission widening both refuse replay',async()=>{
 const f=fixture();try{
  const row=await f.delete(),privateRoot=join(f.root,'private'),file=join(privateRoot,readdirSync(privateRoot)[0]!);
  chmodSync(file,0o644);await expect(runLifecycleOperation(f.catalog,adapter,row)).rejects.toThrow();chmodSync(file,0o600);
  const content=readFileSync(file);rmSync(file);writeFileSync(file,content,{mode:0o600});
  await expect(runLifecycleOperation(f.catalog,adapter,row)).rejects.toThrow();
 }finally{f.close();}
});

test('fixture authentication cannot authorize a full native lifecycle inventory',async()=>{
 const f=fixture();try{
  const authority=await f.authorize(),original=f.catalog.lifecycle('owner',f.environment)!;
  // Keep this case at the trust boundary, without manufacturing dedicated resource admission.
  const row={...original,state:'deleting' as const,actor:'owner',management_epoch:0,coverage:'dedicated-resources'};
  expect(()=>authority.capture(row)).toThrow('Lifecycle authority revoked');
 }finally{f.close();}
});

test('same Catalog transfer runtime guard fences enrollment lifecycle intent and worker effects',async()=>{
 const f=fixture();const {Database}=await import('bun:sqlite');const control=new Database(f.path);
 try{
  control.exec('CREATE TABLE transfer_runtime_guards(runtime TEXT PRIMARY KEY,operation TEXT NOT NULL,held_epoch INTEGER NOT NULL)');
  const hold=()=>control.query('INSERT INTO transfer_runtime_guards VALUES (?,?,?)').run(f.job.runtime,randomUUID(),f.catalog.runtimeEpoch(f.job.runtime));
  hold();expect(()=>f.catalog.registerLifecycleResources(f.job.runtime,[f.resource],'disposable-fixture')).toThrow('Lifecycle authority revoked');
  await expect(f.delete()).rejects.toThrow('Lifecycle authority revoked');
  control.exec('DELETE FROM transfer_runtime_guards');const row=await f.delete();hold();let effects=0;
  await expect(runLifecycleOperation(f.catalog,async input=>{effects++;return adapter(input);},row)).rejects.toThrow('Lifecycle authority revoked');
  expect(effects).toBe(0);expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.state).toBe('deleting');
  }finally{control.close();f.close();}
});

for(const boundary of ['restart','inspect'] as const)
 test(`lifecycle preserves its original grant factor after same-timestamp replacement at ${boundary}`,async()=>{
  const f=fixture();try{
   const second={...verifiedFactor,id:otherFactorId};f.factors=[verifiedFactor,second];
   const row=await f.delete();let effects=0;
   const replace=()=>{
    expect(f.catalog.managementSecurity.grant('owner',sessionId,otherFactorId,f.now,f.now+50000,0)).toBe(true);
    f.factors=[second];
    expect(f.catalog.managementSecurity.granted('owner',sessionId,f.now,[otherFactorId])).toBe(true);
   };
   if(boundary==='restart'){f.restart();replace();}
   await expect(runLifecycleOperation(f.catalog,async input=>{
    effects++;const result=await adapter(input);if(input.action==='inspect')replace();return result;
   },f.catalog.lifecycleAuthorizationOperation(row.operation)!)).rejects.toThrow('Lifecycle authority revoked');
   expect(effects).toBe(boundary==='restart'?0:1);
   expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.state).toBe('deleting');
   expect(f.catalog.lifecycleAuthorizationOperation(row.operation)!.effects.some(item=>item.state==='done')).toBe(false);
  }finally{f.close();}
 });

test('explicit lifecycle renewal can bind a different currently granted factor without changing operation history',async()=>{
 const f=fixture();try{
  const second={...verifiedFactor,id:otherFactorId};f.factors=[verifiedFactor,second];
  const row=await f.delete(),before=f.catalog.lifecycleAuthorizationOperation(row.operation)!;
  expect(f.catalog.managementSecurity.grant('owner',sessionId,otherFactorId,f.now,f.now+50000,0)).toBe(true);
  f.factors=[second];const authority=await f.authorize();
  authority.run(()=>f.catalog.resumeLifecycleOperation('owner',f.environment,row.operation));
  const renewed=f.catalog.lifecycleAuthorizationOperation(row.operation)!;
  expect(renewed.operation).toBe(before.operation);expect(renewed.effects).toEqual(before.effects);
  expect(renewed.resources).toEqual(before.resources);expect(renewed.epoch).toBe(before.epoch);
  await runLifecycleOperation(f.catalog,adapter,renewed);
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('deleted');
 }finally{f.close();}
});
