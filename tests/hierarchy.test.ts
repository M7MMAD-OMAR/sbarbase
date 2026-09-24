import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readFileSync} from 'node:fs';
import {Catalog,ENVIRONMENT_LIMIT} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {KeyStore} from '../src/control/keys';
import {keyHandler} from '../src/control/key-http';

/** A producer outside the catalog (lab/notification_producers.py) may record only a runtime, or
 * nothing at all for an installation event. This writes such a row the same way. */
function produce(path:string,id:string,subject:{organization?:string;runtime?:string}) {
  const db=new Database(path);
  try {
    const now=Date.now();
    db.query(`INSERT INTO notification_outbox(id,at,last_at,window_until,kind,severity,dedupe_key,organization,
      project,environment,runtime,actor,reason,detail,expires_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?)`)
      .run(id,now,now,now,'installation.start_failed','critical',id,subject.organization??null,subject.runtime??null,
        'system:supervisor','installation_failed','{}',now+86400000);
    db.query("INSERT INTO notification_delivery(event,channel,state,next_attempt_at) VALUES (?,'email','pending',?)").run(id,now);
  } finally {db.close();}
}

const ids=(events:{id:string}[])=>[...new Set(events.map(event=>event.id))].sort();

test('an organization owner reads only the notifications of their own organizations',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-hierarchy-'));
  const path=join(directory,'catalog.db');
  const catalog=new Catalog(path);
  try {
    const home=catalog.initializeInstallation('bootstrap','alice','Installation');
    const a=catalog.createOrganization('bob','A'),b=catalog.createOrganization('mallory','B');
    const project=catalog.createProject('mallory',b,'Private');
    const environment=catalog.createEnvironment('mallory',project,'production');
    const runtime=catalog.getProvision('mallory',environment).runtime;
    // One event per organization through the catalog's own producer.
    catalog.setMember('bob',a,'dave','owner');
    catalog.setMember('mallory',b,'erin','owner');
    produce(path,'runtime-only',{runtime});
    produce(path,'installation-wide',{});
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),directory);
    const read=async(actor:string)=>{
      const response=await handler(new Request('http://local/management/v1/notifications',{headers:{authorization:actor}}));
      expect(response.status).toBe(200);
      return (await response.json() as {data:{undelivered:number;events:{id:string;organization:string|null}[]}}).data;
    };
    const bob=await read('bob');
    expect(bob.events.every(event=>event.organization===a)).toBe(true);
    expect(JSON.stringify(bob)).not.toContain(b);
    expect(JSON.stringify(bob)).not.toContain(runtime);
    expect(bob.undelivered).toBe(1);
    // A runtime-only event belongs to the organization that owns the runtime.
    const mallory=await read('mallory');
    expect(ids(mallory.events)).toContain('runtime-only');
    expect(ids(mallory.events)).not.toContain('installation-wide');
    // Installation events go to the bootstrap organization's owners.
    const alice=await read('alice');
    expect(ids(alice.events)).toEqual(['installation-wide']);
    expect(home).toBeString();
    // Trusted callers without an actor still see everything.
    expect(ids(catalog.listNotifications(50)).length).toBe(4);
  } finally {catalog.close();rmSync(directory,{recursive:true});}
});

test('moving a project cancels its queued job visibly and later events name the new organization',()=>{
  const catalog=new Catalog(':memory:');
  try {
    const a=catalog.createOrganization('owner','A'),b=catalog.createOrganization('owner','B');
    const project=catalog.createProject('owner',a,'P');
    const ready=catalog.createEnvironment('owner',project,'production');
    const job=catalog.claimProvision()!;catalog.finishProvision(ready,job.claim!,true);
    const queued=catalog.createEnvironment('owner',project,'staging');
    catalog.transferProject('owner',project,b);
    expect(catalog.getProvision('owner',queued).state).toBe('cancelled');
    expect(catalog.getProvision('owner',queued).organization).toBe(b);
    expect(catalog.getProvision('owner',ready).organization).toBe(b);
    // A routing event for the moved, ready environment now names the destination.
    const runtime=catalog.getProvision('owner',ready).runtime;
    catalog.changeRuntimeRouting(runtime,0,'pause');
    const paused=catalog.listNotifications(50).find(event=>event.kind==='routing.paused')!;
    expect(paused.organization).toBe(b);
    // Retrying in the destination runs the cancelled job under the new owners.
    catalog.retryProvision('owner',queued);
    expect(catalog.claimProvision()?.environment).toBe(queued);
  } finally {catalog.close();}
});

test('a viewer can neither list nor revoke keys, and another organization cannot revoke them',async()=>{
  const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
  try {
    const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('bob','B');
    catalog.setMember('alice',a,'carol','viewer');
    const e=catalog.createEnvironment('alice',catalog.createProject('alice',a,'P'),'production');
    const f=catalog.createEnvironment('bob',catalog.createProject('bob',b,'Q'),'production');
    let job;while((job=catalog.claimProvision()))catalog.finishProvision(job.environment,job.claim!,true);
    const handler=keyHandler(catalog,keys,async request=>request.headers.get('authorization'));
    const request=(actor:string,environment:string,tail:string,method:string)=>handler(new Request(
      `http://localhost/management/v1/environments/${environment}/${tail}`,{method,headers:{authorization:actor}}));
    const issued=await request('alice',e,'keys','POST');expect(issued.status).toBe(201);
    const {id}=await issued.json() as {id:string};
    expect((await request('carol',e,'keys','GET')).status).toBe(403);
    expect((await request('carol',e,`keys/${id}`,'DELETE')).status).toBe(403);
    expect([403,404]).toContain((await request('bob',f,`keys/${id}`,'DELETE')).status);
    expect((await request('bob',e,`keys/${id}`,'DELETE')).status).toBe(403);
    // The key is still active for its owner.
    const listed=await (await request('alice',e,'keys','GET')).json() as {data:{id:string;revoked_at?:number|null}[]};
    const kept=listed.data.find(key=>key.id===id);
    expect(kept).toBeDefined();expect(kept!.revoked_at).toBeNull();
  } finally {catalog.close();keys.close();}
});

test('names are unique where the hierarchy scopes them, and a clash answers 409',async()=>{
  const catalog=new Catalog(':memory:');
  try {
    const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('alice','B');
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
    const create=(path:string,name:string)=>handler(new Request('http://local/management/v1/'+path,{method:'POST',
      headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify({name})}));
    const first=await create(`organizations/${a}/projects`,'Shop');expect(first.status).toBe(201);
    const {id:project}=await first.json() as {id:string};
    expect((await create(`organizations/${a}/projects`,'Shop')).status).toBe(409);
    // The same name in another organization is a different project.
    expect((await create(`organizations/${b}/projects`,'Shop')).status).toBe(201);
    expect((await create(`projects/${project}/environments`,'production')).status).toBe(202);
    expect((await create(`projects/${project}/environments`,'production')).status).toBe(409);
    // A move may not create a duplicate in the destination.
    expect(()=>catalog.transferProject('alice',project,b)).toThrow('Name already used');
  } finally {catalog.close();}
});

test('the catalog migrates an older file in place and refuses one written by a newer release',()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-schema-'));
  try {
    const old=join(directory,'old.db');
    // The shape a catalog had before the failure column and the version ladder.
    const legacy=new Database(old);
    legacy.exec(`CREATE TABLE organizations(id TEXT PRIMARY KEY,name TEXT NOT NULL);
      CREATE TABLE memberships(organization TEXT NOT NULL REFERENCES organizations(id), actor TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('owner','admin','viewer')), PRIMARY KEY(organization,actor));
      CREATE TABLE projects(id TEXT PRIMARY KEY, organization TEXT NOT NULL REFERENCES organizations(id),name TEXT NOT NULL);
      CREATE TABLE environments(id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL, UNIQUE(project,name));
      CREATE TABLE provision_jobs(environment TEXT PRIMARY KEY REFERENCES environments(id),runtime TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL,organization TEXT NOT NULL REFERENCES organizations(id),
        state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
        attempt INTEGER NOT NULL DEFAULT 0,claim TEXT);
      INSERT INTO organizations VALUES ('o','Old');
      INSERT INTO memberships VALUES ('o','alice','owner');
      INSERT INTO projects VALUES ('p','o','Kept');`);
    legacy.close();
    const migrated=new Catalog(old);
    try {
      expect(migrated.schemaVersion()).toBe(3);
      expect(migrated.listProjects('alice','o').map(project=>project.name)).toEqual(['Kept']);
      expect(()=>migrated.createProject('alice','o','Kept')).toThrow('Name already used');
    } finally {migrated.close();}
    // Duplicates written before the rule keep the catalog usable at version 1.
    const duplicated=join(directory,'duplicated.db');
    const seeded=new Catalog(duplicated);
    const organization=seeded.createOrganization('alice','D');seeded.close();
    const raw=new Database(duplicated);
    raw.exec("DROP INDEX projects_organization_name; PRAGMA user_version=1;");
    raw.query('INSERT INTO projects VALUES (?,?,?),(?,?,?)').run('x',organization,'Same','y',organization,'Same');
    raw.close();
    const reopened=new Catalog(duplicated);
    try {expect(reopened.schemaVersion()).toBe(1);} finally {reopened.close();}
    // A newer release's catalog is not read.
    const newer=new Database(duplicated);newer.exec('PRAGMA user_version=99');newer.close();
    expect(()=>new Catalog(duplicated)).toThrow('newer than this release');
  } finally {rmSync(directory,{recursive:true});}
});

test('the API refuses an environment past the installation limit before queueing it',async()=>{
  const catalog=new Catalog(':memory:');
  try {
    const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('bob','B');
    const p=catalog.createProject('alice',a,'P'),q=catalog.createProject('bob',b,'Q');
    for(let index=0;index<ENVIRONMENT_LIMIT;index++)catalog.createEnvironment('alice',p,'e'+index);
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
    const response=await handler(new Request(`http://local/management/v1/projects/${q}/environments`,{method:'POST',
      headers:{authorization:'bob','content-type':'application/json'},body:JSON.stringify({name:'production'})}));
    expect(response.status).toBe(409);
    expect(catalog.listEnvironments('bob',q)).toEqual([]);
    // A cancelled job frees its slot, and retrying it takes the slot back.
    const cancelled=catalog.listEnvironments('alice',p)[0]!.id;
    const raw=(catalog as unknown as {db:{query:(sql:string)=>{run:(...args:unknown[])=>unknown}}}).db;
    raw.query("UPDATE provision_jobs SET state='cancelled' WHERE environment=?").run(cancelled);
    expect(catalog.createEnvironment('bob',q,'production')).toBeString();
    expect(()=>catalog.retryProvision('alice',cancelled)).toThrow('Environment capacity reached');
  } finally {catalog.close();}
});

test('the API limit and the runtime guard name the same number',()=>{
  const source=readFileSync(new URL('../lab/durable_runtime.py',import.meta.url),'utf8');
  expect(source).toContain(`ENVIRONMENT_LIMIT = ${ENVIRONMENT_LIMIT}\n`);
  expect(source).toContain('> ENVIRONMENT_LIMIT:');
});

test('installation operators create organizations; owners list members; failed environments retry',async()=>{
  const catalog=new Catalog(':memory:');
  try {
    const home=catalog.initializeInstallation('bootstrap','alice','Installation');
    const client=catalog.createOrganization('bob','Client');catalog.setMember('bob',client,'carol','viewer');
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
    const call=(actor:string,path:string,method='GET',name?:string)=>handler(new Request('http://local/management/v1/'+path,{method,
      headers:{authorization:actor,...(name===undefined?{}:{'content-type':'application/json'})},...(name===undefined?{}:{body:JSON.stringify({name})})}));
    expect((await (await call('alice','organizations')).json() as {operator:boolean}).operator).toBe(true);
    expect((await (await call('bob','organizations')).json() as {operator:boolean}).operator).toBe(false);
    expect((await call('bob','organizations','POST','Another')).status).toBe(403);
    const created=await call('alice','organizations','POST','Second client');expect(created.status).toBe(201);
    const {id}=await created.json() as {id:string};
    expect(catalog.listOrganizations('alice').map(item=>item.id)).toContain(id);
    expect(home).not.toBe(id);
    // Members: owners and admins read, viewers and outsiders do not.
    const listed=await call('bob',`organizations/${client}/members`);expect(listed.status).toBe(200);
    expect((await listed.json() as {data:unknown}).data).toEqual([{actor:'bob',role:'owner'},{actor:'carol',role:'viewer'}]);
    expect((await call('carol',`organizations/${client}/members`)).status).toBe(403);
    expect((await call('alice',`organizations/${client}/members`)).status).toBe(403);
    // Retry: only a failed or cancelled job, only by an owner or admin.
    const project=catalog.createProject('bob',client,'P'),environment=catalog.createEnvironment('bob',project,'production');
    expect((await call('bob',`environments/${environment}/retry`,'POST')).status).toBe(409);
    const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,false);
    expect((await call('carol',`environments/${environment}/retry`,'POST')).status).toBe(403);
    expect((await call('alice',`environments/${environment}/retry`,'POST')).status).toBe(403);
    expect((await call('bob',`environments/${environment}/retry`,'GET')).status).toBe(405);
    expect((await call('bob',`environments/${environment}/retry`,'POST')).status).toBe(202);
    expect(catalog.getProvision('bob',environment).state).toBe('queued');
  } finally {catalog.close();}
});
