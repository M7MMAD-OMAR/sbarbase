import {test,expect,beforeAll,afterAll} from 'bun:test';
import {mkdtempSync,rmSync,existsSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {application} from '../src/control/application';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {AccessTokenStore,ACCESS_TOKEN} from '../src/control/access-tokens';
import {refOf,splitDeployFiles} from '../src/control/supabase-api';
import type {SqlRunner} from '../src/control/agent-sql';
import {serveLocal} from '../src/http/local-server';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';
const viewerSession='55555555-5555-4555-8555-555555555555';
const viewerToken=()=>managementToken('viewer','aal2',viewerSession,Math.floor(Date.now()/1000));

// The console writes private files (database passwords, function code) under the working
// directory, so the whole file runs in a temporary one.
const original=process.cwd();let work='';
beforeAll(()=>{work=mkdtempSync(join(tmpdir(),'sbarbase-agents-'));process.chdir(work);});
afterAll(()=>{process.chdir(original);rmSync(work,{recursive:true,force:true});});

type Call={kind:string;runtime:string;input?:any};
function setup() {
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:'),tokens=new AccessTokenStore(':memory:');
 const org=catalog.createOrganization('owner','Acme'),other=catalog.createOrganization('stranger','Other');
 catalog.setMember('owner',org,'viewer','viewer');
 const created=catalog.createProjectWithProduction('owner',org,'Shop');
 let job;while((job=catalog.claimProvision()))catalog.finishProvision(job.environment,job.claim!,true);
 const runtime=catalog.getProvision('owner',created.environment).runtime!;
 const sqlCalls:Call[]=[];
 const sql:SqlRunner={
  async query(target,input){sqlCalls.push({kind:'query',runtime:target.runtime,input});return [{ok:1}];},
  async migrations(target){sqlCalls.push({kind:'migrations',runtime:target.runtime});return [{version:'20261017000000',name:'init'}];},
  async migrate(target,input){sqlCalls.push({kind:'migrate',runtime:target.runtime,input});return {version:'20261017000001'};},
 };
 const now=Math.floor(Date.now()/1000);
 const human=managementToken('owner','aal2',sessionId,now),viewerHuman=managementToken('viewer','aal2',viewerSession,now);
 catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
 catalog.managementSecurity.grant('viewer',viewerSession,factorId,now,now+3600,0);
 let loopback:string|undefined;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},
  ()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',keys:[],anonymousToken:'anon',enabled:true}),
  (async(input:RequestInfo|URL,init?:RequestInit)=>{
   const bearer=new Headers(init?.headers).get('authorization')??'';
   if(String(input)==='http://realm.invalid/user'){
    if(bearer==='Bearer '+human)return Response.json({id:'owner',factors:[verifiedFactor]});
    if(bearer==='Bearer '+viewerHuman)return Response.json({id:'viewer',factors:[verifiedFactor]});
    return Response.json({message:'invalid JWT'},{status:401});
   }
   return Response.json({message:'unexpected'},{status:500});
  }) as typeof fetch,undefined,undefined,undefined,undefined,undefined,
  {tokens,sql,publicUrl:()=>'https://sb.example',loopback:()=>loopback});
 const manage=(method:string,path:string,body?:unknown,who=human)=>handler(new Request('http://local/management/v1'+path,{method,
  headers:{authorization:'Bearer '+who,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
 const api=(token:string,method:string,path:string,body?:unknown,headers:Record<string,string>={})=>handler(new Request('http://local'+path,{method,
  headers:{authorization:'Bearer '+token,...(body===undefined||body instanceof FormData?{}:{'content-type':'application/json'}),...headers},
  ...(body===undefined?{}:{body:body instanceof FormData?body:JSON.stringify(body)})}));
 const mint=async(input:Record<string,unknown>={},who=human)=>{
  const response=await manage('POST','/access-tokens',{name:'agent',...input},who);
  expect(response.status).toBe(201);return (await response.json()).token as string;
 };
 const setDatabase=(state:'on'|'off')=>(catalog as any).db.query(`INSERT INTO database_access(runtime,desired,state,failure,actor,updated_at) VALUES (?,?,?,NULL,'owner',0)
  ON CONFLICT(runtime) DO UPDATE SET desired=excluded.desired,state=excluded.state`).run(runtime,state,state);
 return {catalog,keys,tokens,org,other,created,runtime,ref:refOf(created.environment),handler,manage,api,mint,sqlCalls,setDatabase,
  setLoopback:(value:string)=>{loopback=value;},close(){catalog.close();keys.close();tokens.close();}};
}

test('tokens look like Supabase personal access tokens, are shown once, and only a signed-in person manages them',async()=>{
 const s=setup();
 try {
  const token=await s.mint({days:30});
  expect(token).toMatch(ACCESS_TOKEN);expect(token).toMatch(/^sbp_[a-f0-9]{40}$/);
  const listed=await (await s.manage('GET','/access-tokens')).text();
  expect(listed).not.toContain(token);expect(JSON.parse(listed).data[0]).toMatchObject({name:'agent',read_only:false,scope:{kind:'all'}});
  // A token is not a console session: it cannot make, list or revoke tokens.
  expect((await s.api(token,'GET','/management/v1/access-tokens')).status).toBe(401);
  expect((await s.api(token,'POST','/management/v1/access-tokens',{name:'more'})).status).toBe(401);
  for(const bad of [{name:''},{name:'x',days:0},{name:'x',days:366},{name:'x',scope:{kind:'organization',id:s.other}},{name:'x',scope:{kind:'all',id:s.org}},{name:'x',read_only:'yes'}])
   expect([400,404]).toContain((await s.manage('POST','/access-tokens',bad)).status);
  expect(JSON.stringify(s.catalog.auditEvents('owner',s.org))).not.toContain(token);
 } finally {s.close();}
});

test('the Supabase-compatible API lists what the token reaches, with refs the Supabase CLI accepts',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const orgs=await (await s.api(token,'GET','/v1/organizations')).json();
  expect(orgs).toEqual([{id:s.org,slug:s.org,name:'Acme'}]);
  const projects=await (await s.api(token,'GET','/v1/projects')).json();
  expect(projects).toHaveLength(1);
  expect(projects[0]).toMatchObject({id:s.ref,ref:s.ref,organization_id:s.org,name:'Shop',status:'ACTIVE_HEALTHY',region:'self-hosted',api_url:`https://sb.example/${s.runtime}`});
  expect(s.ref).toMatch(/^[a-z]{20}$/);
  expect((await s.api(token,'GET',`/v1/projects/${s.ref}`)).status).toBe(200);
  expect((await s.api(token,'GET','/v1/projects/abcdefghijklmnopqrst')).status).toBe(404);
  expect((await s.api('sbp_'+'0'.repeat(40),'GET','/v1/projects')).status).toBe(401);
  expect((await s.api('not-a-token','GET','/v1/projects')).status).toBe(401);
 } finally {s.close();}
});

test('scope narrows what a token sees; another organization stays invisible',async()=>{
 const s=setup();
 try {
  const elsewhere=s.catalog.createProject('owner',s.org,'Blog');
  const project=await s.mint({scope:{kind:'project',id:s.created.id}});
  expect((await (await s.api(project,'GET','/v1/projects')).json()).map((item:any)=>item.ref)).toEqual([s.ref]);
  expect((await s.api(project,'POST','/v1/projects',{name:'New',organization_slug:s.org})).status).toBe(403);
  const organization=await s.mint({scope:{kind:'organization',id:s.org}});
  expect((await (await s.api(organization,'GET','/v1/organizations')).json()).map((item:any)=>item.id)).toEqual([s.org]);
  expect((await s.api(organization,'GET',`/v1/organizations/${s.other}`)).status).toBe(404);
  void elsewhere;
 } finally {s.close();}
});

test('a read-only token reads, and its SQL runs read only whatever it asks',async()=>{
 const s=setup();
 try {
  s.setDatabase('on');
  const token=await s.mint({read_only:true});
  expect((await s.api(token,'GET','/v1/projects')).status).toBe(200);
  expect((await s.api(token,'POST','/v1/projects',{name:'New',organization_slug:s.org})).status).toBe(403);
  expect((await s.api(token,'POST',`/v1/projects/${s.ref}/database/migrations`,{name:'x',query:'select 1'})).status).toBe(403);
  const response=await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:'delete from orders',read_only:false});
  expect(response.status).toBe(201);
  expect(s.sqlCalls.at(-1)).toMatchObject({kind:'query',runtime:s.runtime,input:{readOnly:true,query:'delete from orders'}});
  const writer=await s.mint();
  await s.api(writer,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1'});
  expect(s.sqlCalls.at(-1)!.input.readOnly).toBe(false);
 } finally {s.close();}
});

test('SQL needs an owner or admin; a viewer token cannot reach the database',async()=>{
 const s=setup();
 try {
  s.setDatabase('on');
  const token=await s.mint({read_only:true},viewerToken());
  expect((await s.api(token,'GET','/v1/projects')).status).toBe(200);
  expect((await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1'})).status).toBe(403);
  expect(s.sqlCalls).toHaveLength(0);
 } finally {s.close();}
});

test('database access that is off is turned on for a writable token, and the call says to retry',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const response=await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1'});
  expect(response.status).toBe(409);expect((await response.json()).message).toContain('being turned on');
  expect(s.catalog.databaseAccess('owner',s.created.environment)).toMatchObject({desired:'on',state:'pending'});
  // The password went to the private file the supervisor reads, not to the agent.
  expect(existsSync(join(work,'.secrets/upstream/database',`${s.runtime}.json`))).toBe(true);
  expect(await response.text().catch(()=>'')).not.toContain('password');
  const readOnly=await s.mint({read_only:true});
  s.setDatabase('off');
  expect((await (await s.api(readOnly,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1'})).json()).message).toContain('off');
 } finally {s.close();}
});

test('migrations are listed and applied through the runner',async()=>{
 const s=setup();
 try {
  s.setDatabase('on');
  const token=await s.mint();
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/database/migrations`)).json()).toEqual([{version:'20261017000000',name:'init'}]);
  expect((await s.api(token,'POST',`/v1/projects/${s.ref}/database/migrations`,{name:'create_orders',query:'create table orders(id int)'})).status).toBe(200);
  expect(s.sqlCalls.at(-1)).toMatchObject({kind:'migrate',input:{name:'create_orders'}});
 } finally {s.close();}
});

test('a project created through the API is a new project with its production environment',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const response=await s.api(token,'POST','/v1/projects',{name:'Agent made',organization_slug:s.org,region:'us-east-1',db_pass:'ignored'});
  expect(response.status).toBe(201);
  const value=await response.json();
  expect(value.ref).toMatch(/^[a-p]{20}$/);expect(value.status).toBe('COMING_UP');expect(value.name).toBe('Agent made');
  expect(s.catalog.listProjects('owner',s.org).map(project=>project.name)).toContain('Agent made');
  expect((await s.api(token,'POST','/v1/projects',{name:'X',organization_slug:s.other})).status).toBe(404);
  expect((await s.api(token,'DELETE',`/v1/projects/${s.ref}`)).status).toBe(501);
 } finally {s.close();}
});

test('API keys: a publishable key can be read back, a secret key only once',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const publishable=await (await s.api(token,'POST',`/v1/projects/${s.ref}/api-keys`,{type:'publishable',name:'web_app'})).json();
  expect(publishable.api_key).toMatch(/^sb_publishable_/);
  const secret=await (await s.api(token,'POST',`/v1/projects/${s.ref}/api-keys`,{type:'secret',name:'backend'})).json();
  expect(secret.api_key).toMatch(/^sb_secret_/);
  const listed=await (await s.api(token,'GET',`/v1/projects/${s.ref}/api-keys?reveal=true`)).json();
  expect(listed.find((key:any)=>key.name==='web_app').api_key).toBe(publishable.api_key);
  expect(listed.find((key:any)=>key.name==='backend').api_key).toBeNull();
  expect(JSON.stringify(listed)).not.toContain(secret.api_key);
  expect((await s.api(token,'GET',`/v1/projects/${s.ref}/api-keys/legacy`)).status).toBe(200);
  expect((await s.api(token,'DELETE',`/v1/projects/${s.ref}/api-keys/${secret.id}`)).status).toBe(200);
  expect(s.keys.resolve(s.runtime,secret.api_key)).toBeNull();
 } finally {s.close();}
});

test('Edge Functions deploy from the multipart form the Supabase MCP sends, and read back',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const form=new FormData();
  form.append('metadata',new Blob([JSON.stringify({name:'hello',entrypoint_path:'index.ts',verify_jwt:false})],{type:'application/json'}));
  form.append('file',new Blob(["import {cors} from '../_shared/cors.ts';\nDeno.serve(()=>new Response('hi',{headers:cors}));"],{type:'application/typescript'}),'index.ts');
  form.append('file',new Blob(['export const cors={};'],{type:'application/typescript'}),'../_shared/cors.ts');
  const deployed=await s.api(token,'POST',`/v1/projects/${s.ref}/functions/deploy?slug=hello`,form);
  expect(deployed.status).toBe(201);
  const value=await deployed.json();
  expect(value).toMatchObject({slug:'hello',name:'hello',status:'ACTIVE',verify_jwt:false});
  expect(value.entrypoint_path).toMatch(/^file:\/\/\/tmp\/user_fn_.+\/source\/index\.ts$/);
  const list=await (await s.api(token,'GET',`/v1/projects/${s.ref}/functions`)).json();
  expect(list.map((fn:any)=>fn.slug)).toEqual(['hello']);
  const body=await s.api(token,'GET',`/v1/projects/${s.ref}/functions/hello/body`);
  expect(body.headers.get('content-type')).toStartWith('multipart/form-data');
  const files=[...(await body.formData()).getAll('file')] as File[];
  expect(files.map(file=>file.name).sort()).toEqual(['_shared/cors.ts','index.ts']);
  expect((await s.api(token,'DELETE',`/v1/projects/${s.ref}/functions/hello`)).status).toBe(200);
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/functions`)).json()).toEqual([]);
 } finally {s.close();}
});

test('deploy file names are confined to the function and its shared folder',()=>{
 expect(splitDeployFiles([{name:'./index.ts',content:'a'},{name:'_shared/x.ts',content:'b'},{name:'lib/y.ts',content:'c'}],'index.ts'))
  .toEqual({files:{'index.ts':'a','lib/y.ts':'c'},shared:{'x.ts':'b'}});
 expect(splitDeployFiles([{name:'main.ts',content:'a'}],'main.ts').files['index.ts']).toBe("import './main.ts';\n");
 for(const name of ['../../etc/passwd','/abs.ts','a/../b.ts','../other/x.ts'])
  expect(()=>splitDeployFiles([{name:'index.ts',content:''},{name,content:''}],'index.ts')).toThrow();
});

test('function secrets are set and listed by name, never by value',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  expect((await s.api(token,'POST',`/v1/projects/${s.ref}/secrets`,[{name:'STRIPE_KEY',value:'sk_test_value'}])).status).toBe(201);
  const listed=await (await s.api(token,'GET',`/v1/projects/${s.ref}/secrets`)).text();
  expect(listed).toContain('STRIPE_KEY');expect(listed).not.toContain('sk_test_value');
  expect((await s.api(token,'DELETE',`/v1/projects/${s.ref}/secrets`,['STRIPE_KEY'])).status).toBe(200);
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/secrets`)).json()).toEqual([]);
 } finally {s.close();}
});

test('a token stops at revocation, at its expiry and when its owner\'s password is reset',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const id=(await (await s.manage('GET','/access-tokens')).json()).data[0].id;
  expect((await s.api(token,'GET','/v1/projects')).status).toBe(200);
  expect((await s.manage('DELETE','/access-tokens/'+id)).status).toBe(200);
  expect((await s.api(token,'GET','/v1/projects')).status).toBe(401);
  const second=await s.mint();
  s.catalog.managementSecurity.revoke('owner');
  expect((await s.api(second,'GET','/v1/projects')).status).toBe(401);
  const clock={now:Date.now()};
  const store=new AccessTokenStore(':memory:',()=>clock.now);
  const issued=store.issue('owner','0.0',{name:'short',scope:{kind:'all'},read_only:false,days:1});
  expect(store.resolve(issued.token,()=>'0.0')).not.toBeNull();
  clock.now+=86_400_001;
  expect(store.resolve(issued.token,()=>'0.0')).toBeNull();
  store.close();
 } finally {s.close();}
});

test('a membership removed after the token was made applies at the next call',async()=>{
 const s=setup();
 try {
  const token=await s.mint({},viewerToken());
  expect((await (await s.api(token,'GET','/v1/projects')).json())).toHaveLength(1);
  s.catalog.setMember('owner',s.org,'viewer',null);
  expect((await (await s.api(token,'GET','/v1/projects')).json())).toEqual([]);
 } finally {s.close();}
});

test('the MCP endpoint runs the official Supabase MCP server against this API, with this server\'s project URL',async()=>{
 const s=setup();
 const server=await serveLocal(s.handler);
 try {
  s.setLoopback(`http://127.0.0.1:${server.port}`);
  const token=await s.mint();
  expect((await fetch(`http://127.0.0.1:${server.port}/mcp`,{method:'POST',body:'{}'})).status).toBe(401);
  const rpc=async(method:string,params:unknown,id:number|null=1)=>{
   const response=await fetch(`http://127.0.0.1:${server.port}/mcp`,{method:'POST',headers:{authorization:'Bearer '+token,
    'content-type':'application/json',accept:'application/json, text/event-stream','mcp-protocol-version':'2025-06-18'},
    body:JSON.stringify(id===null?{jsonrpc:'2.0',method,params}:{jsonrpc:'2.0',id,method,params})});
   const text=await response.text();
   const line=text.split('\n').find(row=>row.startsWith('data: '));
   return {status:response.status,body:text?JSON.parse(line?line.slice(6):text):null};
  };
  const init=await rpc('initialize',{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'test',version:'1'}});
  expect(init.status).toBe(200);expect(init.body.result.serverInfo).toBeDefined();
  const tools=await rpc('tools/list',{},2);
  const names=tools.body.result.tools.map((tool:any)=>tool.name);
  for(const name of ['list_projects','execute_sql','apply_migration','deploy_edge_function','get_project_url','get_publishable_keys'])expect(names).toContain(name);
  const url=await rpc('tools/call',{name:'get_project_url',arguments:{project_id:s.ref}},3);
  expect(JSON.stringify(url.body)).toContain(`https://sb.example/${s.runtime}`);
  const listed=await rpc('tools/call',{name:'list_projects',arguments:{}},4);
  expect(JSON.stringify(listed.body)).toContain(s.ref);
  const readOnly=await s.mint({read_only:true});
  const readOnlyTools=await (await fetch(`http://127.0.0.1:${server.port}/mcp`,{method:'POST',headers:{authorization:'Bearer '+readOnly,'content-type':'application/json',
   accept:'application/json, text/event-stream','mcp-protocol-version':'2025-06-18'},body:JSON.stringify({jsonrpc:'2.0',id:5,method:'tools/list',params:{}})})).text();
  expect(readOnlyTools).not.toContain('"apply_migration"');expect(readOnlyTools).not.toContain('"deploy_edge_function"');
 } finally {server.stop(true);s.close();}
});

test('a call that only reads never turns database access on',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  expect((await s.api(token,'GET',`/v1/projects/${s.ref}/database/migrations`)).status).toBe(409);
  const advisors=await (await s.api(token,'GET',`/v1/projects/${s.ref}/advisors/security`)).json();
  expect(advisors.lints).toEqual([]);
  expect(s.catalog.databaseAccess('owner',s.created.environment).desired).toBe('off');
  expect((await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1',read_only:true})).status).toBe(409);
  expect(s.catalog.databaseAccess('owner',s.created.environment).desired).toBe('off');
 } finally {s.close();}
});

test('a writing query and a migration are audited with the token, never with the query',async()=>{
 const s=setup();
 try {
  s.setDatabase('on');
  const token=await s.mint();
  const id=(await (await s.manage('GET','/access-tokens')).json()).data[0].id;
  await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:"update orders set note='secret-value'"});
  await s.api(token,'POST',`/v1/projects/${s.ref}/database/migrations`,{name:'create_orders',query:'create table orders(id int)'});
  await s.api(token,'POST',`/v1/projects/${s.ref}/database/query`,{query:'select 1',read_only:true});
  const audit=s.catalog.auditEvents('owner',s.org).filter(event=>event.action.startsWith('database.agent_'));
  expect(audit.map(event=>event.action).sort()).toEqual(['database.agent_migration','database.agent_query']);
  expect(JSON.stringify(audit)).toContain(id);expect(JSON.stringify(audit)).not.toContain('secret-value');
 } finally {s.close();}
});

test('a project without a readable publishable key gets one; a key that cannot be shown is left out',async()=>{
 const s=setup();
 try {
  // A key from before 0.5.0: only its digest is kept.
  const old=s.keys.issue(s.runtime,'publishable','legacy_key');
  (s.keys as any).db.query('UPDATE api_keys SET token=NULL WHERE id=?').run(old.id);
  const token=await s.mint();
  const listed=await (await s.api(token,'GET',`/v1/projects/${s.ref}/api-keys`)).json();
  expect(listed.map((key:any)=>key.name)).toEqual(['default']);
  expect(listed[0].api_key).toMatch(/^sb_publishable_/);
  const again=await (await s.api(token,'GET',`/v1/projects/${s.ref}/api-keys`)).json();
  expect(again).toHaveLength(1);
  const readOnly=await s.mint({read_only:true});
  expect((await (await s.api(readOnly,'GET',`/v1/projects/${s.ref}/api-keys`)).json())).toHaveLength(1);
 } finally {s.close();}
});

test('through MCP: publishable keys, the logs tool this server answers, and no URL outside the token scope',async()=>{
 const s=setup();
 const server=await serveLocal(s.handler);
 try {
  s.setLoopback(`http://127.0.0.1:${server.port}`);
  const other=s.catalog.createProjectWithProduction('owner',s.org,'Other');
  let job;while((job=s.catalog.claimProvision()))s.catalog.finishProvision(job.environment,job.claim!,true);
  const call=async(token:string,method:string,params:unknown)=>{
   const response=await fetch(`http://127.0.0.1:${server.port}/mcp`,{method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json',
    accept:'application/json, text/event-stream','mcp-protocol-version':'2025-06-18'},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
   const text=await response.text();const line=text.split('\n').find(row=>row.startsWith('data: '));
   return JSON.parse(line?line.slice(6):text);
  };
  const token=await s.mint();
  const names=(await call(token,'tools/list',{})).result.tools.map((tool:any)=>tool.name);
  expect(names).toContain('get_logs');expect(names).not.toContain('query_logs');
  const keys=await call(token,'tools/call',{name:'get_publishable_keys',arguments:{project_id:s.ref}});
  expect(JSON.stringify(keys)).toContain('sb_publishable_');expect(keys.result.isError).not.toBe(true);
  const scoped=await s.mint({scope:{kind:'project',id:s.created.id}});
  const outside=await call(scoped,'tools/call',{name:'get_project_url',arguments:{project_id:refOf(other.environment)}});
  const otherRuntime=s.catalog.getProvision('owner',other.environment).runtime!;
  expect(JSON.stringify(outside)).not.toContain(otherRuntime);
 } finally {server.stop(true);s.close();}
});
