import {test,expect,beforeEach,afterEach} from 'bun:test';
import {chmodSync,copyFileSync,mkdirSync,mkdtempSync,readdirSync,rmSync,symlinkSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {deploymentSetupHandler,DeploymentSetupStore,prepareDeployment,type DeploymentIntent} from '../src/control/deployment-setup';
import {withManagementAuthorization} from '../src/control/management-context';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import {DeploymentSetup,deploymentSetupClient} from '../ui/DeploymentSetup';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';

const intent:DeploymentIntent={version:1,profile:'public',public_url:'https://api.example.org',ipv4:['8.8.8.8'],ipv6:[],
 provider_access:true,tls:'managed',acme_email:'operator@example.org',certificate_ref:null,
 site_url:'https://app.example.org',redirect_urls:['https://app.example.org/return'],smtp_ref:'environment-mail'};
let catalog:Catalog,directory:string,environment:string,installation:string,runtime:string,neighbor:string;
let handler:ReturnType<typeof deploymentSetupHandler>;
beforeEach(()=>{
 directory=mkdtempSync(join(tmpdir(),'sb08-setup-'));
 catalog=new Catalog(':memory:');installation=catalog.initializeInstallation('bootstrap-fixture','owner','Installation');
 catalog.setMember('owner',installation,'operator','admin');
 const project=catalog.createProject('owner',installation,'App');environment=catalog.createEnvironment('owner',project,'Production');
 neighbor=catalog.createEnvironment('owner',project,'Neighbor');
 for(let index=0;index<2;index++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
 runtime=catalog.getProvision('owner',environment).runtime;
 handler=deploymentSetupHandler(catalog,async request=>request.headers.get('authorization'),directory);
});
afterEach(()=>{catalog.close();rmSync(directory,{recursive:true,force:true});});
function request(method='GET',body:unknown=intent,actor='operator',revision=0,id=environment){
 return new Request(`http://local/management/v1/environments/${id}/deployment-setup`,{method,
  headers:{authorization:actor,'if-match':String(revision),'content-type':'application/json'},
  ...(method==='GET'?{}:{body:JSON.stringify(body)})});
}
function delayed(){
 let controller:ReadableStreamDefaultController<Uint8Array>,reading=()=>{};
 const started=new Promise<void>(resolve=>{reading=resolve;});
 const stream=new ReadableStream<Uint8Array>({start(value){controller=value;},pull(){reading();}},{highWaterMark:0});
 const pending=new Request(`http://local/management/v1/environments/${environment}/deployment-setup`,{
  method:'PUT',headers:{authorization:'operator','if-match':'0'},body:stream});
 return {request:pending,started,finish(){controller.enqueue(new TextEncoder().encode(JSON.stringify(intent)));controller.close();}};
}

test('preview validates the exact callback without persisting setup files',async()=>{
 const response=await handler(request('POST'));expect(response.status).toBe(200);
 const view=await response.json();expect(view.data.callback_url).toBe(`https://api.example.org/${runtime}/auth/v1/callback`);
 expect(view.data.production_ready).toBe(false);expect(readdirSync(directory)).toEqual([]);
});
test('prepared state is durable private CAS, without claiming runtime application',async()=>{
 const response=await handler(request('PUT'));expect(response.status).toBe(202);
 const view=await response.json();expect(view.data.revision).toBe(1);expect(view.data.state).toBe('prepared');
 expect(view.data.runtime_applied).toBe(false);expect(view.data.production_ready).toBe(false);
 expect(statSync(join(directory,'setup.sqlite')).mode&0o777).toBe(0o600);
 expect((await handler(request('PUT'))).status).toBe(409);
 expect((await (await handler(request())).json()).data.revision).toBe(1);
 expect((await handler(request('PUT',intent,'operator',1))).status).toBe(202);
});
test('literal local origin cannot persist a preparation without its matching bind address',async()=>{
 const local={...intent,profile:'local',tls:'local',acme_email:'',public_url:'https://127.0.0.1:8443',
  ipv4:[],ipv6:['::1'],site_url:'http://localhost:3000',redirect_urls:[]};
 expect((await handler(request('PUT',local))).status).toBe(400);expect(readdirSync(directory)).toEqual([]);
 const aligned={...local,ipv4:['127.0.0.1'],ipv6:[]};
 expect((await handler(request('POST',aligned))).status).toBe(200);expect(readdirSync(directory)).toEqual([]);
});
test('neighbor configuration stays independent',async()=>{
 await handler(request('PUT'));
 const other=await (await handler(request('GET',intent,'operator',0,neighbor))).json();
 expect(other.data.state).toBe('unconfigured');expect(other.data.intent).toBeNull();
});
test('client-supplied SMTP passwords and TLS keys are refused and redacted',async()=>{
 for(const field of ['smtp_password','tls_key','provider_token']){
  const response=await handler(request('PUT',{...intent,[field]:'SYNTHETIC_SECRET'}));expect(response.status).toBe(400);
  expect(await response.text()).not.toContain('SYNTHETIC_SECRET');
 }
 expect(readdirSync(directory)).toEqual([]);
});
test('duplicate JSON fields are refused before saving',async()=>{
 const raw=JSON.stringify(intent).replace('"version":1','"version":1,"version":1');
 const response=await handler(new Request(request('PUT'),{body:raw}));expect(response.status).toBe(400);
 expect(readdirSync(directory)).toEqual([]);
});
test('oversized and chunked bodies cannot allocate intent state',async()=>{
 const raw='x'.repeat(32769),response=await handler(new Request(request('PUT'),{body:raw}));
 expect(response.status).toBe(400);expect(readdirSync(directory)).toEqual([]);
});
test('role denial includes installation viewer and other organization owner',async()=>{
 catalog.setMember('owner',installation,'viewer','viewer');catalog.createOrganization('other-owner','Other');
 for(const actor of ['viewer','other-owner','unknown'])expect((await handler(request('PUT',intent,actor))).status).toBe(403);
 expect(readdirSync(directory)).toEqual([]);
});
test('missing identity and unknown route refuse before body or file effects',async()=>{
 const denied=deploymentSetupHandler(catalog,async()=>null,directory);
 expect((await denied(request('PUT'))).status).toBe(401);
 expect((await handler(new Request('http://local/management/v1/anything'))).status).toBe(404);
 expect((await handler(request('DELETE'))).status).toBe(405);expect(readdirSync(directory)).toEqual([]);
});
for(const role of ['viewer',null] as const){
 test(`operator ${role??'removed'} during deferred body causes no effect`,async()=>{
  const body=delayed(),pending=handler(body.request);await body.started;
  catalog.changeMember('owner',installation,'operator',role);body.finish();
  expect((await pending).status).toBe(403);expect(readdirSync(directory)).toEqual([]);
 });
}
test('durable actor epoch revocation during deferred body causes no effect',async()=>{
 const body=delayed(),pending=handler(body.request);await body.started;
 catalog.managementSecurity.revoke('operator');body.finish();
 expect((await pending).status).toBe(403);expect(readdirSync(directory)).toEqual([]);
});
test('current MFA scope invalidated during deferred body causes no effect',async()=>{
 let current=true;const body=delayed(),pending=withManagementAuthorization(()=>current,()=>handler(body.request));await body.started;
 current=false;body.finish();expect((await pending).status).toBe(403);expect(readdirSync(directory)).toEqual([]);
});
test('fresh identity failure after validation causes no effect',async()=>{
 let calls=0;const local=deploymentSetupHandler(catalog,async()=>++calls===1?'operator':null,directory);
 expect((await local(request('PUT'))).status).toBe(403);expect(calls).toBe(2);expect(readdirSync(directory)).toEqual([]);
});
test('two controllers serialize revision conflicts across private store connections',async()=>{
 const prepared=await prepareDeployment(new TextEncoder().encode(JSON.stringify(intent)),runtime);
 const first=new DeploymentSetupStore(directory),second=new DeploymentSetupStore(directory);
 try{
  first.save(environment,runtime,'operator',0,0,prepared,0);
  expect(()=>second.save(environment,runtime,'operator',0,0,prepared,0)).toThrow('revision_conflict');
  expect(second.get(environment,runtime).revision).toBe(1);
 }finally{first.close();second.close();}
});
test('private directory, symlink and FIFO references are refused',async()=>{
 const unsafe=join(directory,'unsafe');symlinkSync(directory,unsafe);
 const local=deploymentSetupHandler(catalog,async()=> 'operator',unsafe);
 expect((await local(request('PUT'))).status).toBe(400);
 chmodSync(directory,0o755);
 expect((await handler(request('PUT'))).status).toBe(400);
 chmodSync(directory,0o700);
 symlinkSync(join(directory,'missing'),join(directory,'setup.sqlite'));
 expect((await handler(request('PUT'))).status).toBe(400);
});
test('public origin and unknown path never choose an executable or private file path',async()=>{
 for(const input of [{...intent,certificate_ref:'../secret'},{...intent,public_url:'https://user:secret@api.example.org'},
  {...intent,smtp_ref:'/private/password'}])expect((await handler(request('PUT',input))).status).toBe(400);
 expect(readdirSync(directory)).toEqual([]);
});
test('GET response is uncached and cannot label a preparation as production ready',async()=>{
 const response=await handler(request());expect(response.headers.get('cache-control')).toBe('no-store');
 const view=await response.json();expect(view.data.state).toBe('unconfigured');expect(view.data.production_ready).toBe(false);
});
test('wizard initial render identifies shared scope without credential controls',()=>{
 const markup=renderToStaticMarkup(createElement(DeploymentSetup,{environmentName:'Production',client:{
  load:async()=>({revision:0,operation:null,state:'unconfigured',intent:null,preview:null,runtime_applied:false,production_ready:false} as const),
  preview:async()=>{throw new Error('not called');},save:async()=>{throw new Error('not called');}}}));
 expect(markup).toContain('Domain and email setup');expect(markup).toContain('origin is shared');
 expect(markup).toContain('role="status"');expect(markup).not.toContain('type="password"');
});

function wiredApplication(){
 const previous=process.cwd(),keys=new KeyStore(':memory:'),now=Math.floor(Date.now()/1000);
 const realm={auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'};
 const transport=(async(input,init)=>{
  if(String(input)!=='http://realm.invalid/user')throw new Error('Unexpected fixture transport');
  const token=new Headers(init?.headers).get('authorization')?.slice(7)??'';
  const claims=JSON.parse(Buffer.from(token.split('.')[1]!,'base64url').toString('utf8'));
  return Response.json({id:claims.sub,is_anonymous:false,factors:[verifiedFactor]});
 }) as typeof fetch;
 mkdirSync(join(directory,'lab'),{mode:0o700});mkdirSync(join(directory,'.secrets'),{mode:0o700});
 copyFileSync(join(previous,'lab/domain_transport.py'),join(directory,'lab/domain_transport.py'));
 process.chdir(directory);
 let app:ReturnType<typeof application>;
 try{app=application(catalog,keys,realm,()=>undefined,transport);}finally{process.chdir(previous);}
 return {app,keys,now,token:managementToken('operator','aal2',sessionId,now),
  grant:()=>catalog.managementSecurity.grant('operator',sessionId,factorId,now,now+3600,catalog.managementSecurity.epoch('operator'))};
}

test('actual dispatcher routes setup under the assembled application MFA admission',async()=>{
 const fixture=wiredApplication();try{
  const auth={authorization:'Bearer '+fixture.token,'if-match':'0','content-type':'application/json'};
  const call=(method:string)=>fixture.app(new Request(request(method),{headers:auth}));
  expect((await call('POST')).status).toBe(403);fixture.grant();
  const preview=await call('POST');expect(preview.status).toBe(200);
  expect((await preview.json()).data.callback_url).toContain(runtime);
  expect((await call('PUT')).status).toBe(202);
  expect(statSync(join(directory,'.secrets','deployment','setup.sqlite')).mode&0o777).toBe(0o600);
  catalog.managementSecurity.revoke('operator');expect((await call('GET')).status).toBe(403);
 }finally{fixture.keys.close();}
});

test('assembled application revokes a pending setup before any private intent effect',async()=>{
 const fixture=wiredApplication();try{
  fixture.grant();const body=delayed();
  body.request.headers.set('authorization','Bearer '+fixture.token);
  const pending=fixture.app(body.request);
  const entry=await Promise.race([body.started.then(()=>({started:true})),pending.then(async response=>({started:false,status:response.status,text:await response.text()}))]);
  expect(entry).toEqual({started:true});catalog.managementSecurity.revoke('operator');body.finish();
  expect((await pending).status).toBe(403);expect(readdirSync(join(directory,'.secrets'))).toEqual([]);
 }finally{fixture.keys.close();}
});

test('maintenance or changed routing during deferred setup invalidates its operation binding',async()=>{
 const body=delayed(),pending=handler(body.request);await body.started;
 catalog.changeRuntimeRouting(runtime,0,'pause');body.finish();
 expect((await pending).status).toBe(409);expect(readdirSync(directory)).toEqual([]);
});

test('wizard API reads the current token and revision for each request',async()=>{
 const original=globalThis.fetch;let token='fixture-first';const headers:Headers[]=[],methods:string[]=[];
 globalThis.fetch=(async(_input,init)=>{headers.push(new Headers(init?.headers));methods.push(init?.method??'GET');
  return Response.json({data:{revision:1,state:'prepared',runtime_applied:false,production_ready:false}});
 }) as typeof fetch;
 try{
  const client=deploymentSetupClient(environment,()=>token),controller=new AbortController();
  await client.load(controller.signal);token='fixture-refreshed';await client.save(intent,3,controller.signal);
  expect(methods).toEqual(['GET','PUT']);expect(headers[0]!.get('authorization')).toBe('Bearer fixture-first');
  expect(headers[1]!.get('authorization')).toBe('Bearer fixture-refreshed');expect(headers[1]!.get('if-match')).toBe('3');
 }finally{globalThis.fetch=original;}
});
