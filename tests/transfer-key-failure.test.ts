import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managementHandler} from '../src/control/http';
import {managedGateway} from '../src/gateway/managed';
const nativeFetch=globalThis.fetch;

function setup() {
  const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
  const source=catalog.createOrganization('alice','Source');
  const destination=catalog.createOrganization('bob','Destination');
  catalog.setMember('bob',destination,'alice','owner');
  const project=catalog.createProject('alice',source,'Shop');
  const ready=['production','staging'].map(name=>catalog.createEnvironment('alice',project,name));
  let job;
  while((job=catalog.claimProvision()))catalog.finishProvision(job.environment,job.claim!,true);
  const entries=ready.map(environment=>{
    const runtime=catalog.getProvision('alice',environment).runtime;
    return {environment,runtime,key:keys.issue(runtime)};
  }).sort((a,b)=>a.runtime.localeCompare(b.runtime));
  const queued=catalog.createEnvironment('alice',project,'preview');
  const queuedRuntime=catalog.getProvision('alice',queued).runtime;
  const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.',keys);
  const move=(actor='alice')=>handler(new Request(`http://local/management/v1/projects/${project}/move`,{
    method:'POST',headers:{authorization:actor,'content-type':'application/json'},
    body:JSON.stringify({organization:destination})}));
  let transports=0;
  const gateway=managedGateway(catalog,keys,()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',
    keys:[],anonymousToken:'internal',enabled:true}),
    Object.assign(async()=>{transports++;return Response.json({ok:true});}, {preconnect:nativeFetch.preconnect}));
  const app=(entry:typeof entries[number])=>gateway(new Request(`http://local/${entry.runtime}/rest/v1/items`,
    {headers:{apikey:entry.key.token}}));
  return {catalog,keys,source,destination,project,entries,queued,queuedRuntime,move,app,
    transports:()=>transports,close:()=>{catalog.close();keys.close();}};
}

for(const failure of ['before any revocation','after one ready runtime was revoked'] as const) {
  test(`a failed project move rolls ownership and catalog effects back ${failure}`,async()=>{
    const s=setup();
    try {
      for(const entry of s.entries)expect((await s.app(entry)).status).toBe(200);
      const auditBefore=s.catalog.auditEvents('alice',s.source);
      const destinationAuditBefore=s.catalog.auditEvents('bob',s.destination);
      const notificationsBefore=s.catalog.listNotifications(50);
      const queuedBefore=s.catalog.getProvision('alice',s.queued);
      const allRuntimes=[...s.entries.map(entry=>entry.runtime),s.queuedRuntime].sort();
      const failingRuntime=failure==='before any revocation'?allRuntimes[0]!:s.entries[1]!.runtime;
      const revoke=s.keys.revokeAll.bind(s.keys),attempts:string[]=[],revoked:string[]=[];
      s.keys.revokeAll=(runtime:string)=>{
        attempts.push(runtime);
        if(runtime===failingRuntime)throw new Error('Injected key store write failure');
        const count=revoke(runtime);revoked.push(runtime);return count;
      };
      const response=await s.move();
      expect(response.status).toBe(500);
      expect(attempts).toContain(failingRuntime);
      expect(s.catalog.listProjects('alice',s.source).map(project=>project.id)).toContain(s.project);
      expect(s.catalog.listProjects('bob',s.destination).map(project=>project.id)).not.toContain(s.project);
      expect(s.catalog.getProvision('alice',s.queued)).toEqual(queuedBefore);
      expect(s.catalog.auditEvents('alice',s.source)).toEqual(auditBefore);
      expect(s.catalog.auditEvents('bob',s.destination)).toEqual(destinationAuditBefore);
      expect(s.catalog.listNotifications(50)).toEqual(notificationsBefore);
      for(const entry of s.entries) {
        expect(s.catalog.getProvision('alice',entry.environment).organization).toBe(s.source);
        expect(()=>s.catalog.getProvision('bob',entry.environment)).toThrow('Forbidden');
        const wasRevoked=revoked.includes(entry.runtime);
        expect(s.keys.resolve(entry.runtime,entry.key.token)).toBe(wasRevoked?null:'publishable');
        expect((await s.app(entry)).status).toBe(wasRevoked?401:200);
      }
      if(failure==='after one ready runtime was revoked')expect(revoked).toContain(s.entries[0]!.runtime);
      // Retrying completes the transfer and revokes every remaining source key.
      s.keys.revokeAll=revoke;
      const retry=await s.move();
      expect(retry.status).toBe(200);
      expect(await retry.json()).toMatchObject({organization:s.destination,cancelled:[s.queued],
        revoked:s.entries.filter(entry=>!revoked.includes(entry.runtime)).length});
      expect(s.catalog.getProvision('bob',s.queued)).toMatchObject({organization:s.destination,state:'cancelled'});
      expect(s.catalog.listProjects('bob',s.destination).map(project=>project.id)).toContain(s.project);
      const forwarded=s.transports();
      for(const entry of s.entries) {
        expect(s.keys.resolve(entry.runtime,entry.key.token)).toBeNull();
        expect((await s.app(entry)).status).toBe(401);
      }
      expect(s.transports()).toBe(forwarded);
    } finally {s.close();}
  });
}

for(const refusal of ['unauthorized','name clash','running provision'] as const) {
  test(`a project move rejects ${refusal} before touching keys`,async()=>{
    const s=setup();
    try {
      if(refusal==='name clash')s.catalog.createProject('bob',s.destination,'Shop');
      if(refusal==='running provision')expect(s.catalog.claimProvision()?.environment).toBe(s.queued);
      let attempts=0;
      s.keys.revokeAll=()=>{attempts++;throw new Error('Revocation must not be reached');};
      const response=await s.move(refusal==='unauthorized'?'bob':'alice');
      expect(response.status).toBe(refusal==='unauthorized'?403:409);
      expect(attempts).toBe(0);
      expect(s.catalog.listProjects('alice',s.source).map(project=>project.id)).toContain(s.project);
      for(const entry of s.entries) {
        expect(s.keys.resolve(entry.runtime,entry.key.token)).toBe('publishable');
        expect((await s.app(entry)).status).toBe(200);
      }
    } finally {s.close();}
  });
}
