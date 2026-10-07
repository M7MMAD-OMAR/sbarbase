import {test,expect,spyOn} from 'bun:test';
import {databaseWorkflow,databaseTunnel,budgetStatus,connectionDiagnostic,type WorkflowEndpoint} from '../src/control/database-workflow';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {DirectDatabase,directDatabaseEndpoint} from '../src/http/database-proxy';
import {createServer} from 'node:net';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync,existsSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';
const runtime='e_'+'a'.repeat(24),other='e_'+'b'.repeat(24);
const direct:WorkflowEndpoint={mode:'direct',host:'127.0.0.1',port:6543,user:runtime+'_developer',database:runtime,ready:true};
const session:WorkflowEndpoint={...direct,mode:'session',port:5432,user:runtime+'_developer.'+runtime};
const transaction:WorkflowEndpoint={...session,mode:'transaction',port:6544};
const budget={usable:97,promised:87,operationsReserve:10,developer:10,poolBackends:4,poolClients:100};
test('migrations use explicit direct mode despite port6543',()=>{
 const view=databaseWorkflow(runtime,'migration',[direct,session,transaction]);
 expect(view.mode).toBe('direct');expect(view.url).toContain(':6543/');expect(view.preparedStatements).toBe(true);
});
test('serverless choice explains transaction limitations',()=>{
 const view=databaseWorkflow(runtime,'serverless',[direct,transaction]);
 expect(view.mode).toBe('transaction');expect(view.sessionState).toBe(false);expect(view.preparedStatements).toBe(false);
 expect(view.message).toContain('Disable prepared statements');
});
test('session-state requires an admitted session endpoint',()=>{
 expect(databaseWorkflow(runtime,'session-state',[direct]).available).toBe(false);
 expect(databaseWorkflow(runtime,'session-state',[session]).sessionState).toBe(true);
});
test('missing transaction pool never silently uses direct',()=>{
 const view=databaseWorkflow(runtime,'serverless',[direct]);expect(view.available).toBe(false);expect(view.url).toBeNull();
});
test('pending endpoint is not published',()=>{
 expect(databaseWorkflow(runtime,'migration',[{...direct,ready:false}]).url).toBeNull();
});
test('templates never accept supplied password or cross-environment login',()=>{
 for(const endpoint of [{...direct,user:runtime+'_developer:secret'}, {...direct,database:other},
  {...transaction,user:other+'_developer.'+runtime},{...direct,host:'owner:secret@localhost'}])
  expect(()=>databaseWorkflow(runtime,'migration',[endpoint])).toThrow();
 expect(databaseWorkflow(runtime,'migration',[direct]).url).toContain('[YOUR-PASSWORD]');
});
test('IPv6 host stays bracketed and ports are finite',()=>{
 expect(databaseWorkflow(runtime,'migration',[{...direct,host:'[::1]'}]).url).toContain('@[::1]:');
 for(const port of [0,-1,65536,Infinity,1.5])expect(()=>databaseWorkflow(runtime,'migration',[{...direct,port}])).toThrow();
});
test('SSH tunnel separates client loopback from the server destination and publishes its local URL',()=>{
 expect(databaseTunnel(direct)).toEqual({command:'ssh -L 127.0.0.1:6543:127.0.0.1:6543 you@your-server',
  url:`postgresql://${runtime}_developer:[YOUR-PASSWORD]@127.0.0.1:6543/${runtime}`});
 for(const host of ['::1','[::1]']){
  const tunnel=databaseTunnel({...direct,host});
  expect(tunnel.command).toBe('ssh -L [::1]:6543:[::1]:6543 you@your-server');
  expect(tunnel.url).toContain('@[::1]:6543/');
 }
 for(const host of ['10.0.0.5','192.168.1.50','db.example.test','localhost']){
  const tunnel=databaseTunnel({...direct,host});
  expect(tunnel.command).toBe(`ssh -L 127.0.0.1:6543:${host}:6543 you@your-server`);
  expect(tunnel.url).toBe(`postgresql://${runtime}_developer:[YOUR-PASSWORD]@127.0.0.1:6543/${runtime}`);
 }
 expect(databaseWorkflow(runtime,'migration',[{...direct,host:'[::1]'}]).url).toContain('@[::1]:6543/');
 for(const host of ['owner:secret@localhost','::1;command','[::1]:6543'])
  expect(()=>databaseTunnel({...direct,host})).toThrow();
 for(const port of [0,65536,NaN,Infinity])expect(()=>databaseTunnel({...direct,port})).toThrow();
});
test('duplicate or unknown endpoint mode is refused',()=>{
 expect(()=>databaseWorkflow(runtime,'migration',[direct,direct])).toThrow();
 expect(()=>databaseWorkflow(runtime,'migration',[{...direct,mode:'unknown' as any}])).toThrow();
});
test('prepared transactions are distinct from prepared statements',()=>{
 const view=databaseWorkflow(runtime,'prepared-transaction',[direct,transaction]);
 expect(view.mode).toBe('direct');expect(view.message).toContain('max_prepared_transactions');expect(view.message).toContain('rollback');
});
test('aggregate operator reserve is retained at boundary',()=>{
 expect(budgetStatus(budget).fits).toBe(true);expect(budgetStatus(budget).available).toBe(0);
 expect(budgetStatus({...budget,promised:88}).fits).toBe(false);
 expect(()=>budgetStatus({...budget,operationsReserve:9})).toThrow();
});
test('invalid or inconsistent budget refused',()=>{
 for(const value of [-1,NaN,Infinity,1.5])expect(()=>budgetStatus({...budget,poolClients:value})).toThrow();
 expect(()=>budgetStatus({...budget,developer:3})).toThrow();
 expect(()=>budgetStatus({...budget,poolClients:3})).toThrow();
});
test('unmeasured budget stays unavailable',()=>expect(databaseWorkflow(runtime,'migration',[direct]).budget).toBeNull());
test('diagnostics are actionable without echoing untrusted text',()=>{
 expect(connectionDiagnostic('53300')).toContain('Reduce application pool');
 expect(connectionDiagnostic('28P01')).toContain('current password');
 expect(connectionDiagnostic('42501')).toContain('RLS policy');
 expect(connectionDiagnostic('42P05')).toContain('disable prepared statements');
 expect(connectionDiagnostic('57014')).toContain('locks');
 expect(connectionDiagnostic('password-secret')).not.toContain('password-secret');
});

/** Real Catalog/application composition with an explicitly synthetic Auth transport.
 * These source regressions do not establish native Auth, SQL or pooler acceptance. */
function fixture(options:{tokenClaims?:Record<string,unknown>}={}){
 const directory=mkdtempSync(join(tmpdir(),'database-workflow-')),path=join(directory,'catalog.sqlite');
 const catalog=new Catalog(path),keys=new KeyStore(':memory:'),worker=new Database(path),direct=new DirectDatabase();
 const organization=catalog.createOrganization('owner','Organization');
 catalog.setMember('owner',organization,'admin','admin');
 const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'Project'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const now=Math.floor(Date.now()/1000),token=managementToken('admin','aal2',sessionId,now,options.tokenClaims);
 catalog.managementSecurity.grant('admin',sessionId,factorId,now,now+3600,0);
 const handler=(transport:typeof fetch)=>application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport,undefined,undefined,undefined,undefined,direct);
 const incoming=(method='GET',body?:ReadableStream<Uint8Array>)=>new Request(`http://local/management/v1/environments/${environment}/database`,{
  method,headers:{authorization:'Bearer '+token,'content-type':'application/json'},...(body?{body,duplex:'half'}:{})} as RequestInit);
 const transport=Object.assign(async()=>Response.json({id:'admin',factors:[verifiedFactor]}),{preconnect(){}}) as typeof fetch;
 const passwordPath=join(process.cwd(),'.secrets/upstream/database',`${job.runtime}.json`);
 return {catalog,worker,path,environment,organization,runtime:job.runtime,handler,incoming,transport,direct,passwordPath,now,
  close(){direct.stop();worker.close();catalog.close();keys.close();rmSync(passwordPath,{force:true});rmSync(passwordPath+'.pending',{force:true});rmSync(directory,{recursive:true,force:true});}};
}
test('composed GET exposes only applied direct state and no invented pool or budget',async()=>{
 const f=fixture();
 try{
  const call=f.handler(f.transport);
  let data=(await (await call(f.incoming())).json()).data;
  expect(data.workflow.endpoints).toHaveLength(1);expect(data.workflow.endpoints[0].mode).toBe('direct');
  expect(data.workflow.endpoints[0].ready).toBe(false);expect(data.workflow.pooling).toBe('pending');expect(data.workflow.budget).toBeNull();
  f.catalog.requestDatabaseAccess('owner',f.environment,true);
  data=(await (await call(f.incoming())).json()).data;expect(data.workflow.endpoints[0].ready).toBe(false);
  f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  data=(await (await call(f.incoming())).json()).data;expect(data.workflow.endpoints[0].ready).toBe(false);
  expect(JSON.stringify(data.workflow)).not.toContain('password');expect(data.workflow.capacity).toBe('unmeasured');
 }finally{f.close();}
});
/** Real loopback listeners with synthetic targets, only source consumer/lifecycle evidence. */
test('composed readiness follows owned bind, actual target callback, hold and stop',async()=>{
 const f=fixture(),target=createServer(socket=>socket.end());let held=false,available=true;
 const beforePort=process.env.SBARBASE_DATABASE_PORT,beforeBind=process.env.SBARBASE_DATABASE_BIND;
 await new Promise<void>(resolve=>target.listen(0,'127.0.0.1',resolve));
 const port=(target.address() as {port:number}).port;
 try{
  f.catalog.requestDatabaseAccess('owner',f.environment,true);f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  const call=f.handler(f.transport),ready=async()=>((await (await call(f.incoming())).json()).data.workflow.endpoints[0]);
  expect((await ready()).ready).toBe(false);
  await f.direct.start({host:'127.0.0.1',port:0,target:()=>!held&&available?{host:'127.0.0.1',port}:undefined});
  const active=await ready();expect(active.ready).toBe(true);expect(active.port).toBe(directDatabaseEndpoint(f.direct)!.port);
  process.env.SBARBASE_DATABASE_PORT='6544';process.env.SBARBASE_DATABASE_BIND='::1';
  const stillBound=await ready();expect(stillBound.ready).toBe(true);expect(stillBound.host).toBe('127.0.0.1');expect(stillBound.port).toBe(active.port);
  available=false;expect((await ready()).ready).toBe(false);
  available=true;held=true;expect((await ready()).ready).toBe(false);
  held=false;expect((await ready()).ready).toBe(true);
  f.direct.stop();expect((await ready()).ready).toBe(false);
 }finally{
  if(beforePort===undefined)delete process.env.SBARBASE_DATABASE_PORT;else process.env.SBARBASE_DATABASE_PORT=beforePort;
  if(beforeBind===undefined)delete process.env.SBARBASE_DATABASE_BIND;else process.env.SBARBASE_DATABASE_BIND=beforeBind;
  f.close();target.close();
 }
});
test('composed applied state stays unavailable after actual bind refusal',async()=>{
 const f=fixture(),occupied=createServer();
 await new Promise<void>(resolve=>occupied.listen(0,'127.0.0.1',resolve));
 const port=(occupied.address() as {port:number}).port;
 try{
  f.catalog.requestDatabaseAccess('owner',f.environment,true);f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  await expect(f.direct.start({host:'127.0.0.1',port,target:()=>({host:'127.0.0.1',port})})).rejects.toThrow();
  expect(directDatabaseEndpoint(f.direct)).toBeUndefined();
  const response=await f.handler(f.transport)(f.incoming());expect(response.status).toBe(200);
  expect((await response.json()).data.workflow.endpoints[0].ready).toBe(false);
 }finally{f.close();occupied.close();}
});
for(const revoke of ['removed-owner','demoted-owner','epoch','runtime'] as const)test(`outer response await refuses ${revoke} publication`,async()=>{
 const f=fixture(),second=new Catalog(f.path);let armed=false,changed=false;
 const json=Response.json.bind(Response);
 // Queue a second-connection commit only after the actual handler response exists.
 // It runs while application awaits control, before its final synchronous publication.
 const hook=spyOn(Response,'json').mockImplementation((data,init)=>{
  const response=json(data,init);
  if(!armed&&(data as any)?.data?.workflow){armed=true;queueMicrotask(()=>{
   if(revoke==='removed-owner')second.changeMember('owner',f.organization,'admin',null);
   else if(revoke==='demoted-owner')second.changeMember('owner',f.organization,'admin','viewer');
   else if(revoke==='epoch')second.managementSecurity.revoke('admin');
   else f.worker.query('UPDATE provision_jobs SET runtime=? WHERE environment=?').run(other,f.environment);
   changed=true;
  });}
  return response;
 });
 try{
  const response=await f.handler(f.transport)(f.incoming());expect(armed&&changed).toBe(true);
  expect(response.status).toBe(403);expect(await response.text()).not.toContain('workflow');
 }finally{hook.mockRestore();second.close();f.close();}
});
test('final private transaction refuses matching durable MFA grant deletion without an epoch change',async()=>{
 const f=fixture();let snapshots=0,deleted=false;
 const snapshot=f.catalog.databaseWorkflowState.bind(f.catalog);
 const hook=spyOn(f.catalog,'databaseWorkflowState').mockImplementation((...args)=>{
  snapshots++;
  // Initial authority, initial response and its recheck run before the outer await.
  // Delete the real durable session grant after the explicit final MFA check,
  // immediately before the fourth, final private Catalog transaction begins.
  if(snapshots===4){
   const result=f.worker.query('DELETE FROM management_mfa_grant WHERE actor=? AND session=?').run('admin',sessionId);
   expect(result.changes).toBe(1);deleted=true;
  }
  return snapshot(...args);
 });
 try{
  const response=await f.handler(f.transport)(f.incoming());
  expect(deleted).toBe(true);expect(f.catalog.managementSecurity.epoch('admin')).toBe(0);
  expect(response.status).toBe(403);expect(await response.text()).not.toContain('workflow');
 }finally{hook.mockRestore();f.close();}
});
for(const transition of ['hold','stop'] as const)test(`outer response await refreshes live listener ${transition} before publishing readiness`,async()=>{
 const f=fixture(),target=createServer(socket=>socket.end());let held=false,armed=false;
 await new Promise<void>(resolve=>target.listen(0,'127.0.0.1',resolve));
 const port=(target.address() as {port:number}).port;
 const json=Response.json.bind(Response);
 const hook=spyOn(Response,'json').mockImplementation((data,init)=>{
  const response=json(data,init);
  if(!armed&&(data as any)?.data?.workflow){armed=true;queueMicrotask(()=>{held=true;if(transition==='stop')f.direct.stop();});}
  return response;
 });
 try{
  f.catalog.requestDatabaseAccess('owner',f.environment,true);f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  await f.direct.start({host:'127.0.0.1',port:0,target:()=>held?undefined:{host:'127.0.0.1',port}});
  const response=await f.handler(f.transport)(f.incoming());expect(armed&&held).toBe(true);
  expect((await response.json()).data.workflow.endpoints[0].ready).toBe(false);
 }finally{hook.mockRestore();f.close();target.close();}
});
for(const revoke of ['owner','epoch'] as const)test(`composed workflow refuses stale ${revoke} during Auth read`,async()=>{
 const f=fixture();let release!:(value:Response)=>void,entered!:()=>void,reads=0;
 const reached=new Promise<void>(resolve=>{entered=resolve;});
 const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  expect(String(input)).toBe('http://realm.invalid/user');
  expect(new Headers(init?.headers).get('authorization')).toBe(f.incoming().headers.get('authorization'));
  // Hold the initial native lookup while authority changes; renewed lookups remain available.
  if(reads++===0){entered();return new Promise<Response>(resolve=>{release=resolve;});}
  return Response.json({id:'admin',factors:[verifiedFactor]});
 },{preconnect(){}}) as typeof fetch;
 try{
  const pending=f.handler(transport)(f.incoming());await reached;
  if(revoke==='owner')f.catalog.changeMember('owner',f.organization,'admin','viewer');
  else f.catalog.managementSecurity.revoke('admin');
  release(Response.json({id:'admin',factors:[verifiedFactor]}));
  const response=await pending;expect(response.status).toBe(403);expect(await response.text()).not.toContain('workflow');
  expect(reads).toBe(revoke==='owner'?3:1);
 }finally{f.close();}
});
test('composed deferred body cannot publish password after MFA epoch revocation',async()=>{
 const f=fixture();let controller!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(value){controller=value;}});
 try{
  const incoming=f.incoming('PUT',body),pending=f.handler(f.transport)(incoming);
  for(let turn=0;turn<100&&!incoming.body!.locked;turn++)await Promise.resolve();
  expect(incoming.body!.locked).toBe(true);f.catalog.managementSecurity.revoke('admin');
  controller.enqueue(new TextEncoder().encode('{"enabled":true}'));controller.close();
  const response=await pending;expect(response.status).toBe(403);expect(await response.text()).not.toContain('password');
  expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('off');
 }finally{f.close();}
});
test('disabled direct listener is never advertised ready',async()=>{
 const f=fixture(),before=process.env.SBARBASE_DATABASE_PORT;
 try{
  f.catalog.requestDatabaseAccess('owner',f.environment,true);f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  process.env.SBARBASE_DATABASE_PORT='off';
  const response=await f.handler(f.transport)(f.incoming());expect(response.status).toBe(200);
  expect((await response.json()).data.workflow.endpoints[0].ready).toBe(false);
 }finally{if(before===undefined)delete process.env.SBARBASE_DATABASE_PORT;else process.env.SBARBASE_DATABASE_PORT=before;f.close();}
});
test('transactional workflow snapshot rejects stale epoch and mismatched runtime',()=>{
 const f=fixture();
 try{
  expect(f.catalog.databaseWorkflowState('admin',f.environment,0,f.runtime).runtime).toBe(f.runtime);
  expect(()=>f.catalog.databaseWorkflowState('admin',f.environment,0,other)).toThrow('Forbidden');
  for(const epoch of [-1,NaN,1.5,Infinity])expect(()=>f.catalog.databaseWorkflowState('admin',f.environment,epoch)).toThrow('Forbidden');
  f.catalog.managementSecurity.revoke('admin');
  expect(()=>f.catalog.databaseWorkflowState('admin',f.environment,0,f.runtime)).toThrow('Forbidden');
  expect(f.catalog.databaseWorkflowState('admin',f.environment,1,f.runtime).runtime).toBe(f.runtime);
 }finally{f.close();}
});
test('second Catalog owner removal invalidates workflow snapshot on the first connection',()=>{
 const f=fixture();
 const second=new Catalog(f.path);
 try{
  expect(f.catalog.databaseWorkflowState('admin',f.environment,0,f.runtime).runtime).toBe(f.runtime);
  second.changeMember('owner',f.organization,'admin',null);
  expect(()=>f.catalog.databaseWorkflowState('admin',f.environment,0,f.runtime)).toThrow('Forbidden');
 }finally{second.close();f.close();}
});


// Advance only the test clock. Native getUser still runs through the real application identity adapter.
for(const boundary of ['bearer-expiry','password-age'] as const){
 const claims=(now:number)=>boundary==='bearer-expiry'?{exp:now+1}:{exp:now+3600,
  amr:[{method:'password',timestamp:now-43199},{method:'totp',timestamp:now}]};
 for(const point of ['deferred-body','before-save','response-await','publication-transaction'] as const)
 test(`composed ${boundary} at ${point} refuses stale authority with a live durable grant`,async()=>{
  let clock=Math.floor(Date.now()/1000)*1000;
  const clockHook=spyOn(Date,'now').mockImplementation(()=>clock),f=fixture({tokenClaims:claims(clock/1000)});
  const json=Response.json.bind(Response),snapshot=f.catalog.databaseWorkflowState.bind(f.catalog);
  let crossed=false,constructed=false,snapshots=0,secret='',nativeReads=0;
  const advance=()=>{clock+=1000;crossed=true;};
  const responseHook=spyOn(Response,'json').mockImplementation((data,init)=>{
   const response=json(data,init);
   if(!constructed&&(data as any)?.data?.password){
    constructed=true;secret=(data as any).data.password;
    expect(clock).toBe(f.now*1000);
    expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(secret);
    if(point==='response-await')queueMicrotask(advance);
   }
   return response;
  });
  const stateHook=spyOn(f.catalog,'databaseWorkflowState').mockImplementation((...args)=>{
   snapshots++;
   // The second snapshot is the synchronous guard immediately before savePassword.
   // The fifth snapshot is the private publication transaction after the outer await.
   if((point==='before-save'&&snapshots===2)||(point==='publication-transaction'&&snapshots===5))advance();
   return snapshot(...args);
  });
  const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
   nativeReads++;expect(new URL(String(input)).pathname).toBe('/user');
   expect(new Headers(init?.headers).get('authorization')).toBe(f.incoming().headers.get('authorization'));
   return f.transport(input,init);
  },{preconnect(){}}) as typeof fetch;
  let controller!:ReadableStreamDefaultController<Uint8Array>;
  const body=new ReadableStream<Uint8Array>({start(value){controller=value;}});
  try{
   expect(existsSync(f.passwordPath)).toBe(false);
   const incoming=f.incoming('PUT',body),pending=f.handler(transport)(incoming);
   if(point==='deferred-body'){
    for(let turn=0;turn<100&&!incoming.body!.locked;turn++)await Promise.resolve();
    expect(incoming.body!.locked).toBe(true);expect(nativeReads).toBe(2);advance();
   }
   controller.enqueue(new TextEncoder().encode('{"enabled":true}'));controller.close();
   const response=await pending;
   // Authentication, pre-effect refresh and final publication use the actual original adapter.
   // A synchronous expiry refusal prevents any later native lookup from running.
   expect(crossed).toBe(true);expect(nativeReads).toBe(point==='deferred-body'?2:point==='publication-transaction'?4:3);
   expect(f.catalog.managementSecurity.granted('admin',sessionId,f.now,[factorId])).toBe(true);
   expect(f.catalog.managementSecurity.epoch('admin')).toBe(0);
   expect(response.status).toBe(403);const published=await response.text();
   expect(published).not.toContain('password');
   if(point==='deferred-body'||point==='before-save'){
    expect(constructed).toBe(false);expect(existsSync(f.passwordPath)).toBe(false);
    expect(existsSync(f.passwordPath+'.pending')).toBe(false);
    expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('off');
   }else{
    expect(constructed).toBe(true);expect(secret).toMatch(/^[A-Za-z0-9_-]{32}$/);
    expect(published).not.toContain(secret);
    expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(secret);
   }
  }finally{stateHook.mockRestore();responseHook.mockRestore();clockHook.mockRestore();f.close();}
 });
}

test('composed live native session can save and publish its one-time password',async()=>{
 let clock=Math.floor(Date.now()/1000)*1000;
 const clockHook=spyOn(Date,'now').mockImplementation(()=>clock),f=fixture({tokenClaims:{exp:clock/1000+1}});
 const body=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new TextEncoder().encode('{"enabled":true}'));controller.close();}});
 try{
  expect(existsSync(f.passwordPath)).toBe(false);
  const response=await f.handler(f.transport)(f.incoming('PUT',body));expect(response.status).toBe(202);
  const password=(await response.json()).data.password;expect(password).toMatch(/^[A-Za-z0-9_-]{32}$/);
  expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(password);
  expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('on');
 }finally{clockHook.mockRestore();f.close();}
});
