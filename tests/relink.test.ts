import {test,expect} from 'bun:test';
import {Catalog,type Ownership} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';

/** What lab/recovery_bundle.py catalog_ownership records for a runtime, read from a catalog. */
function recorded(catalog:Catalog,actor:string,environment:string):{ownership:Ownership;runtime:string} {
  const job=catalog.getProvision(actor,environment);
  const project=catalog.listProjects(actor,job.organization)
    .find(item=>catalog.listEnvironments(actor,item.id).some(env=>env.id===environment))!;
  const env=catalog.listEnvironments(actor,project.id).find(item=>item.id===environment)!;
  const organization=catalog.listOrganizations(actor).find(item=>item.id===job.organization)!;
  return {runtime:job.runtime,ownership:{organization:{id:organization.id,name:organization.name},
    project:{id:project.id,name:project.name},environment:{id:env.id,name:env.name}}};
}

/** The installation a backup was taken on: its own ids, runtime and operator. */
function source() {
  const catalog=new Catalog(':memory:');
  catalog.initializeInstallation('first','olga','Old installation');
  const organization=catalog.createOrganization('olga','Acme');
  const project=catalog.createProject('olga',organization,'Shop');
  const environment=catalog.createEnvironment('olga',project,'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const backup=recorded(catalog,'olga',environment);
  catalog.close();
  return backup;
}

function target() {
  const catalog=new Catalog(':memory:');
  catalog.initializeInstallation('second','nina','New installation');
  const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
  const relink=async(actor:string,input:unknown)=>{
    const response=await handler(new Request('http://local/management/v1/relink',{method:'POST',
      headers:{authorization:actor,'content-type':'application/json'},body:JSON.stringify(input)}));
    return {status:response.status,body:await response.json() as any};
  };
  return {catalog,relink};
}

test('a restore on a second, fresh installation re-links the environment to its project',async()=>{
  const backup=source();
  const {catalog,relink}=target();
  try {
    const answer=await relink('nina',backup);
    expect(answer.status).toBe(200);
    expect(answer.body.data).toEqual({organization:backup.ownership.organization.id,project:backup.ownership.project.id,
      environment:backup.ownership.environment.id,runtime:backup.runtime,state:'queued',
      created:{organization:true,project:true,environment:true}});
    // The same ids, names and runtime as on the old installation, owned by this operator.
    expect(catalog.listOrganizations('nina').find(item=>item.id===backup.ownership.organization.id))
      .toEqual({id:backup.ownership.organization.id,name:'Acme',role:'owner'});
    expect(catalog.listProjects('nina',backup.ownership.organization.id)).toEqual([
      {id:backup.ownership.project.id,organization:backup.ownership.organization.id,name:'Shop'}]);
    expect(catalog.listEnvironments('nina',backup.ownership.project.id).map(item=>[item.id,item.name,item.state]))
      .toEqual([[backup.ownership.environment.id,'production','queued']]);
    // The worker provisions exactly the recorded runtime, so the backup restores in place.
    const job=catalog.claimProvision()!;
    expect(job.runtime).toBe(backup.runtime);
    expect(job.organization).toBe(backup.ownership.organization.id);
    catalog.finishProvision(job.environment,job.claim!,true);
    expect(recorded(catalog,'nina',backup.ownership.environment.id)).toEqual(backup);
    // Asking again changes nothing and says so.
    const again=await relink('nina',backup);
    expect(again.body.data.created).toEqual({organization:false,project:false,environment:false});
    expect(again.body.data.state).toBe('succeeded');
  } finally {catalog.close();}
});

test('re-linking never attaches to an organization or project by name, and refusals change nothing',async()=>{
  const backup=source();
  const {catalog,relink}=target();
  try {
    // Another organization with the recorded name but another id.
    const other=catalog.createOrganization('nina','Acme');
    expect(await relink('nina',backup)).toEqual({status:409,body:{message:'Organization name belongs to another organization'}});
    expect(catalog.listOrganizations('nina').map(item=>item.id)).not.toContain(backup.ownership.organization.id);
    catalog.renameOrganization('nina',other,'Other');
    // The recorded project id already belongs to another organization here.
    const moved={...backup,ownership:{...backup.ownership,organization:{id:crypto.randomUUID(),name:'Beta'}}};
    expect((await relink('nina',backup)).status).toBe(200);
    expect(await relink('nina',moved)).toEqual({status:409,body:{message:'Project belongs to another organization'}});
    expect(catalog.listOrganizations('nina').map(item=>item.name)).not.toContain('Beta');
    // The recorded environment id with another runtime, and the runtime under another environment.
    const otherRuntime={...backup,runtime:'e_'+'1'.repeat(24)};
    expect(await relink('nina',otherRuntime)).toEqual({status:409,body:{message:'Environment exists with another project or runtime'}});
    const otherEnvironment={...backup,ownership:{...backup.ownership,environment:{id:crypto.randomUUID(),name:'staging'}}};
    expect(await relink('nina',otherEnvironment)).toEqual({status:409,body:{message:'Runtime belongs to another environment'}});
  } finally {catalog.close();}
});

test('only installation operators re-link, into organizations they own, and bad input is refused',async()=>{
  const backup=source();
  const {catalog,relink}=target();
  try {
    const client=catalog.createOrganization('nina','Client');
    catalog.setMember('nina',client,'carl','owner');
    expect((await relink('carl',backup)).status).toBe(403);
    // An existing organization id needs the operator to own it.
    const home=catalog.installationBootstrap()!.organization;
    catalog.setMember('nina',home,'ivan','admin');
    const into={...backup,ownership:{...backup.ownership,organization:{id:client,name:'Client'}}};
    expect((await relink('ivan',into)).status).toBe(403);
    expect((await relink('nina',into)).status).toBe(200);
    expect(catalog.listProjects('nina',client).map(item=>item.id)).toEqual([backup.ownership.project.id]);
    expect((await relink('nina',{...backup,runtime:'../etc'})).status).toBe(400);
    expect((await relink('nina',{runtime:backup.runtime,ownership:null})).status).toBe(400);
    expect((await relink('nina',{runtime:backup.runtime})).status).toBe(400);
  } finally {catalog.close();}
});

test('a runtime deleted here is never re-linked',async()=>{
  const {catalog,relink}=target();
  try {
    const organization=catalog.createOrganization('nina','Acme');
    const project=catalog.createProject('nina',organization,'Shop');
    const environment=catalog.createEnvironment('nina',project,'production');
    const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
    const backup=recorded(catalog,'nina',environment);
    catalog.deleteEnvironment('nina',environment);
    expect(await relink('nina',backup)).toEqual({status:409,body:{message:'Runtime was deleted here'}});
  } finally {catalog.close();}
});
