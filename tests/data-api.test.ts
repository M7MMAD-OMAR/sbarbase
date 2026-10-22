import {test,expect,beforeAll,afterAll} from 'bun:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {application} from '../src/control/application';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {AccessTokenStore} from '../src/control/access-tokens';
import {refOf} from '../src/control/supabase-api';
import {validateDataApi,mergeDataApi,DataApiError} from '../src/control/data-api';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import type {SqlRunner} from '../src/control/agent-sql';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';

// Data API settings: the console route, the Supabase-compatible /v1/projects/{ref}/postgrest and
// the rules they share with lab/data_api.py (docs/engineering/DATA-API-SCHEMAS.md).

const viewerSession='55555555-5555-4555-8555-555555555555';
const viewerToken=()=>managementToken('viewer','aal2',viewerSession,Math.floor(Date.now()/1000));

// The console writes private files (database passwords, function code) under the working
// directory, so the whole file runs in a temporary one.
const original=process.cwd();let work='';
beforeAll(()=>{work=mkdtempSync(join(tmpdir(),'sbarbase-data-api-'));process.chdir(work);});
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
 return {catalog,keys,tokens,org,other,created,runtime,ref:refOf(created.environment),handler,manage,api,mint,sqlCalls,setDatabase,viewerHuman,
  setLoopback:(value:string)=>{loopback=value;},close(){catalog.close();keys.close();tokens.close();}};
}


/** What lab/data_api.py records when it applied the saved settings. */
const applied=(s:ReturnType<typeof setup>)=>(s.catalog as any).db.query(`UPDATE data_api_settings SET state='applied',applied_schemas=schemas,
 applied_extra_search_path=extra_search_path,applied_max_rows=max_rows,served_schemas=schemas,served_extra_search_path=extra_search_path,served_max_rows=max_rows`).run();
const row=(s:ReturnType<typeof setup>)=>(s.catalog as any).db.query('SELECT schemas,extra_search_path,max_rows,state FROM data_api_settings WHERE runtime=?').get(s.runtime);

test('the rules: order kept, platform schemas and odd names refused, extensions only on the search path',()=>{
 expect(validateDataApi({schemas:['i2s','public']})).toEqual({schemas:['i2s','public'],extraSearchPath:['public'],maxRows:null});
 expect(validateDataApi({schemas:['public'],extraSearchPath:['public','extensions'],maxRows:1000}).extraSearchPath).toEqual(['public','extensions']);
 for(const name of ['auth','storage','vault','cron','net','pg_catalog','information_schema','sbarbase_guard','supabase_functions','extensions','graphql'])
  expect(()=>validateDataApi({schemas:['public',name]})).toThrow(/belongs to the platform/);
 for(const name of ['App','my-schema','1a','a,b',"x'y",'','a'.repeat(64)])expect(()=>validateDataApi({schemas:[name]})).toThrow(DataApiError);
 expect(()=>validateDataApi({schemas:['public','public']})).toThrow(/twice/);
 expect(validateDataApi({schemas:[]}).schemas).toEqual(['sbarbase_empty']);
 expect(()=>validateDataApi({schemas:['public','sbarbase_empty']})).toThrow(DataApiError);
 expect(()=>validateDataApi({schemas:Array.from({length:17},(_,i)=>'s'+i)})).toThrow(DataApiError);
 for(const rows of [0,-1,1_000_001,1.5,'10',true])expect(()=>validateDataApi({schemas:['public'],maxRows:rows})).toThrow(DataApiError);
 expect(()=>validateDataApi({schemas:['public'],extraSearchPath:['auth']})).toThrow(DataApiError);
 expect(()=>validateDataApi({schemas:['public','graphql_public']})).toThrow(/GraphQL is not available/);
});

test('a partial change keeps the fields it does not name, as Supabase PATCH does',()=>{
 const current={schemas:['public','i2s'],extraSearchPath:['public','extensions'],maxRows:1000};
 const keys={schemas:'db_schema',extraSearchPath:'db_extra_search_path',maxRows:'max_rows'};
 expect(mergeDataApi(current,{max_rows:null},keys,true)).toEqual({...current,maxRows:null});
 expect(mergeDataApi(current,{db_schema:' public , app '},keys,true).schemas).toEqual(['public','app']);
 expect(()=>mergeDataApi(current,{db_schema:'public,auth'},keys,true)).toThrow(DataApiError);
});

test('the console reads the defaults, saves a pending change and refuses a second one while it waits',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  const first=await (await s.manage('GET',path)).json();
  expect(first.data).toMatchObject({schemas:['public'],extraSearchPath:['public'],maxRows:null,state:'default'});
  const saved=await s.manage('PUT',path,{schemas:['public','i2s'],extraSearchPath:['public','extensions'],maxRows:null});
  expect(saved.status).toBe(202);
  expect((await saved.json()).data).toMatchObject({schemas:['public','i2s'],state:'pending'});
  expect(row(s)).toEqual({schemas:'public,i2s',extra_search_path:'public,extensions',max_rows:null,state:'pending'});
  expect((await s.manage('PUT',path,{schemas:['public']})).status).toBe(409);
  expect(JSON.stringify(s.catalog.auditEvents('owner',s.org))).toContain('data_api.saved');
  // A viewer reads it and cannot change it.
  expect((await s.manage('GET',path,undefined,s.viewerHuman)).status).toBe(200);
  (s.catalog as any).db.query("UPDATE data_api_settings SET state='applied'").run();
  expect((await s.manage('PUT',path,{schemas:["public"]},s.viewerHuman)).status).toBe(403);
 } finally {s.close();}
});

test('the console refuses bad input with the reason, and nothing is saved',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  for(const body of [{schemas:['public','auth']},{schemas:'public'},{schemas:['public'],jwt_secret:'x'},{schemas:['public'],maxRows:0}]) {
   const response=await s.manage('PUT',path,body);
   expect(response.status).toBe(400);
  }
  const refused=await (await s.manage('PUT',path,{schemas:['public','vault']})).json();
  expect(refused.message).toContain('vault');
  expect(row(s)).toBeNull();
 } finally {s.close();}
});

test('GET and PATCH /v1/projects/{ref}/postgrest speak Supabase\'s shape and never answer the signing secret',async()=>{
 const s=setup();
 try {
  const token=await s.mint();
  const read=await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json();
  expect(read).toEqual({db_schema:'public',db_extra_search_path:'public',max_rows:null,db_pool:3,db_anon_role:'anon',role_claim_key:'.role'});
  const patched=await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{db_schema:'public,i2s',db_extra_search_path:'public, extensions',max_rows:1000});
  expect(patched.status).toBe(200);
  const answer=await patched.text();
  expect(JSON.parse(answer)).toMatchObject({db_schema:'public,i2s',db_extra_search_path:'public,extensions',max_rows:1000});
  expect(answer).not.toContain('jwt');
  expect(row(s)).toMatchObject({schemas:'public,i2s',state:'pending'});
  // Until the supervisor applies it, GET answers what PostgREST serves now.
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json()).toMatchObject({db_schema:'public',max_rows:null});
  applied(s);
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json()).toMatchObject({db_schema:'public,i2s',max_rows:1000});
  expect((await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{db_schema:'public,storage'})).status).toBe(400);
  expect((await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{db_pool:20})).status).toBe(400);
  // Only the row limit changes; the schemas stay.
  expect(await (await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{max_rows:null,db_pool:3})).json()).toMatchObject({db_schema:'public,i2s',max_rows:null});
  const readOnly=await s.mint({read_only:true});
  expect((await s.api(readOnly,'GET',`/v1/projects/${s.ref}/postgrest`)).status).toBe(200);
  expect((await s.api(readOnly,'PATCH',`/v1/projects/${s.ref}/postgrest`,{max_rows:5})).status).toBe(403);
 } finally {s.close();}
});

test('a refused change is shown beside what is still served, and PATCH starts from what is served',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  await s.manage('PUT',path,{schemas:['public','app']});applied(s);
  await s.manage('PUT',path,{schemas:['public','typo']});
  (s.catalog as any).db.query("UPDATE data_api_settings SET state='failed',failure='The schema typo does not exist'").run();
  const view=(await (await s.manage('GET',path)).json()).data;
  expect(view).toMatchObject({schemas:['public','typo'],state:'failed',served:{schemas:['public','app']}});
  const token=await s.mint();
  expect((await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json()).db_schema).toBe('public,app');
  const patched=await (await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{max_rows:50})).json();
  expect(patched).toMatchObject({db_schema:'public,app',max_rows:50});
 } finally {s.close();}
});

test('the gateway passes Accept-Profile and Content-Profile to PostgREST unchanged',async()=>{
 const E='e_'+'a'.repeat(24);
 const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',keys:[],anonymousToken:'anon-token',enabled:true};
 const calls:Headers[]=[];
 const gateway=createGateway(new Map([[E,route]]),(async(_url:URL,init:RequestInit)=>{calls.push(new Headers(init.headers));return Response.json([]);}) as unknown as typeof fetch,
  (runtime,key)=>key==='sb_publishable_ok');
 expect((await gateway(new Request(`http://local/${E}/rest/v1/items`,{headers:{apikey:'sb_publishable_ok','accept-profile':'i2s'}}))).status).toBe(200);
 expect((await gateway(new Request(`http://local/${E}/rest/v1/items`,{method:'POST',headers:{apikey:'sb_publishable_ok','content-profile':'i2s',
  'content-type':'application/json'},body:'{}'}))).status).toBe(200);
 expect(calls[0]!.get('accept-profile')).toBe('i2s');
 expect(calls[1]!.get('content-profile')).toBe('i2s');
});

test('with every exposed schema missing, the platform\'s empty schema is never shown or merged as a choice',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  await s.manage('PUT',path,{schemas:['api']});applied(s);
  // What a start records when `api` is gone (lab/data_api.py effective).
  (s.catalog as any).db.query("UPDATE data_api_settings SET served_schemas='sbarbase_empty',served_extra_search_path='sbarbase_empty',state='failed',failure='Not served because missing from the database: api'").run();
  const token=await s.mint();
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json()).toMatchObject({db_schema:'',db_extra_search_path:''});
  const patched=await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{max_rows:100});
  expect(patched.status).toBe(200);
  expect(row(s)).toMatchObject({schemas:'api',max_rows:100,state:'pending'});
 } finally {s.close();}
});

test('a start\'s own report reaches the console beside the saved state',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  await s.manage('PUT',path,{schemas:['public','app']});applied(s);
  (s.catalog as any).db.query("UPDATE data_api_settings SET served_schemas='public',start_report='Not served because missing from the database: app.'").run();
  const view=(await (await s.manage('GET',path)).json()).data;
  expect(view).toMatchObject({state:'applied',report:'Not served because missing from the database: app.',served:{schemas:['public']}});
 } finally {s.close();}
});

test('an empty list turns the Data API off, through the console and through PATCH',async()=>{
 const s=setup();
 try {
  const path=`/environments/${s.created.environment}/data-api`;
  expect((await s.manage('PUT',path,{schemas:[]})).status).toBe(202);
  expect(row(s)).toMatchObject({schemas:'sbarbase_empty',state:'pending'});
  applied(s);
  expect((await (await s.manage('GET',path)).json()).data).toMatchObject({schemas:[],applied:{schemas:[]}});
  const token=await s.mint();
  expect(await (await s.api(token,'GET',`/v1/projects/${s.ref}/postgrest`)).json()).toMatchObject({db_schema:'',db_extra_search_path:'public'});
  expect((await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{max_rows:5})).status).toBe(200);
  expect(row(s)).toMatchObject({schemas:'sbarbase_empty',max_rows:5});
  applied(s);
  expect((await s.api(token,'PATCH',`/v1/projects/${s.ref}/postgrest`,{db_schema:'public,app'})).status).toBe(200);
  expect(row(s)).toMatchObject({schemas:'public,app'});
  // The platform schema with others is never a choice.
  applied(s);
  expect((await s.manage('PUT',path,{schemas:['public','sbarbase_empty']})).status).toBe(400);
 } finally {s.close();}
});
