import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
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
