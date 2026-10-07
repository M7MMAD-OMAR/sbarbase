import {expect,test} from 'bun:test';
import {mkdtempSync,mkdirSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';

// These proposals exercise the real application wrapper with a synthetic native Auth transport.
// They do not establish acceptance against installed Auth, Docker, providers or a fresh host.
function fixture(pauseAt=3){
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const installation=catalog.initializeInstallation('scope-publication-fixture','owner','Installation');
 catalog.setMember('owner',installation,'alice','admin');
 const organization=catalog.createOrganization('owner','Private tenant');
 catalog.setMember('owner',organization,'alice','admin');
 catalog.setMember('owner',organization,'private-member','viewer');
 const project=catalog.createProject('owner',organization,'Private project');
 const environment=catalog.createEnvironment('owner',project,'private-environment');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 catalog.createInvitation('owner',organization,'private-invite@example.invalid','viewer');
 const now=Math.floor(Date.now()/1000),bearer='Bearer '+managementToken('alice','aal2',sessionId,now);
 catalog.managementSecurity.grant('alice',sessionId,factorId,now,now+3600,0);
 let enter!:()=>void,release!:()=>void,lookups=0;
 const finalLookup=new Promise<void>(resolve=>{enter=resolve;}),wait=new Promise<void>(resolve=>{release=resolve;});
 const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  expect(String(input)).toBe('http://realm.invalid/user');
  expect(new Headers(init?.headers).get('authorization')).toBe(bearer);
  if(++lookups===pauseAt){enter();await wait;}
  return Response.json({id:'alice',factors:[verifiedFactor]});
 },{preconnect(){}}) as typeof fetch;
 // Deployment captures absolute directories at construction. The synchronous cwd change has no await.
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-scope-publication-')),previous=process.cwd();
 mkdirSync(join(directory,'.secrets'),{mode:0o700});
 let handler:ReturnType<typeof application>;
 try {process.chdir(directory);handler=application(catalog,keys,
  {auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport,()=>Buffer.alloc(32,1));}
 finally {process.chdir(previous);}
 return {catalog,keys,installation,organization,project,environment,runtime:job.runtime,bearer,now,
  handler:handler!,finalLookup,release,get lookups(){return lookups;},
  request(path:string,method='GET',input?:unknown){return new Request('http://local/management/v1'+path,{method,
   headers:{authorization:bearer,...(input===undefined?{}:{'content-type':'application/json'})},
   ...(input===undefined?{}:{body:JSON.stringify(input)})});},
  close(){release();catalog.close();keys.close();rmSync(directory,{recursive:true,force:true});}};
}

type Fixture=ReturnType<typeof fixture>;
const scopedReads=[
 {name:'invitation email list',path:(f:Fixture)=>`/organizations/${f.organization}/invitations`,write:true,marker:'private-invite@example.invalid'},
 {name:'member identities',path:(f:Fixture)=>`/organizations/${f.organization}/members`,write:true,marker:'private-member'},
 {name:'audit subjects',path:(f:Fixture)=>`/organizations/${f.organization}/audit`,write:true,marker:'invitation.created'},
 {name:'project list',path:(f:Fixture)=>`/organizations/${f.organization}/projects`,write:false,marker:'Private project'},
 {name:'environment list',path:(f:Fixture)=>`/projects/${f.project}/environments`,write:false,marker:'private-environment'},
 {name:'function metadata',path:(f:Fixture)=>`/environments/${f.environment}/functions`,write:true,marker:'runtime'},
 {name:'sign-in configuration',path:(f:Fixture)=>`/environments/${f.environment}/sign-in`,write:true,marker:'callback_url'},
 {name:'Studio state',path:(f:Fixture)=>`/environments/${f.environment}/studio`,write:true,marker:'runtime'},
 {name:'Realtime state',path:(f:Fixture)=>`/environments/${f.environment}/realtime`,write:true,marker:'runtime'},
 {name:'signing history',path:(f:Fixture)=>`/environments/${f.environment}/signing-key`,write:true,marker:'rotatedAt'},
 {name:'mail configuration',path:(f:Fixture)=>`/environments/${f.environment}/mail`,write:false,marker:'unconfigured'},
 {name:'provision state',path:(f:Fixture)=>`/environments/${f.environment}/provision`,write:false,marker:'succeeded'},
] as const;

for(const surface of scopedReads)for(const change of ['removed','viewer','unchanged'] as const)
 test(`${surface.name} uses current tenant authority at final native publication after ${change}`,async()=>{
  const f=fixture();try {
   const pending=f.handler(f.request(surface.path(f)));await f.finalLookup;expect(f.lookups).toBe(3);
   if(change!=='unchanged')f.catalog.changeMember('owner',f.organization,'alice',change==='removed'?null:'viewer');
   // An unrelated operator membership and the exact original grant remain valid.
   expect(f.catalog.installationOperator('alice')).toBe(true);
   expect(f.catalog.managementSecurity.granted('alice',sessionId,f.now,[factorId])).toBe(true);
   f.release();const response=await pending,body=await response.text();
   const admitted=change==='unchanged'||change==='viewer'&&!surface.write;
   expect(response.status).toBe(admitted?200:403);
   if(admitted)expect(body).toContain(surface.marker);
   else {expect(JSON.parse(body)).toEqual({message:'Forbidden'});expect(body).not.toContain(surface.marker);}
  }finally {f.close();}
 });

const transferredScopes=new Set(['environment list','function metadata','sign-in configuration','Studio state','Realtime state','signing history','mail configuration','provision state']);
for(const surface of scopedReads.filter(surface=>transferredScopes.has(surface.name)))
 test(`${surface.name} refuses a project transferred outside the actor's membership during final lookup`,async()=>{
  const f=fixture();try {
   const destination=f.catalog.createOrganization('owner','New owner');
   const pending=f.handler(f.request(surface.path(f)));await f.finalLookup;
   f.catalog.transferProject('owner',f.project,destination,runtime=>f.keys.revokeAll(runtime));
   f.release();const response=await pending;
   expect(response.status).toBe(403);expect(await response.json()).toEqual({message:'Forbidden'});
  }finally {f.close();}
 });

for(const path of ['/server','/environments/ENV/deployment-setup'])for(const change of ['removed','viewer','unchanged'] as const)
 test(`${path} rechecks installation operator after final native lookup: ${change}`,async()=>{
  const f=fixture(path==='/server'?3:6);try {
   const pending=f.handler(f.request(path.replace('ENV',f.environment)));await f.finalLookup;
   expect(f.lookups).toBe(path==='/server'?3:6);
   if(change!=='unchanged')f.catalog.changeMember('owner',f.installation,'alice',change==='removed'?null:'viewer');
   expect(f.catalog.listOrganizations('alice').find(item=>item.id===f.organization)?.role).toBe('admin');
   expect(f.catalog.managementSecurity.granted('alice',sessionId,f.now,[factorId])).toBe(true);
   f.release();const response=await pending;
   expect(response.status).toBe(change==='unchanged'?200:403);
   if(change!=='unchanged')expect(await response.json()).toEqual({message:'Forbidden'});
  }finally {f.close();}
 });

test('organization aggregate drops one revoked tenant while retaining an unrelated current admin tenant',async()=>{
 const f=fixture();try {
  const pending=f.handler(f.request('/organizations'));await f.finalLookup;
  f.catalog.changeMember('owner',f.organization,'alice',null);f.release();
  const response=await pending,body=await response.json() as {data:{id:string}[];operator:boolean};
  expect(response.status).toBe(200);expect(body.data.some(item=>item.id===f.organization)).toBe(false);
  expect(body.data.some(item=>item.id===f.installation)).toBe(true);expect(body.operator).toBe(true);
 }finally {f.close();}
});

test('notifications refilter each tenant and recompute undelivered after partial membership removal',async()=>{
 const f=fixture();try {
  // Both actual Catalog owner changes create durable tenant notifications.
  f.catalog.setMember('owner',f.organization,'tenant-second-owner','owner');
  f.catalog.setMember('owner',f.installation,'installation-second-owner','owner');
  const before=f.catalog.listNotifications(50,'alice');
  expect(before.some(event=>event.organization===f.organization)).toBe(true);
  expect(before.some(event=>event.organization===f.installation)).toBe(true);
  const pending=f.handler(f.request('/notifications'));await f.finalLookup;
  f.catalog.changeMember('owner',f.organization,'alice',null);f.release();
  const response=await pending,body=await response.json() as {data:{events:{id:string;organization:string|null;state:string}[];undelivered:number}};
  expect(response.status).toBe(200);expect(body.data.events.some(event=>event.organization===f.organization)).toBe(false);
  expect(body.data.events.some(event=>event.organization===f.installation)).toBe(true);
  expect(body.data.undelivered).toBe(new Set(body.data.events.filter(event=>event.state!=='delivered').map(event=>event.id)).size);
 }finally {f.close();}
});

test('share read drops installation totals when operator rights end but tenant viewer access remains',async()=>{
 const f=fixture();try {
  const pending=f.handler(f.request(`/environments/${f.environment}/share`));await f.finalLookup;
  f.catalog.changeMember('owner',f.installation,'alice',null);
  f.catalog.changeMember('owner',f.organization,'alice','viewer');f.release();
  const response=await pending,body=await response.json() as {data:Record<string,unknown>};
  expect(response.status).toBe(200);expect(body.data.operator).toBe(false);
  expect(body.data).not.toHaveProperty('total');expect(body.data).not.toHaveProperty('allocated');
  expect(body.data).toHaveProperty('share');
 }finally {f.close();}
});

test('a committed invitation does not publish its token after inviter demotion in final native lookup',async()=>{
 const f=fixture(4);try {
  const pending=f.handler(f.request(`/organizations/${f.organization}/invitations`,'POST',{email:'one-time@example.invalid',role:'viewer'}));
  await f.finalLookup;expect(f.lookups).toBe(4);
  const issued=f.catalog.listInvitations('owner',f.organization).find(item=>item.email==='one-time@example.invalid');expect(issued).toBeDefined();
  f.catalog.changeMember('owner',f.organization,'alice','viewer');f.release();const response=await pending;
  expect(response.status).toBe(403);expect(await response.json()).toEqual({message:'Forbidden'});
  expect(f.catalog.listInvitations('owner',f.organization).some(item=>item.id===issued!.id)).toBe(false);
 }finally {f.close();}
});

test('unrelated tenant revocation preserves an authorized committed invitation token response',async()=>{
 const f=fixture(4);try {
  const pending=f.handler(f.request(`/organizations/${f.organization}/invitations`,'POST',{email:'still-authorized@example.invalid',role:'viewer'}));
  await f.finalLookup;f.catalog.changeMember('owner',f.installation,'alice',null);f.release();
  const response=await pending,body=await response.json() as {data:{id:string;token:string}};
  expect(response.status).toBe(201);expect(body.data.token.length).toBeGreaterThan(0);
  expect(f.catalog.invitationFor(body.data.token)?.id).toBe(body.data.id);
 }finally {f.close();}
});
