import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';

test('creation queues once, retries keep runtime identity, stale completion cannot win',()=>{
 const c=new Catalog(':memory:');
 try {
  const org=c.createOrganization('owner','A'),p=c.createProject('owner',org,'P');
  const e=c.createEnvironment('owner',p,'production');
  expect(c.getProvision('owner',e).state).toBe('queued');
  expect(()=>c.createEnvironment('owner',p,'production')).toThrow();
  const first=c.claimProvision()!;expect(first.environment).toBe(e);
  expect(c.claimProvision()).toBeNull();
  const destination=c.createOrganization('owner','Destination');
  expect(()=>c.transferProject('owner',p,destination)).toThrow('Provisioning is active');
  c.recoverProvisioning();const retry=c.claimProvision()!;
  expect(retry.runtime).toBe(first.runtime);expect(retry.attempt).toBe(2);
  expect(()=>c.finishProvision(e,first.claim!,true)).toThrow('Stale');
  c.finishProvision(e,retry.claim!,false);
  expect(c.getProvision('owner',e).state).toBe('failed');
  c.retryProvision('owner',e);const third=c.claimProvision()!;
  c.finishProvision(e,third.claim!,true);
  expect(c.getProvision('owner',e).state).toBe('succeeded');
  expect(()=>c.retryProvision('owner',e)).toThrow('not retryable');
 } finally {c.close();}
});

test('pending jobs cannot execute after actor revocation or organization transfer',()=>{
 const c=new Catalog(':memory:');
 try {
  const a=c.createOrganization('owner','A'),b=c.createOrganization('owner','B');
  c.setMember('owner',a,'admin','admin');
  const p=c.createProject('owner',a,'P'),e=c.createEnvironment('admin',p,'production');
  c.setMember('owner',a,'admin',null);
  expect(c.claimProvision()).toBeNull();expect(c.getProvision('owner',e).state).toBe('cancelled');
  expect(()=>c.retryProvision('admin',e)).toThrow('Forbidden');
  c.retryProvision('owner',e);c.transferProject('owner',p,b);
  expect(c.claimProvision()).toBeNull();expect(c.getProvision('owner',e).state).toBe('cancelled');
  c.retryProvision('owner',e);expect(c.claimProvision()?.environment).toBe(e);
 } finally {c.close();}
});

test('provision status is scoped and hides internal claim and runtime details',async()=>{
 const {managementHandler}=await import('../src/control/http');
 const c=new Catalog(':memory:');
 try {
  const org=c.createOrganization('owner','A'),p=c.createProject('owner',org,'P');
  const e=c.createEnvironment('owner',p,'production');c.claimProvision();
  const request=new Request(`http://localhost/management/v1/environments/${e}/provision`);
  const allowed=await managementHandler(c,async()=> 'owner')(request);
  expect(await allowed.json()).toEqual({environment:e,state:'running',attempt:1});
  expect((await managementHandler(c,async()=> 'outsider')(request)).status).toBe(403);
 } finally {c.close();}
});


test('safe failure code persists, stale claims cannot change it, retry clears it',async()=>{
 const {managementHandler}=await import('../src/control/http');
 const c=new Catalog(':memory:');
 try {
  const org=c.createOrganization('owner','A'),project=c.createProject('owner',org,'P');
  const environment=c.createEnvironment('owner',project,'production'),job=c.claimProvision()!;
  c.finishProvision(environment,job.claim!,false,'capacity_exceeded');
  expect(()=>c.finishProvision(environment,job.claim!,false,'runtime_failed')).toThrow('Stale');
  const response=await managementHandler(c,async()=> 'owner')(new Request(`http://localhost/management/v1/environments/${environment}/provision`));
  expect(await response.json()).toEqual({environment,state:'failed',attempt:1,failure:'capacity_exceeded'});
  c.retryProvision('owner',environment);
  expect(c.getProvision('owner',environment).failure).toBeNull();
  const retried=c.claimProvision()!;c.finishProvision(environment,retried.claim!,true);
  expect(c.getProvision('owner',environment).failure).toBeNull();
 }finally{c.close();}
});
