import {expect,test} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {RequestLog} from '../src/gateway/observe';
import type {ContainerReader} from '../src/control/observe';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';

// The real application and Catalog run against an explicitly synthetic native Auth transport.
// This isolates the final native lookup boundary without claiming native acceptance.
function fixture(){
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const organization=catalog.createOrganization('owner','Tenant');
 catalog.setMember('owner',organization,'alice','admin');
 const project=catalog.createProject('owner',organization,'Project');
 const environment=catalog.createEnvironment('owner',project,'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const now=Math.floor(Date.now()/1000),bearer='Bearer '+managementToken('alice','aal2',sessionId,now);
 catalog.managementSecurity.grant('alice',sessionId,factorId,now,now+3600,0);
 const log=new RequestLog();
 log.record(job.runtime,{method:'GET',service:'rest',path:'/tenant-private-request',status:200,ms:1});
 const reader:ContainerReader={async logs(){return 'tenant-private-service-line';},
  async stats(){return [{service:'auth',cpuPercent:1,memoryBytes:4242,memoryLimitBytes:8192}];}};
 let enter!:()=>void,release!:()=>void,lookups=0;
 const finalLookup=new Promise<void>(resolve=>{enter=resolve;}),wait=new Promise<void>(resolve=>{release=resolve;});
 const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  expect(String(input)).toBe('http://realm.invalid/user');
  expect(new Headers(init?.headers).get('authorization')).toBe(bearer);
  if(++lookups===3){enter();await wait;}
  return Response.json({id:'alice',factors:[verifiedFactor]});
 },{preconnect(){}}) as typeof fetch;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},
  ()=>undefined,transport,undefined,log,reader);
 return {catalog,keys,organization,project,environment,runtime:job.runtime,bearer,now,handler,finalLookup,release,
  get lookups(){return lookups;},close(){release();catalog.close();keys.close();}};
}

const surfaces=[
 {name:'request logs',path:'logs?source=requests',method:'GET',status:200,marker:'tenant-private-request'},
 {name:'service logs',path:'logs?source=auth',method:'GET',status:200,marker:'tenant-private-service-line'},
 {name:'metrics',path:'metrics',method:'GET',status:200,marker:'memoryBytes'},
 {name:'new key',path:'keys',method:'POST',status:201,marker:'publishable'},
] as const;
for(const surface of surfaces)for(const change of ['removed','viewer'] as const)
 test(`${surface.name} publication rechecks a ${change} actor after the final native lookup`,async()=>{
  const f=fixture();try{
   const pending=f.handler(new Request(`http://local/management/v1/environments/${f.environment}/${surface.path}`,
    {method:surface.method,headers:{authorization:f.bearer}}));
   await f.finalLookup;expect(f.lookups).toBe(3);
   if(surface.name==='new key')expect(f.keys.list(f.runtime)).toHaveLength(1);
   f.catalog.changeMember('owner',f.organization,'alice',change==='removed'?null:'viewer');
   // Membership changes leave the original native session and durable MFA grant valid.
   expect(f.catalog.managementSecurity.granted('alice',sessionId,f.now,[factorId])).toBe(true);
   f.release();const response=await pending,body=await response.text();
   const admitted=surface.name==='metrics'&&change==='viewer';
   expect(response.status).toBe(admitted?200:403);
   if(admitted)expect(body).toContain(surface.marker);
   else {expect(JSON.parse(body)).toEqual({message:'Forbidden'});expect(body).not.toContain(surface.marker);}
  }finally{f.close();}
 });

for(const surface of [surfaces[1],surfaces[3]])test(`${surface.name} publication preserves a current authorized actor`,async()=>{
 const f=fixture();try{
  const pending=f.handler(new Request(`http://local/management/v1/environments/${f.environment}/${surface.path}`,
   {method:surface.method,headers:{authorization:f.bearer}}));
  await f.finalLookup;f.release();const response=await pending;
  expect(response.status).toBe(surface.status);expect(await response.text()).toContain(surface.marker);expect(f.lookups).toBe(3);
 }finally{f.close();}
});

for(const surface of [surfaces[0],surfaces[3]])test(`${surface.name} publication refuses transfer to a different tenant during final lookup`,async()=>{
 const f=fixture();try{
  const target=f.catalog.createOrganization('owner','Destination');
  const pending=f.handler(new Request(`http://local/management/v1/environments/${f.environment}/${surface.path}`,
   {method:surface.method,headers:{authorization:f.bearer}}));
  await f.finalLookup;
  f.catalog.transferProject('owner',f.project,target,runtime=>f.keys.revokeAll(runtime));
  f.release();const response=await pending;
  expect(response.status).toBe(403);expect(await response.json()).toEqual({message:'Forbidden'});
 }finally{f.close();}
});
