import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managedGateway} from '../src/gateway/managed';
import {resolveRuntimePlacement,validatePlacement,type NativePlacement} from '../src/control/placement';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';

function fixture(){
 const directory=mkdtempSync(join(tmpdir(),'sbar-placement-'));
 const path=join(directory,'control.sqlite'),catalog=new Catalog(path),keys=new KeyStore(':memory:');
 const org=catalog.createOrganization('owner','O'),project=catalog.createProject('owner',org,'P');
 const environment=catalog.createEnvironment('owner',project,'E'),job=catalog.claimProvision()!;
 catalog.finishProvision(environment,job.claim!,true);
 return {catalog,keys,path,runtime:job.runtime,environment,close(){catalog.close();keys.close();rmSync(directory,{recursive:true,force:true});}};
}
function native(runtime:string):NativePlacement {
 return {version:1,profile:'native-dedicated',runtime,ownership:'owned-project',generation:1,
  engine:{id:'engine-a',container:'a'.repeat(64),image:'sha256:'+'b'.repeat(64),network:'c'.repeat(64),volume:'owned-pgdata'},
  application:{name:'postgres',oid:17001},maintenance:{name:'sbar_maintenance',oid:17002},
  credentials:{application:'secret-ref/app',maintenance:'secret-ref/maintenance'},declaration:'controlled-declaration',
  services:{auth:'http://native-auth.invalid',rest:'http://native-rest.invalid'}};
}
const configured={auth:'http://legacy-auth.invalid',rest:'http://legacy-rest.invalid',keys:[],anonymousToken:'anon',enabled:true};

test('legacy absence and URL-only placement retain routing without native identity adoption',async()=>{
 const f=fixture();let observed:string[]=[];
 const key=f.keys.issue(f.runtime),gateway=managedGateway(f.catalog,f.keys,()=>configured,
  (async(input)=>{observed.push(String(input));return new Response(null,{status:204});}) as typeof fetch);
 const request=()=>new Request(`http://local/${f.runtime}/rest/v1/`,{headers:{apikey:key.token}});
 try{
  expect(resolveRuntimePlacement(f.runtime,f.catalog.runtimeRouting(f.runtime))).toEqual({profile:'legacy-shared',runtime:f.runtime,generation:null,database:f.runtime,services:null});
  expect(()=>resolveRuntimePlacement(f.runtime,f.catalog.runtimeRouting(f.runtime),native(f.runtime))).toThrow('initialization');
  expect((await gateway(request())).status).toBe(204);
  f.catalog.changeRuntimeRouting(f.runtime,0,'pause');
  f.catalog.changeRuntimeRouting(f.runtime,1,'stage',{auth:'http://stage-auth.invalid',rest:'http://stage-rest.invalid'});
  f.catalog.changeRuntimeRouting(f.runtime,2,'resume');
  expect(resolveRuntimePlacement(f.runtime,f.catalog.runtimeRouting(f.runtime)).generation).toBeNull();
  expect((await gateway(request())).status).toBe(204);
  expect(observed).toEqual(['http://legacy-rest.invalid/','http://stage-rest.invalid/']);
 }finally{f.close();}
});

test('CAS stages declared identity only in maintenance and refuses activation or identity downgrade',()=>{
 const f=fixture(),n=native(f.runtime),other=new Catalog(f.path);
 const db=new Database(f.path);
 try{
  expect(()=>f.catalog.changeRuntimeRouting(f.runtime,0,'stage',n)).toThrow('transition');
  expect(f.catalog.runtimeRouting(f.runtime).revision).toBe(0);
  f.catalog.changeRuntimeRouting(f.runtime,0,'pause');
  expect(()=>f.catalog.changeRuntimeRouting(f.runtime,1,'stage',{...n,generation:2})).toThrow('Initial');
  f.catalog.changeRuntimeRouting(f.runtime,1,'stage',n);
  expect(other.runtimeRouting(f.runtime)).toEqual({revision:2,maintenance:true,placement:n});
  const auditBefore=db.query('SELECT count(*) AS count FROM audit_events').get();
  const refused:NativePlacement[]=[{...n,ownership:'foreign'}, {...n,generation:3}, {...n,generation:0},
   {...n,engine:{...n.engine,container:'d'.repeat(64)}}, {...n,runtime:'e_'+'f'.repeat(24)}];
  for(const record of refused)expect(()=>f.catalog.changeRuntimeRouting(f.runtime,2,'stage',record)).toThrow();
  expect(()=>f.catalog.changeRuntimeRouting(f.runtime,2,'stage',{auth:'http://old.invalid',rest:'http://old.invalid'})).toThrow('revert');
  expect(()=>f.catalog.changeRuntimeRouting(f.runtime,2,'resume')).toThrow('not admitted');
  expect(other.runtimeRouting(f.runtime)).toEqual({revision:2,maintenance:true,placement:n});
  expect(db.query('SELECT count(*) AS count FROM audit_events').get()).toEqual(auditBefore);
  const next={...n,generation:2,engine:{...n.engine,container:'d'.repeat(64)}};
  expect(other.changeRuntimeRouting(f.runtime,2,'stage',next)).toBe(3);
  expect(()=>f.catalog.changeRuntimeRouting(f.runtime,2,'stage',n)).toThrow('Stale routing');
  expect(()=>resolveRuntimePlacement(f.runtime,f.catalog.runtimeRouting(f.runtime),n)).toThrow('Stale or foreign');
  expect(f.catalog.runtimeRouting(f.runtime).placement).toEqual(next);
 }finally{db.close();other.close();f.close();}
});

test('native identity validates distinct maintenance and bound image, container and OIDs',()=>{
 const runtime='e_'+'1'.repeat(24),n=native(runtime),routing={revision:2,maintenance:true,placement:n};
 expect(resolveRuntimePlacement(runtime,routing,n).profile).toBe('native-dedicated');
 for(const record of [ {...n,version:2}, {...n,profile:'unknown'}, {...n,maintenance:undefined},
  {...n,maintenance:n.application}, {...n,maintenance:{...n.maintenance,oid:n.application.oid}},
  {...n,engine:{...n.engine,container:'short'}}, {...n,services:{...n.services,auth:'http://user:secret@host'}}])expect(()=>validatePlacement(record)).toThrow();
 for(const record of [{...n,engine:{...n.engine,container:'d'.repeat(64)}},{...n,application:{...n.application,oid:17003}},
  {...n,maintenance:{...n.maintenance,oid:17004}},{...n,generation:2}])expect(()=>resolveRuntimePlacement(runtime,routing,record)).toThrow('Stale or foreign');
});

test('actual gateway and service discovery refuse unadmitted or malformed persisted placement before endpoint resolution',async()=>{
 const f=fixture();let endpoints=0,applicationTransport=0,gatewayTransport=0;
 const now=Math.floor(Date.now()/1000),outsiderSession='55555555-5555-4555-8555-555555555555';
 const ownerToken=managementToken('owner','aal2',sessionId,now),outsiderToken=managementToken('outsider','aal2',outsiderSession,now);
 f.catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
 f.catalog.managementSecurity.grant('outsider',outsiderSession,factorId,now,now+3600,0);
 const endpoint=()=>{endpoints++;return configured;};
 const key=f.keys.issue(f.runtime);
 const gateway=managedGateway(f.catalog,f.keys,endpoint,(async(_input)=>{gatewayTransport++;return new Response();}) as typeof fetch);
 const handler=application(f.catalog,f.keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},endpoint,
  (async(input,init)=>{applicationTransport++;expect(String(input)).toBe('http://realm.invalid/user');
   const header=new Headers(init?.headers).get('authorization');
   if(header!=='Bearer '+ownerToken&&header!=='Bearer '+outsiderToken)return Response.json({message:'Invalid fixture token'},{status:401});
   return Response.json({id:header==='Bearer '+outsiderToken?'outsider':'owner',factors:[verifiedFactor]});}) as typeof fetch);
 const request=()=>new Request(`http://local/${f.runtime}/rest/v1/`,{headers:{apikey:key.token}});
 const discovery=()=>new Request(`http://local/management/v1/environments/${f.environment}/connection`,{headers:{authorization:'Bearer '+ownerToken}});
 const db=new Database(f.path);
 try{
  f.catalog.changeRuntimeRouting(f.runtime,0,'pause');f.catalog.changeRuntimeRouting(f.runtime,1,'stage',native(f.runtime));
  expect((await gateway(request())).status).toBe(503);
  expect((await handler(request())).status).toBe(503);
  expect(endpoints).toBe(0);expect(gatewayTransport).toBe(0);expect(applicationTransport).toBe(0);
  // Corrupt persisted state models an invalid active record, not an authorized activation path.
  db.query('UPDATE runtime_routing SET maintenance=0 WHERE runtime=?').run(f.runtime);
  const refused=await gateway(request());expect(refused.status).toBe(503);
  expect(await refused.json()).toEqual({message:'Native dedicated placement is not admitted'});
  expect((await handler(request())).status).toBe(503);
  expect((await handler(discovery())).status).toBe(503);
  expect((await handler(new Request(`http://local/management/v1/environments/${f.environment}/connection`,{headers:{authorization:'Bearer '+outsiderToken}}))).status).toBe(403);
  // Protected discovery checks native Auth at admission, handler authentication and response publication.
  expect(endpoints).toBe(0);expect(gatewayTransport).toBe(0);expect(applicationTransport).toBe(6);
  for(const invalid of [{...native(f.runtime),version:2},{...native(f.runtime),profile:'unknown'},
   {...native(f.runtime),runtime:'e_'+'f'.repeat(24)}, {...native(f.runtime),maintenance:undefined}]){
   db.query('UPDATE runtime_routing SET placement=? WHERE runtime=?').run(JSON.stringify(invalid),f.runtime);
   expect((await gateway(request())).status).toBe(503);
   expect((await handler(request())).status).toBe(503);
   expect((await handler(discovery())).status).toBe(503);
  }
  expect(endpoints).toBe(0);expect(gatewayTransport).toBe(0);expect(applicationTransport).toBe(18);
 }finally{db.close();f.close();}
});

test('public gateway authorizes keys before disclosing native placement and preserves supported keyless routes',async()=>{
 const f=fixture();const valid=f.keys.issue(f.runtime),foreign=f.keys.issue('e_'+'2'.repeat(24));
 let resolves=0,transports=0;
 const gateway=managedGateway(f.catalog,f.keys,()=>{resolves++;return {...configured,
  storage:{url:'http://storage.invalid',tenantHost:'legacy.storage'},functions:{url:'http://functions.invalid'}};},
  (async(_input)=>{transports++;return new Response(null,{status:204});}) as typeof fetch);
 const request=(path='rest/v1/',key?:string,method='GET')=>new Request(`http://local/${f.runtime}/${path}`,{method,
  headers:key===undefined?{}:{apikey:key}});
 const keyless=['auth/v1/authorize','auth/v1/verify','auth/v1/callback','storage/v1/object/public/bucket/file',
  'storage/v1/object/sign/bucket/file?token=signed','functions/v1/webhook'];
 const db=new Database(f.path);
 try{
  for(const key of [undefined,'invalid',foreign.token]){
   const response=await gateway(request('rest/v1/',key));expect(response.status).toBe(401);
   expect(await response.json()).toEqual({message:'Invalid API key'});
  }
  for(const path of keyless)expect((await gateway(request(path))).status).toBe(204);
  expect((await gateway(request('auth/v1/callback',undefined,'POST'))).status).toBe(204);
  expect((await gateway(request('rest/v1/',undefined,'OPTIONS'))).status).toBe(204);
  expect((await gateway(request('functions/v1/_internal','invalid'))).status).toBe(404);
  expect((await gateway(request('realtime/v1/tenants','invalid'))).status).toBe(404);
  f.catalog.changeRuntimeRouting(f.runtime,0,'pause');f.catalog.changeRuntimeRouting(f.runtime,1,'stage',native(f.runtime));
  resolves=0;transports=0;
  for(const declaration of [native(f.runtime),{...native(f.runtime),version:2},{...native(f.runtime),maintenance:undefined}]){
   db.query('UPDATE runtime_routing SET placement=? WHERE runtime=?').run(JSON.stringify(declaration),f.runtime);
   for(const key of [undefined,'invalid',foreign.token]){
    const response=await gateway(request('rest/v1/',key));expect(response.status).toBe(401);
    expect(await response.json()).toEqual({message:'Invalid API key'});
   }
   for(const path of ['auth/v1/user','storage/v1/object/private/bucket/file','storage/v1/object/sign/bucket/file?token=a&token=b'])
    expect((await gateway(request(path))).status).toBe(401);
   for(const path of keyless){
    const response=await gateway(request(path));expect(response.status).toBe(503);
    expect(await response.json()).toEqual({message:'Environment routing unavailable'});
    expect((await gateway(request(path,'invalid'))).status).toBe(401);
   }
   for(const [path,method] of [['auth/v1/callback','POST'],['rest/v1/','OPTIONS']]){
    const response=await gateway(request(path,undefined,method));expect(response.status).toBe(503);
    expect(await response.json()).toEqual({message:'Environment routing unavailable'});
   }
   expect((await gateway(request('rest/v1/',valid.token))).status).toBe(503);
  }
  expect(resolves).toBe(0);expect(transports).toBe(0);
  db.query('UPDATE runtime_routing SET placement=? WHERE runtime=?').run(JSON.stringify(native(f.runtime)),f.runtime);
  expect(await (await gateway(request('rest/v1/',valid.token))).json()).toEqual({message:'Native dedicated placement is not admitted'});
 }finally{db.close();f.close();}
});
