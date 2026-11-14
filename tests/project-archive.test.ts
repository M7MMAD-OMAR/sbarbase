import {test,expect,spyOn} from 'bun:test';
import {Database} from 'bun:sqlite';
import {randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog,CATALOG_SCHEMA_VERSION} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';

const adapter:LifecycleAdapter=async input=>({outcome:input.action==='inspect'?'present':input.action==='quarantine'?'quarantined':
 ['restore','readiness'].includes(input.action)?'restored':'purged',reclaimedBytes:0,observedBytes:17});
function fixture(path=':memory:'){
 const catalog=new Catalog(path,{lifecycleAdmission:()=> 'fixture-recovery-proof'});
 catalog.initializeInstallation('bootstrap','owner','Installation');
 const organization=catalog.createOrganization('owner','Customer'),other=catalog.createOrganization('stranger','Other');
 catalog.setMember('owner',organization,'admin','admin');catalog.setMember('owner',organization,'viewer','viewer');
 const project=catalog.createProject('owner',organization,'i2s'),environment=catalog.createEnvironment('owner',project,'production'),job=catalog.claimProvision()!;
 catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
 catalog.registerLifecycleResources(job.runtime,[{kind:'container',id:'a'.repeat(64),installation:randomUUID(),resource:randomUUID(),runtime:job.runtime}],'disposable-fixture');
 const handler=managementHandler(catalog,async request=>request.headers.get('x-actor'));
 const request=(actor:string,path:string,method='GET',input?:unknown)=>handler(new Request('http://test/management/v1'+path,{method,headers:{'x-actor':actor,...(input===undefined?{}:{'content-type':'application/json'})},...(input===undefined?{}:{body:JSON.stringify(input)})}));
 const deleted=async()=>{catalog.deleteEnvironment('owner',environment);await runLifecycleOperation(catalog,adapter,catalog.lifecycle('owner',environment)!);};
 return {catalog,organization,other,project,environment,job,request,deleted};
}

test('projects with active, incomplete or failed environment deletion remain protected',async()=>{
 const f=fixture();try{
  expect(()=>f.catalog.deleteProject('owner',f.project)).toThrow('Project has environments');
  f.catalog.deleteEnvironment('owner',f.environment);
  expect(()=>f.catalog.deleteProject('owner',f.project)).toThrow('Project has environments');
  await expect(runLifecycleOperation(f.catalog,async()=>{throw new Error('fixture failure');},f.catalog.lifecycle('owner',f.environment)!)).rejects.toThrow();
  expect(()=>f.catalog.deleteProject('owner',f.project)).toThrow('Project has environments');
  expect(f.catalog.listProjects('owner',f.organization).map(item=>item.id)).toContain(f.project);
  await runLifecycleOperation(f.catalog,adapter,f.catalog.lifecycle('owner',f.environment)!);
  f.catalog.createEnvironment('owner',f.project,'staging');
  expect(()=>f.catalog.deleteProject('owner',f.project)).toThrow('Project has environments');
 }finally{f.catalog.close();}
});

test('retained project deletion archives metadata without deleting native evidence, and is idempotent',async()=>{
 const f=fixture();try{
  await f.deleted();const before=f.catalog.lifecycle('owner',f.environment)!;
  const response=await f.request('owner',`/projects/${f.project}`,'DELETE');expect(response.status).toBe(200);expect(await response.json()).toMatchObject({deleted:true,retained:true});
  expect(f.catalog.listProjects('owner',f.organization)).toEqual([]);
  expect(f.catalog.listArchivedProjects('owner',f.organization)).toMatchObject([{id:f.project,name:'i2s',retained_environments:1}]);
  expect(f.catalog.lifecycle('owner',f.environment)).toEqual(before);
  expect(f.catalog.retainedEnvironments('owner',f.project)).toHaveLength(1);
  const first=f.catalog.listArchivedProjects('owner',f.organization)[0]!.archived_at;
  f.catalog.deleteProject('owner',f.project);
  expect(f.catalog.listArchivedProjects('owner',f.organization)[0]!.archived_at).toBe(first);
  expect(f.catalog.auditEvents('owner',f.organization).filter(event=>event.action==='project.archived')).toHaveLength(1);
  expect(()=>f.catalog.renameProject('owner',f.project,'renamed')).toThrow('Forbidden');
  expect(()=>f.catalog.createEnvironment('owner',f.project,'new')).toThrow('Forbidden');
  const destination=f.catalog.createOrganization('owner','Destination');
  expect(()=>f.catalog.transferProject('owner',f.project,destination)).toThrow('Forbidden');
  expect(()=>f.catalog.relinkEnvironment('owner',{organization:{id:f.organization,name:'Customer'},project:{id:f.project,name:'i2s'},environment:{id:randomUUID(),name:'unexpected'}},'e_'+('b'.repeat(24)))).toThrow('Forbidden');
  expect(()=>f.catalog.createProject('owner',f.organization,'i2s')).toThrow('Name already used');
  expect(f.catalog.agentScopeCurrent('owner',{kind:'all'},'projects',f.project)).toBe(false);
 }finally{f.catalog.close();}
});

test('archived project listing and restoration enforce organization membership and owner mutation',async()=>{
 const f=fixture();try{
  await f.deleted();
  for(const actor of ['stranger','viewer','admin'])expect((await f.request(actor,`/projects/${f.project}`,'DELETE')).status).toBe(403);
  expect(f.catalog.listProjects('owner',f.organization).map(item=>item.id)).toContain(f.project);
  f.catalog.deleteProject('owner',f.project);
  expect((await f.request('owner',`/organizations/${f.organization}/archived-projects`)).status).toBe(200);
  for(const actor of ['stranger','viewer'])expect((await f.request(actor,`/organizations/${f.organization}/archived-projects`)).status).toBe(403);
  for(const actor of ['stranger','viewer','admin'])expect((await f.request(actor,`/projects/${f.project}/restore`,'POST')).status).toBe(403);
  expect((await f.request('owner',`/projects/${f.project}/restore`,'POST',{untrusted:true})).status).toBe(400);
  const restored=await f.request('owner',`/projects/${f.project}/restore`,'POST');expect(restored.status).toBe(200);
  expect(f.catalog.listProjects('owner',f.organization).map(item=>item.id)).toContain(f.project);
  expect(f.catalog.listArchivedProjects('owner',f.organization)).toEqual([]);
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('deleted');
  expect((await f.request('owner',`/projects/${f.project}/restore`,'POST')).status).toBe(200);
 }finally{f.catalog.close();}
});

test('only authorized native environment restoration atomically makes an archived project visible',async()=>{
 const f=fixture();try{
  await f.deleted();f.catalog.deleteProject('owner',f.project);
  for(const actor of ['stranger','viewer','admin'])expect(()=>f.catalog.restoreEnvironment(actor,f.environment,randomUUID())).toThrow('Forbidden');
  expect(f.catalog.listProjects('owner',f.organization)).toEqual([]);
  const restoring=f.catalog.restoreEnvironment('owner',f.environment,randomUUID());
  expect(restoring.state).toBe('restoring');
  expect(f.catalog.listProjects('owner',f.organization).map(item=>item.id)).toContain(f.project);
  expect(()=>f.catalog.deleteProject('owner',f.project)).toThrow('Project has environments');
  await runLifecycleOperation(f.catalog,adapter,restoring);
  expect(f.catalog.runtimeReady(f.job.runtime)).toBe(true);
 }finally{f.catalog.close();}
});

test('a fully purged environment may remain as evidence while its project is archived',async()=>{
 const f=fixture();let clock:ReturnType<typeof spyOn>|undefined;
 try{
  await f.deleted();clock=spyOn(Date,'now').mockReturnValue(f.catalog.lifecycle('owner',f.environment)!.retain_until!+1);
  const purge=f.catalog.purgeEnvironment('owner',f.environment,randomUUID());await runLifecycleOperation(f.catalog,adapter,purge);
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('purged');
  expect(f.catalog.deleteProject('owner',f.project)).toMatchObject({retained:true});
  expect(f.catalog.lifecycle('owner',f.environment)!.state).toBe('purged');
 }finally{clock?.mockRestore();f.catalog.close();}
});

test('empty project deletion keeps its original permanent deletion contract',async()=>{
 const f=fixture();try{
  const empty=f.catalog.createProject('owner',f.organization,'Empty');
  const response=await f.request('owner',`/projects/${empty}`,'DELETE');expect(response.status).toBe(200);expect(await response.json()).toEqual({deleted:true});
  expect(()=>f.catalog.restoreProject('owner',empty)).toThrow('Forbidden');
  expect(f.catalog.listArchivedProjects('owner',f.organization)).toEqual([]);
 }finally{f.catalog.close();}
});

test('catalog version 9 upgrades archive metadata atomically and keeps retained hierarchy',async()=>{
 const root=mkdtempSync(join(tmpdir(),'project-archive-upgrade-')),path=join(root,'catalog.sqlite');
 const f=fixture(path);try{
  await f.deleted();f.catalog.close();
  const before=new Database(path);before.exec('DROP TABLE IF EXISTS project_archives; PRAGMA user_version=9');before.close();
  const upgraded=new Catalog(path);try{
   expect(upgraded.schemaVersion()).toBe(10);expect(CATALOG_SCHEMA_VERSION).toBe(10);
   expect(upgraded.listProjects('owner',f.organization).map(item=>item.id)).toContain(f.project);
   expect(upgraded.lifecycle('owner',f.environment)!.state).toBe('deleted');
   upgraded.deleteProject('owner',f.project);expect(upgraded.listArchivedProjects('owner',f.organization)).toHaveLength(1);
  }finally{upgraded.close();}
 }finally{rmSync(root,{recursive:true,force:true});}
});


test('a failed archive DDL rolls back before catalog version 10 is recorded',()=>{
 const root=mkdtempSync(join(tmpdir(),'project-archive-crash-')),path=join(root,'catalog.sqlite');
 const f=fixture(path);f.catalog.close();const seed=new Database(path);seed.exec('DROP TABLE project_archives; PRAGMA user_version=9');seed.close();
 const original=Database.prototype.exec;let failed:Database|undefined;
 const injected=spyOn(Database.prototype,'exec').mockImplementation(function(this:Database,sql:string){
  const value=original.call(this,sql);
  if(sql.includes('CREATE TABLE IF NOT EXISTS project_archives')){failed=this;throw new Error('fixture DDL interruption');}
  return value;
 });
 try{
  expect(()=>new Catalog(path)).toThrow('fixture DDL interruption');injected.mockRestore();failed?.close();failed=undefined;
  const check=new Database(path,{readonly:true});try{
   expect(check.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version).toBe(9);
   expect(check.query("SELECT 1 FROM sqlite_master WHERE name='project_archives'").get()).toBeNull();
   expect(check.query('SELECT 1 FROM environments WHERE id=?').get(f.environment)).not.toBeNull();
  }finally{check.close();}
  const retry=new Catalog(path);try{expect(retry.schemaVersion()).toBe(10);}finally{retry.close();}
 }finally{injected.mockRestore();failed?.close();rmSync(root,{recursive:true,force:true});}
});

test('missing installed native lifecycle authority cannot unarchive a project as a side effect',async()=>{
 const f=fixture();try{
  await f.deleted();f.catalog.deleteProject('owner',f.project);
  const before=f.catalog.lifecycle('owner',f.environment)!;
  f.catalog.lifecycleAuthorizationStore={} as any;
  expect(()=>f.catalog.restoreEnvironment('owner',f.environment,randomUUID())).toThrow('Lifecycle authority revoked');
  expect(f.catalog.listProjects('owner',f.organization)).toEqual([]);
  expect(f.catalog.lifecycle('owner',f.environment)).toEqual(before);
 }finally{f.catalog.close();}
});


test('legacy duplicate-name catalogs cannot create archives before reaching the protected schema version',async()=>{
 const root=mkdtempSync(join(tmpdir(),'project-archive-legacy-')),path=join(root,'catalog.sqlite');
 const f=fixture(path);try{
  await f.deleted();const second=f.catalog.createProject('owner',f.organization,'Second');f.catalog.close();
  const seed=new Database(path);seed.exec('DROP INDEX projects_organization_name; PRAGMA user_version=1');seed.query('UPDATE projects SET name=? WHERE id=?').run('i2s',second);seed.close();
  const legacy=new Catalog(path);try{
   expect(legacy.schemaVersion()).toBe(1);expect(legacy.listProjects('owner',f.organization)).toHaveLength(2);
   expect(()=>legacy.deleteProject('owner',f.project)).toThrow('Project archive migration unavailable');
   expect(legacy.listArchivedProjects('owner',f.organization)).toEqual([]);
  }finally{legacy.close();}
 }finally{rmSync(root,{recursive:true,force:true});}
});
