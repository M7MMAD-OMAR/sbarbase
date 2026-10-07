import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Database} from 'bun:sqlite';
import {Catalog} from '../src/control/catalog';
import {databaseHandler} from '../src/control/database';
import {publishDatabaseResponse} from '../src/control/database-publication';

/** Controlled source publication regressions, no native Auth, PostgreSQL or admission proof. */
function fixture(){
 const directory=mkdtempSync(join(tmpdir(),'database-publication-')),path=join(directory,'catalog.sqlite');
 const catalog=new Catalog(path),second=new Catalog(path),worker=new Database(path);
 const organization=catalog.createOrganization('owner','Organization');catalog.setMember('owner',organization,'admin','admin');
 const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'Project'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const handler=databaseHandler(catalog,async()=> 'admin',directory);
 const incoming=(method='GET',password=false,input:unknown={enabled:true})=>new Request(
  `http://local/management/v1/environments/${environment}/database${password?'/password':''}`,{
   method,headers:{'content-type':'application/json'},...(method==='PUT'?{body:JSON.stringify(input)}:{})});
 return {catalog,second,worker,organization,environment,runtime:job.runtime,directory,handler,incoming,
  close(){worker.close();second.close();catalog.close();rmSync(directory,{recursive:true,force:true});}};
}

for(const revoke of ['owner','epoch'] as const)test(`constructed one-time secret is withheld after ${revoke} changes during response await`,async()=>{
 const f=fixture();
 try{
  const response=await f.handler(f.incoming('PUT'));expect(response.status).toBe(202);
  const password=JSON.parse(readFileSync(join(f.directory,`${f.runtime}.json`),'utf8')).password;
  if(revoke==='owner')f.second.changeMember('owner',f.organization,'admin',null);
  else f.second.managementSecurity.revoke('admin');
  // The bound original Response has not been parsed or consumed to perform the final check.
  const published=publishDatabaseResponse(f.catalog,response);expect(published.status).toBe(403);
  expect(await published.text()).not.toContain(password);
  expect(publishDatabaseResponse(f.catalog,response).status).toBe(403);
 }finally{f.close();}
});

test('publication requires the exact privately bound Catalog instance',async()=>{
 const f=fixture();
 try{
  const response=await f.handler(f.incoming());
  expect(publishDatabaseResponse(f.second,response).status).toBe(403);
  expect(publishDatabaseResponse(f.catalog,response).status).toBe(200);
 }finally{f.close();}
});

test('direct PUT and reset retain one-time secrets with current authority',async()=>{
 const f=fixture();
 try{
  let response=publishDatabaseResponse(f.catalog,await f.handler(f.incoming('PUT')));
  expect(response.status).toBe(202);const first=(await response.json()).data.password;expect(first).toMatch(/^[A-Za-z0-9_-]{32}$/);
  f.worker.query("UPDATE database_access SET state='on' WHERE runtime=?").run(f.runtime);
  response=publishDatabaseResponse(f.catalog,await f.handler(f.incoming('POST',true)));
  expect(response.status).toBe(202);const reset=(await response.json()).data;
  expect(reset.password).not.toBe(first);expect(reset.url).toContain(reset.password);
  const later=publishDatabaseResponse(f.catalog,await f.handler(f.incoming()));
  expect(await later.text()).not.toContain(reset.password);
 }finally{f.close();}
});

test('caller supplied authority and readiness flags cannot enable mutation',async()=>{
 const f=fixture();
 try{
  const response=await f.handler(f.incoming('PUT',false,{enabled:true,authorized:true,ready:true,epoch:0}));
  expect(response.status).toBe(400);expect(existsSync(join(f.directory,`${f.runtime}.json`))).toBe(false);
  expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('off');
 }finally{f.close();}
});
