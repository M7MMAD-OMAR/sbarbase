import {test,expect} from 'bun:test';
import {databaseWorkflow,budgetStatus,connectionDiagnostic,type WorkflowEndpoint} from '../src/control/database-workflow';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
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
function fixture(){
 const directory=mkdtempSync(join(tmpdir(),'database-workflow-')),path=join(directory,'catalog.sqlite');
 const catalog=new Catalog(path),keys=new KeyStore(':memory:'),worker=new Database(path);
 const organization=catalog.createOrganization('owner','Organization');
 catalog.setMember('owner',organization,'admin','admin');
 const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'Project'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const now=Math.floor(Date.now()/1000),token=managementToken('admin','aal2',sessionId,now);
 catalog.managementSecurity.grant('admin',sessionId,factorId,now,now+3600,0);
 const handler=(transport:typeof fetch)=>application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport);
 const incoming=(method='GET',body?:ReadableStream<Uint8Array>)=>new Request(`http://local/management/v1/environments/${environment}/database`,{
  method,headers:{authorization:'Bearer '+token,'content-type':'application/json'},...(body?{body,duplex:'half'}:{})} as RequestInit);
 const transport=Object.assign(async()=>Response.json({id:'admin',factors:[verifiedFactor]}),{preconnect(){}}) as typeof fetch;
 return {catalog,worker,path,environment,organization,runtime:job.runtime,handler,incoming,transport,
  close(){worker.close();catalog.close();keys.close();rmSync(directory,{recursive:true,force:true});}};
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
  data=(await (await call(f.incoming())).json()).data;expect(data.workflow.endpoints[0].ready).toBe(true);
  expect(JSON.stringify(data.workflow)).not.toContain('password');expect(data.workflow.capacity).toBe('unmeasured');
 }finally{f.close();}
});
for(const revoke of ['owner','epoch'] as const)test(`composed workflow refuses stale ${revoke} during Auth read`,async()=>{
 const f=fixture();let release!:(value:Response)=>void,entered!:()=>void;
 const reached=new Promise<void>(resolve=>{entered=resolve;});
 const transport=Object.assign(async()=>{entered();return new Promise<Response>(resolve=>{release=resolve;});},{preconnect(){}}) as typeof fetch;
 try{
  const pending=f.handler(transport)(f.incoming());await reached;
  if(revoke==='owner')f.catalog.changeMember('owner',f.organization,'admin','viewer');
  else f.catalog.managementSecurity.revoke('admin');
  release(Response.json({id:'admin',factors:[verifiedFactor]}));
  const response=await pending;expect(response.status).toBe(403);expect(await response.text()).not.toContain('workflow');
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
