import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managementHandler} from '../src/control/http';
import {managedGateway} from '../src/gateway/managed';
import {ConcurrencyGate} from '../src/gateway/concurrency';

/** Rename, move and delete through the management API, with the catalog's role checks. */
function setup() {
  const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
  catalog.initializeInstallation('bootstrap','alice','Installation');
  const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('alice','B');
  catalog.setMember('alice',a,'vera','viewer');catalog.setMember('alice',a,'adam','admin');
  const project=catalog.createProject('alice',a,'Shop');
  const environment=catalog.createEnvironment('alice',project,'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.',keys);
  const call=async(actor:string,method:string,path:string,input?:unknown)=>{
    const response=await handler(new Request('http://local/management/v1/'+path,{method,
      headers:{authorization:actor,...(input===undefined?{}:{'content-type':'application/json'})},
      ...(input===undefined?{}:{body:JSON.stringify(input)})}));
    return {status:response.status,body:await response.json() as any};
  };
  const upstream=(async()=>new Response('{}',{headers:{'content-type':'application/json'}})) as unknown as typeof fetch;
  const gateway=managedGateway(catalog,keys,()=>({auth:'http://auth.internal',rest:'http://rest.internal',
    keys:[],anonymousToken:'anon',enabled:true}),upstream,new ConcurrencyGate(4,8,1000,1000));
  const app=(token:string)=>gateway(new Request(`http://local/${runtime}/rest/v1/items`,{headers:{apikey:token}}));
  return {catalog,keys,a,b,project,environment,runtime,call,app,close:()=>{catalog.close();keys.close();}};
}

test('deleting an organization that still has projects is refused, and an empty one is deleted',async()=>{
  const s=setup();
  try {
    expect(await s.call('alice','DELETE',`organizations/${s.a}`)).toEqual({status:409,body:{message:'Organization has projects'}});
    expect((await s.call('adam','DELETE',`organizations/${s.b}`)).status).toBe(403);
    expect((await s.call('alice','DELETE',`organizations/${s.b}`)).status).toBe(200);
    expect(s.catalog.listOrganizations('alice').map(item=>item.id)).not.toContain(s.b);
    // The organization that runs the installation is never deleted.
    const home=s.catalog.installationBootstrap()!.organization;
    expect(await s.call('alice','DELETE',`organizations/${home}`)).toEqual({status:409,body:{message:'Installation organization cannot be deleted'}});
    // A project that still holds an environment is refused too.
    expect(await s.call('alice','DELETE',`projects/${s.project}`)).toEqual({status:409,body:{message:'Project has environments'}});
  } finally {s.close();}
});

test('a deleted environment\'s key returns 401 and the key is revoked',async()=>{
  const s=setup();
  try {
    const {token,id}=s.keys.issue(s.runtime);
    expect((await s.app(token)).status).toBe(200);
    // Viewers and admins may not delete; owners may.
    expect((await s.call('vera','DELETE',`environments/${s.environment}`)).status).toBe(403);
    expect((await s.call('adam','DELETE',`environments/${s.environment}`)).status).toBe(403);
    const deleted=await s.call('alice','DELETE',`environments/${s.environment}`);
    expect(deleted).toEqual({status:200,body:{deleted:true,revoked:1}});
    const answer=await s.app(token);
    expect(answer.status).toBe(401);
    expect(await answer.json()).toEqual({message:'Invalid API key'});
    expect(s.keys.list(s.runtime).find(key=>key.id===id)?.revoked_at).toBeNumber();
    // Gone from the catalog: an unknown id answers 403, the project may now be deleted.
    expect((await s.call('alice','PATCH',`environments/${s.environment}`,{name:'x'})).status).toBe(403);
    expect(s.catalog.listEnvironments('alice',s.project)).toEqual([]);
    expect((await s.call('alice','DELETE',`projects/${s.project}`)).status).toBe(200);
    // A runtime nobody ever had is still unknown.
    const unknown=await (managedGateway(s.catalog,s.keys,()=>undefined))(new Request('http://local/e_000000000000000000000000/rest/v1/'));
    expect(unknown.status).toBe(404);
  } finally {s.close();}
});

test('an environment is not deleted while provisioning or a service it runs is still on',async()=>{
  const s=setup();
  try {
    const queued=s.catalog.createEnvironment('alice',s.project,'staging');
    expect(await s.call('alice','DELETE',`environments/${queued}`)).toEqual({status:409,body:{message:'Provisioning is active'}});
    s.catalog.requestStudio('alice',s.environment,'running');
    const {token}=s.keys.issue(s.runtime);
    expect(await s.call('alice','DELETE',`environments/${s.environment}`)).toEqual({status:409,body:{message:'Environment services are still on'}});
    // The refusal changed nothing: the key still works.
    expect((await s.app(token)).status).toBe(200);
    s.catalog.requestStudio('alice',s.environment,'stopped');
    expect((await s.call('alice','DELETE',`environments/${s.environment}`)).status).toBe(200);
  } finally {s.close();}
});

test('a worker that restarts with the receipt of a deleted environment settles it and nothing else',()=>{
  const catalog=new Catalog(':memory:');
  try {
    const org=catalog.createOrganization('alice','A'),project=catalog.createProject('alice',org,'P');
    const environment=catalog.createEnvironment('alice',project,'production');
    const job=catalog.claimProvision()!;
    catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0);
    catalog.deleteEnvironment('alice',environment);
    expect(()=>catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,0)).not.toThrow();
    expect(()=>catalog.applyProvisionReceipt(environment,job.runtime,'another-claim',job.attempt,0)).toThrow('Provisioning receipt mismatch');
    expect(()=>catalog.applyProvisionReceipt(environment,job.runtime,job.claim!,job.attempt,75)).toThrow('Provisioning receipt mismatch');
  } finally {catalog.close();}
});

test('renames keep names unique where the hierarchy scopes them',async()=>{
  const s=setup();
  try {
    expect(await s.call('adam','PATCH',`projects/${s.project}`,{name:'Store'})).toEqual({status:200,body:{id:s.project,name:'Store'}});
    expect((await s.call('vera','PATCH',`projects/${s.project}`,{name:'Mine'})).status).toBe(403);
    s.catalog.createProject('alice',s.a,'Other');
    expect(await s.call('alice','PATCH',`projects/${s.project}`,{name:'Other'})).toEqual({status:409,body:{message:'Name already used'}});
    s.catalog.createEnvironment('alice',s.project,'staging');
    expect((await s.call('adam','PATCH',`environments/${s.environment}`,{name:'staging'})).status).toBe(409);
    expect((await s.call('adam','PATCH',`environments/${s.environment}`,{name:'live'})).status).toBe(200);
    expect(s.catalog.listEnvironments('alice',s.project).map(item=>item.name).sort()).toEqual(['live','staging']);
    // Only owners rename the organization; a body with another field is refused.
    expect((await s.call('adam','PATCH',`organizations/${s.a}`,{name:'Acme'})).status).toBe(403);
    expect((await s.call('alice','PATCH',`organizations/${s.a}`,{name:'Acme',extra:1})).status).toBe(400);
    expect((await s.call('alice','PATCH',`organizations/${s.a}`,{name:'Acme'})).status).toBe(200);
    expect(s.catalog.listOrganizations('alice').find(item=>item.id===s.a)?.name).toBe('Acme');
    expect((await s.call('alice','GET',`organizations/${s.a}`)).status).toBe(405);
  } finally {s.close();}
});

test('moving a project revokes its keys and keeps provision_jobs.organization consistent',async()=>{
  const s=setup();
  try {
    const {token}=s.keys.issue(s.runtime);
    const queued=s.catalog.createEnvironment('alice',s.project,'staging');
    // An admin of the source is not enough: moving needs an owner of both organizations.
    expect((await s.call('adam','POST',`projects/${s.project}/move`,{organization:s.b})).status).toBe(403);
    expect((await s.call('alice','POST',`projects/${s.project}/move`,{organization:'not-an-id'})).status).toBe(400);
    const moved=await s.call('alice','POST',`projects/${s.project}/move`,{organization:s.b});
    expect(moved).toEqual({status:200,body:{organization:s.b,cancelled:[queued],revoked:1}});
    expect(s.catalog.getProvision('alice',s.environment).organization).toBe(s.b);
    expect(s.catalog.getProvision('alice',queued).organization).toBe(s.b);
    expect(s.catalog.getProvision('alice',queued).state).toBe('cancelled');
    // The old key no longer opens the environment; members of the source lost it too.
    expect((await s.app(token)).status).toBe(401);
    expect((await s.call('vera','PATCH',`projects/${s.project}`,{name:'Mine'})).status).toBe(403);
    // A name clash in the destination refuses and costs no key.
    const fresh=s.keys.issue(s.runtime);
    s.catalog.createProject('alice',s.a,'Shop');
    expect((await s.call('alice','POST',`projects/${s.project}/move`,{organization:s.a})).status).toBe(409);
    expect((await s.app(fresh.token)).status).toBe(200);
  } finally {s.close();}
});

test('move and environment delete refuse without the key store instead of skipping revocation',async()=>{
  const catalog=new Catalog(':memory:');
  try {
    const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('alice','B');
    const project=catalog.createProject('alice',a,'P');
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
    const response=await handler(new Request(`http://local/management/v1/projects/${project}/move`,{method:'POST',
      headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify({organization:b})}));
    expect(response.status).toBe(500);
    expect(catalog.listProjects('alice',a).map(item=>item.id)).toEqual([project]);
    const environment=catalog.createEnvironment('alice',project,'production');
    const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
    const removal=await handler(new Request(`http://local/management/v1/environments/${environment}`,{method:'DELETE',
      headers:{authorization:'alice'}}));
    expect(removal.status).toBe(500);
    expect(catalog.getProvision('alice',environment).state).toBe('succeeded');
  } finally {catalog.close();}
});
