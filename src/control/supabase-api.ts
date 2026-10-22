import {createHash} from 'node:crypto';
import {existsSync,readdirSync,readFileSync,statSync} from 'node:fs';
import {join,relative} from 'node:path';
import {DATA_API_EMPTY,type AgentDirectoryEntry,type Catalog} from './catalog';
import {reply} from './auth';
import {tokenEpoch,type AccessToken,type AccessTokenStore} from './access-tokens';
import {SqlRefusal,type SqlRunner,type SqlTarget} from './agent-sql';
import {ENTRYPOINTS,FUNCTION_NAME,readManifest} from './functions';
import {uploadLimit} from '../gateway/handler';
import {mergeDataApi} from './data-api';

/** The part of Supabase's Management API (`https://api.supabase.com/v1/...`) that agents and tools
 * use, answered for this installation (docs/guides/agents.md). The official Supabase MCP server,
 * given this server's address, works against it, and so does this server's own hosted MCP
 * (src/control/mcp.ts).
 *
 * A Supabase "project" is a Sbarbase environment: it has its own database, keys and API address.
 * Its ref is 20 lowercase letters made from the environment id, the form the Supabase CLI accepts.
 *
 * Every call carries a personal access token (src/control/access-tokens.ts) and acts as the
 * person who made it, narrowed by the token's scope and read-only flag. Changes go through the
 * console's own management routes, with their checks, audit and publication rules; only SQL and
 * reading a function's files are done here, after the same role checks. */

export type AgentDispatch=(token:AccessToken,method:string,path:string,body?:unknown)=>Promise<Response>;
export type SupabaseApiOptions={
 catalog:Catalog;
 tokens:AccessTokenStore;
 dispatch:AgentDispatch;
 sql:SqlRunner;
 publicUrl:()=>string;
 functionsRoot?:string;
};

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const REF=/^[a-p]{20}$/;
const MAX_DEPLOY=10*1024*1024;
/** Each environment's PostgREST pool, PGRST_DB_POOL in lab/run.py rest_configuration. */
const REST_POOL=3;

const MAX_JSON=2*1024*1024,BODY_SECONDS=30;

/** A request body up to `limit` bytes, read within BODY_SECONDS; a larger or stalled one is refused
 * before it is all in memory. */
export async function bounded(request:Request,limit:number,message:string):Promise<Buffer> {
 if(Number(request.headers.get('content-length'))>limit)refuse(413,message);
 const reader=request.body?.getReader();
 if(!reader)return Buffer.alloc(0);
 const chunks:Uint8Array[]=[];let size=0,timer:ReturnType<typeof setTimeout>|undefined;
 const expired=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Refusal(408,'The request body was too slow')),BODY_SECONDS*1000);});
 try {
  while(true){
   const item=await Promise.race([reader.read(),expired]);
   if(item.done)break;
   size+=item.value.byteLength;
   if(size>limit)refuse(413,message);
   chunks.push(item.value);
  }
 } catch(error) {void reader.cancel().catch(()=>{});throw error;}
 finally {clearTimeout(timer);}
 return Buffer.concat(chunks);
}

/** Whether an environment is inside a token's scope. */
export function inScope(token:AccessToken,entry:AgentDirectoryEntry):boolean {
 const scope=token.scope;
 return scope.kind==='all'||scope.kind==='organization'&&entry.organization===scope.id||scope.kind==='project'&&entry.project===scope.id;
}

/** 20 letters a to p, one per hex digit of the environment id. */
export function refOf(environment:string):string {
 return environment.replace(/-/g,'').slice(0,20).replace(/[0-9a-f]/g,digit=>String.fromCharCode(97+parseInt(digit,16)));
}

class Refusal extends Error {constructor(readonly status:number,message:string){super(message);}}
const refuse=(status:number,message:string):never=>{throw new Refusal(status,message);};

function status(entry:AgentDirectoryEntry):string {
 if(entry.state==='succeeded')return 'ACTIVE_HEALTHY';
 if(entry.state==='failed'||entry.state==='cancelled')return 'INIT_FAILED';
 return 'COMING_UP';
}

function functionId(runtime:string,name:string):string {
 const hex=createHash('sha256').update(runtime+'/'+name).digest('hex');
 return `${hex.slice(0,8)}-${hex.slice(8,12)}-4${hex.slice(13,16)}-a${hex.slice(17,20)}-${hex.slice(20,32)}`;
}

/** Every file under a folder, as relative paths. */
function walk(root:string):string[] {
 const found:string[]=[];
 const visit=(directory:string)=>{
  for(const entry of readdirSync(directory,{withFileTypes:true})){
   const path=join(directory,entry.name);
   if(entry.isDirectory())visit(path);else if(entry.isFile())found.push(relative(root,path).split('\\').join('/'));
  }
 };
 if(existsSync(root))visit(root);
 return found.sort();
}

/** Files of a Supabase deploy: names relative to the function folder; `../_shared/...` or
 * `_shared/...` are the shared code next to it, as in a Supabase project. */
export function splitDeployFiles(files:{name:string;content:string}[],entrypoint:string|undefined,slug?:string) {
 const own:Record<string,string>={},shared:Record<string,string>={};
 // A name read back from Supabase carries the runtime's own prefix, /tmp/user_fn_<id>/source/.
 const clean=(name:string)=>name.replace(/^file:\/\//,'').replace(/^\/tmp\/user_fn_[^/]+\/(source\/)?/,'').replace(/^(\.\/)+/,'');
 // The Supabase CLI sends paths from the project, such as supabase/functions/<slug>/index.ts and
 // supabase/functions/_shared/cors.ts; they become the function's own files and its shared code.
 const project=(name:string)=>{
  const shared=name.match(/^(?:.*\/)?functions\/_shared\/(.+)$/);
  if(shared)return '_shared/'+shared[1];
  const own=slug?name.match(new RegExp(`^(?:.*\\/)?functions\\/${slug.replace(/[-]/g,'\\-')}\\/(.+)$`)):null;
  return own?own[1]!:name;
 };
 for(const file of files){
  let name=project(clean(file.name));
  let target=own;
  if(name.startsWith('../_shared/'))name=name.slice('../'.length);
  if(name.startsWith('_shared/')){target=shared;name=name.slice('_shared/'.length);}
  if(!name||name.startsWith('/')||name.split('/').some(part=>part==='..'||part==='.'||part===''))refuse(400,`File name not allowed: ${file.name.slice(0,100)}`);
  target[name]=file.content;
 }
 const entry=entrypoint?project(clean(entrypoint.replace(/^file:\/\//,''))):undefined;
 if(!ENTRYPOINTS.some(name=>name in own)){
  if(!entry||!(entry in own))refuse(400,'Send the function\'s entry point file (index.ts)');
  // The runtime starts index.ts; an entry point named otherwise is imported from one.
  own['index.ts']=`import './${entry}';\n`;
 }
 return {files:own,shared};
}

export function supabaseApi(options:SupabaseApiOptions) {
 const {catalog,tokens,dispatch,sql}=options;
 const functionsRoot=options.functionsRoot??'.lab/upstream/functions';
 const issuing=new Map<string,Promise<void>>();

 function directory(token:AccessToken):AgentDirectoryEntry[] {
  return catalog.agentDirectory(token.actor).filter(entry=>inScope(token,entry));
 }
 function organizations(token:AccessToken) {
  const scope=token.scope;
  const all=catalog.listOrganizations(token.actor);
  if(scope.kind==='all')return all;
  if(scope.kind==='organization')return all.filter(organization=>organization.id===scope.id);
  const owner=catalog.agentDirectory(token.actor).find(entry=>entry.project===scope.id)?.organization;
  return all.filter(organization=>organization.id===owner);
 }
 function find(token:AccessToken,ref:string):AgentDirectoryEntry {
  if(!REF.test(ref))refuse(404,'Project not found');
  const matches=directory(token).filter(entry=>refOf(entry.environment)===ref);
  if(matches.length!==1)refuse(404,'Project not found');
  return matches[0]!;
 }
 const writer=(entry:AgentDirectoryEntry)=>{if(entry.role==='viewer')refuse(403,'Your role in this organization is viewer; an owner or admin can change it');};
 const ready=(entry:AgentDirectoryEntry)=>{if(entry.state!=='succeeded'||!entry.runtime)refuse(409,'The project is still being created; try again in a minute');return entry.runtime!;};

 function project(entry:AgentDirectoryEntry) {
  const name=entry.environment_name==='production'?entry.project_name:`${entry.project_name} (${entry.environment_name})`;
  const base=options.publicUrl().replace(/\/+$/,'');
  return {id:refOf(entry.environment),ref:refOf(entry.environment),organization_id:entry.organization,organization_slug:entry.organization,
   name,region:'self-hosted',status:status(entry),created_at:new Date(entry.created_at??0).toISOString(),
   database:{host:'',version:'17',postgres_engine:'17',release_channel:'ga'},
   // Not in Supabase's answer: where this project's API is, and what it is in Sbarbase.
   api_url:entry.runtime?`${base}/${entry.runtime}`:null,
   sbarbase:{organization:entry.organization,project:entry.project,environment:entry.environment,environment_name:entry.environment_name,runtime:entry.runtime}};
 }

 /** A console route on the token's behalf; its error answer is passed on as it is. */
 async function call(token:AccessToken,method:string,path:string,body?:unknown):Promise<any> {
  const response=await dispatch(token,method,'/management/v1'+path,body);
  const value=await response.json().catch(()=>({}));
  if(!response.ok)refuse(response.status,typeof value?.message==='string'?value.message:'The request failed');
  return value;
 }

 /** The SQL target, once database access is on. Only a call that may change something (`enable`)
  * turns it on; a read never does. */
 async function databaseReady(token:AccessToken,entry:AgentDirectoryEntry,enable=false):Promise<SqlTarget> {
  const runtime=ready(entry);
  writer(entry);
  const state=(await call(token,'GET',`/environments/${entry.environment}/database`)).data as {desired?:string;state?:string;connection?:{host?:string;port?:number}};
  if(state?.desired==='on'&&state.state==='on')return {runtime,host:state.connection?.host,port:state.connection?.port};
  if(state?.desired==='on')refuse(409,'Database access is being turned on; try again in a minute');
  if(token.read_only||!enable)refuse(409,'Database access is off for this project. Turn it on in the console (Database connection), or run SQL with a token that can write, which turns it on');
  // The developer login is turned on as the console would, with a new password the server keeps;
  // nobody had the old one, since access was off.
  await call(token,'PUT',`/environments/${entry.environment}/database`,{enabled:true});
  return refuse(409,'Database access was off; it is being turned on now. Try again in a minute');
 }

 function functionView(entry:AgentDirectoryEntry,name:string,detail:{verify_jwt:boolean;updated_at:number}) {
  const runtime=entry.runtime!,id=functionId(runtime,name),version=Math.max(1,Math.floor(detail.updated_at/1000));
  const manifest=readManifest(functionsRoot,runtime).functions[name];
  const folder=manifest?join(functionsRoot,runtime,manifest.version,name):'';
  const entrypoint=ENTRYPOINTS.find(file=>folder&&existsSync(join(folder,file)))??'index.ts';
  const deployment=`${refOf(entry.environment)}_${id}_${version}`;
  return {id,slug:name,name,status:'ACTIVE',version,created_at:detail.updated_at,updated_at:detail.updated_at,verify_jwt:detail.verify_jwt,
   import_map:false,entrypoint_path:`file:///tmp/user_fn_${deployment}/source/${entrypoint}`};
 }
 async function functionList(token:AccessToken,entry:AgentDirectoryEntry) {
  const data=(await call(token,'GET',`/environments/${entry.environment}/functions`)).data as {functions:{name:string;verify_jwt:boolean;updated_at:number}[];secrets:string[]};
  return data;
 }

 async function deploy(token:AccessToken,entry:AgentDirectoryEntry,request:Request,url:URL) {
  writer(entry);ready(entry);
  const slug=url.searchParams.get('slug')??'';
  if(!FUNCTION_NAME.test(slug))refuse(400,'Give the function a name (?slug=): letters, digits, - and _');
  if(!(request.headers.get('content-type')??'').startsWith('multipart/form-data'))refuse(400,'Send the files as multipart/form-data');
  const body=await bounded(request,MAX_DEPLOY,'A deploy is at most 10 MiB');
  if(!body.length)refuse(400,'Send the files');
  let form:FormData;
  try{form=await new Response(new Uint8Array(body),{headers:{'content-type':request.headers.get('content-type')!}}).formData();}
  catch{return refuse(400,'The multipart body could not be read');}
  let metadata:{entrypoint_path?:string;verify_jwt?:boolean}={};
  const meta=form.get('metadata');
  if(meta!==null){
   try{metadata=JSON.parse(typeof meta==='string'?meta:await meta.text());}catch{refuse(400,'"metadata" is JSON');}
  }
  const files:{name:string;content:string}[]=[];
  for(const part of form.getAll('file')){
   if(typeof part==='string')refuse(400,'Each "file" part needs a file name');
   const file=part as File;
   files.push({name:file.name,content:await file.text()});
  }
  if(!files.length)refuse(400,'Send at least one file');
  const split=splitDeployFiles(files,metadata.entrypoint_path,slug);
  const verify=typeof metadata.verify_jwt==='boolean'?metadata.verify_jwt:true;
  const deployed=(await call(token,'PUT',`/environments/${entry.environment}/functions/${slug}`,{files:split.files,shared:split.shared,verify_jwt:verify})).data.deployed;
  return functionView(entry,slug,{verify_jwt:deployed.verify_jwt,updated_at:deployed.updated_at});
 }

 function functionBody(entry:AgentDirectoryEntry,name:string):Response {
  const runtime=entry.runtime!;
  const manifest=readManifest(functionsRoot,runtime).functions[name];
  if(!manifest)refuse(404,'Function not found');
  const version=join(functionsRoot,runtime,manifest!.version);
  // multipart/form-data by hand: one metadata part, then one part per file, as Supabase answers.
  const boundary='sbarbase-'+createHash('sha256').update(runtime+name+manifest!.version).digest('hex').slice(0,24);
  const parts:Buffer[]=[];
  const part=(headers:string,content:Buffer)=>parts.push(Buffer.from(`--${boundary}\r\n${headers}\r\n\r\n`),content,Buffer.from('\r\n'));
  const quoted=(value:string)=>value.replace(/["\\\r\n]/g,'_');
  part('Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json',Buffer.from(JSON.stringify({name,verify_jwt:manifest!.verify_jwt})));
  const files=[...walk(join(version,name)).map(path=>({path,name:path,root:join(version,name)})),
   ...walk(join(version,'_shared')).map(path=>({path,name:'_shared/'+path,root:join(version,'_shared')}))];
  for(const file of files)part(`Content-Disposition: form-data; name="file"; filename="${quoted(file.name)}"\r\nContent-Type: application/typescript`,readFileSync(join(file.root,file.path)));
  parts.push(Buffer.from(`--${boundary}--\r\n`));
  return new Response(Buffer.concat(parts),{status:200,headers:{'content-type':`multipart/form-data; boundary=${boundary}`,'cache-control':'no-store'}});
 }

 async function logs(token:AccessToken,entry:AgentDirectoryEntry,url:URL) {
  ready(entry);
  const query=url.searchParams.get('sql')??'';
  const table=query.match(/source\s*=\s*'([a-z_]+)'/)?.[1]??'edge_logs';
  const source=({auth_logs:'auth',storage_logs:'storage',realtime_logs:'realtime',function_logs:'functions',function_edge_logs:'functions',postgres_logs:'rest'} as Record<string,string>)[table]??'requests';
  const data=(await call(token,'GET',`/environments/${entry.environment}/logs?source=${source}&lines=200`)).data as {requests?:{at?:number;method:string;service:string;path:string;status:number;ms:number}[];lines?:(string|{at?:string;text?:string})[]};
  if(data.requests)return {result:data.requests.map(item=>({timestamp:item.at?item.at*1000:null,event_message:`${item.method} ${item.status} ${item.service} ${item.path} ${Math.round(item.ms)}ms`,
   status_code:item.status,method:item.method,path:item.path}))};
  return {result:(data.lines??[]).map(line=>typeof line==='string'?{event_message:line}:{timestamp:line.at??null,event_message:line.text??''})};
 }

 async function securityLints(token:AccessToken,entry:AgentDirectoryEntry) {
  // Public tables without row level security: anyone with the publishable key reads them. Needs
  // database access; without it the list is empty and says why.
  let target:SqlTarget;
  try{target=await databaseReady(token,entry);}catch(error){return {lints:[],message:error instanceof Error?error.message:''};}
  const rows=await sql.query(target,{readOnly:true,query:`SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relrowsecurity ORDER BY 1`}) as {name:string}[];
  return {lints:rows.map(row=>({name:'rls_disabled_in_public',title:'RLS Disabled in Public',level:'ERROR',facing:'EXTERNAL',categories:['SECURITY'],
   description:'Detects cases where row level security (RLS) has not been enabled on tables in schemas exposed to the API',
   detail:`Table \`public.${row.name}\` is public, but RLS has not been enabled.`,
   remediation:'https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public',
   metadata:{schema:'public',name:row.name,type:'table'},cache_key:`rls_disabled_in_public_public_${row.name}`}))};
 }

 async function route(token:AccessToken,request:Request,url:URL):Promise<Response> {
  const method=request.method,path=url.pathname.replace(/\/+$/,'');
  const json=async()=>{
   const text=await bounded(request,MAX_JSON,'Request too large');
   try{return text.length?JSON.parse(text.toString('utf8')):{};}catch{return refuse(400,'Send JSON');}
  };
  const writes=method!=='GET'&&method!=='HEAD';
  // A read-only token reads; SQL is the one write-shaped call it may make, and it runs read only.
  if(writes&&token.read_only&&!/\/database\/query$/.test(path))refuse(403,'This access token is read only');

  if(path==='/v1/organizations'&&method==='GET')return reply(200,organizations(token).map(item=>({id:item.id,slug:item.id,name:item.name})));
  let match=path.match(/^\/v1\/organizations\/([a-f0-9-]{36})$/);
  if(match&&method==='GET'){
   const organization=organizations(token).find(item=>item.id===match![1]);
   if(!organization)refuse(404,'Organization not found');
   return reply(200,{id:organization!.id,slug:organization!.id,name:organization!.name,plan:'free',allowed_release_channels:['ga'],opt_in_tags:[]});
  }
  if(path==='/v1/projects'&&method==='GET')return reply(200,directory(token).map(project));
  if(path==='/v1/projects'&&method==='POST'){
   const input=await json() as {name?:unknown;organization_slug?:unknown;organization_id?:unknown};
   const organization=String(input.organization_slug??input.organization_id??'');
   if(token.scope.kind==='project')refuse(403,'This access token is for one project; it cannot create another');
   if(!organizations(token).some(item=>item.id===organization))refuse(404,'Organization not found');
   if(typeof input.name!=='string'||!input.name.trim())refuse(400,'Give the project a name');
   const created=await call(token,'POST',`/organizations/${organization}/projects`,{name:(input.name as string).trim().slice(0,100)});
   const entry=catalog.agentDirectory(token.actor).find(item=>item.environment===created.environment);
   if(!entry)refuse(500,'The project was created but cannot be read back');
   return reply(201,project(entry!));
  }
  match=path.match(/^\/v1\/projects\/([a-z]{20})(\/.*)?$/);
  if(!match)return reply(404,{message:'Not available on Sbarbase'});
  const entry=find(token,match[1]!),rest=match[2]??'';

  if(rest===''&&method==='GET')return reply(200,project(entry));
  if(rest===''&&method==='DELETE')refuse(501,'Delete projects in the Sbarbase console; deletion needs a person there');
  if(rest==='/pause'||rest==='/restore')refuse(501,'Sbarbase projects are not paused');
  if(rest==='/health'&&method==='GET')return reply(200,['auth','rest','storage','realtime'].map(name=>({name,healthy:entry.state==='succeeded',status:entry.state==='succeeded'?'ACTIVE_HEALTHY':'COMING_UP'})));

  if(rest==='/api-keys/legacy'&&method==='GET')return reply(200,{enabled:false});
  if(rest==='/api-keys'&&method==='GET'){
   ready(entry);
   type Listed={id:string;kind:string;name:string;created_at:number;revoked_at:number|null;token?:string|null};
   const list=async()=>((await call(token,'GET',`/environments/${entry.environment}/keys`)).data as Listed[])
    // A publishable key from before 0.5.0 cannot be shown, so it is left out rather than given as null.
    .filter(key=>!key.revoked_at&&(key.kind!=='publishable'||!!key.token));
   let keys=await list();
   // An app needs a publishable key and a new project has none: a token that may write gets one
   // made, named "default", as the console's Create key would. One at a time per environment, so
   // two calls at once make one key.
   if(!keys.some(key=>key.kind==='publishable')&&!token.read_only&&entry.role!=='viewer'){
    const previous=issuing.get(entry.environment)??Promise.resolve();
    const turn=previous.then(async()=>{
     if(!(await list()).some(key=>key.kind==='publishable'))
      await call(token,'POST',`/environments/${entry.environment}/keys`,{type:'publishable',name:'default'});
    });
    issuing.set(entry.environment,turn.catch(()=>{}));
    try{await turn;}finally{if(issuing.get(entry.environment)===turn)issuing.delete(entry.environment);}
    keys=await list();
   }
   return reply(200,keys.map(key=>({id:key.id,name:key.name,type:key.kind,description:null,
    api_key:key.kind==='publishable'?key.token??null:null,prefix:key.kind==='publishable'?'sb_publishable_':'sb_secret_',hash:null,
    secret_jwt_template:key.kind==='secret'?{role:'service_role'}:null,inserted_at:new Date(key.created_at).toISOString(),updated_at:new Date(key.created_at).toISOString()})));
  }
  if(rest==='/api-keys'&&method==='POST'){
   ready(entry);
   const input=await json() as {type?:unknown;name?:unknown};
   const issued=await call(token,'POST',`/environments/${entry.environment}/keys`,{type:input.type??'publishable',...(input.name!==undefined?{name:input.name}:{})});
   const now=new Date().toISOString();
   return reply(201,{id:issued.id,name:issued.name,type:issued.type,description:null,api_key:issued.token,
    prefix:issued.type==='secret'?'sb_secret_':'sb_publishable_',hash:null,secret_jwt_template:issued.type==='secret'?{role:'service_role'}:null,inserted_at:now,updated_at:now});
  }
  match=rest.match(/^\/api-keys\/([a-f0-9-]{36})$/);
  if(match&&method==='DELETE'){ready(entry);await call(token,'DELETE',`/environments/${entry.environment}/keys/${match[1]}`);return reply(200,{id:match[1]});}

  if(rest==='/database/query'&&method==='POST'){
   const input=await json() as {query?:unknown;parameters?:unknown;read_only?:unknown};
   const readOnly=token.read_only||input.read_only===true;
   const target=await databaseReady(token,entry,!readOnly);
   // Recorded before it runs: a write that commits and then fails (too large an answer, the
   // deadline) is still in the log.
   if(!readOnly)catalog.recordAgentSql(token.actor,entry.environment,'database.agent_query',
    {token:token.id,query:createHash('sha256').update(String(input.query)).digest('hex').slice(0,16)});
   return reply(201,await sql.query(target,{query:input.query as string,parameters:input.parameters as unknown[]|undefined,readOnly}));
  }
  if(rest==='/database/migrations'&&method==='GET')return reply(200,await sql.migrations(await databaseReady(token,entry)));
  if(rest==='/database/migrations'&&method==='POST'){
   const input=await json() as {name?:unknown;query?:unknown};
   const target=await databaseReady(token,entry,true);
   catalog.recordAgentSql(token.actor,entry.environment,'database.agent_migration',{token:token.id,name:String(input.name??'').slice(0,100)});
   return reply(200,await sql.migrate(target,{name:String(input.name??''),query:input.query as string}));
  }
  if(rest==='/types/typescript')refuse(501,'Type generation is not available on Sbarbase yet. Run `supabase gen types typescript --db-url "<connection string>"` with the project\'s database connection string');
  if(rest==='/advisors/security'&&method==='GET')return reply(200,await securityLints(token,entry));
  if(rest==='/advisors/performance'&&method==='GET')return reply(200,{lints:[]});
  if(rest==='/analytics/endpoints/logs'&&method==='GET')return reply(200,await logs(token,entry,url));

  if(rest==='/functions'&&method==='GET'){
   ready(entry);
   return reply(200,(await functionList(token,entry)).functions.map(item=>functionView(entry,item.name,item)));
  }
  // `supabase functions deploy --use-api` bundles first, then switches every function in one call;
  // a deploy here is already live, so the switch answers what is there.
  if(rest==='/functions'&&(method==='PUT'||method==='PATCH')){
   ready(entry);writer(entry);
   return reply(200,(await functionList(token,entry)).functions.map(item=>functionView(entry,item.name,item)));
  }
  if(rest==='/functions/deploy'&&method==='POST')return reply(201,await deploy(token,entry,request,url));
  match=rest.match(/^\/functions\/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(\/body)?$/);
  if(match){
   ready(entry);
   const name=match[1]!;
   if(method==='DELETE'){writer(entry);await call(token,'DELETE',`/environments/${entry.environment}/functions/${name}`);return reply(200,{});}
   if(method!=='GET')refuse(405,'Method not allowed');
   const item=(await functionList(token,entry)).functions.find(fn=>fn.name===name);
   if(!item)refuse(404,'Function not found');
   if(match[2]){writer(entry);return functionBody(entry,name);}
   return reply(200,functionView(entry,name,item!));
  }
  if(rest==='/secrets'&&method==='GET'){
   ready(entry);writer(entry);
   return reply(200,(await functionList(token,entry)).secrets.map(name=>({name,value:''})));
  }
  if(rest==='/secrets'&&(method==='POST'||method==='DELETE')){
   ready(entry);
   const input=await json();
   if(!Array.isArray(input)||input.length>100)refuse(400,method==='POST'?'Send a list of {"name", "value"}':'Send a list of names');
   const secrets:Record<string,string|null>={};
   for(const item of input as unknown[]){
    if(method==='DELETE'){if(typeof item!=='string')refuse(400,'Send a list of names');secrets[item as string]=null;continue;}
    const value=item as {name?:unknown;value?:unknown};
    if(typeof value?.name!=='string'||typeof value.value!=='string')refuse(400,'Each secret needs a "name" and a "value"');
    secrets[value.name as string]=value.value as string;
   }
   await call(token,'PUT',`/environments/${entry.environment}/function-secrets`,{secrets});
   return reply(method==='POST'?201:200,{});
  }
  if(rest==='/config/storage'&&method==='GET'){
   let limit=50*1024*1024;try{limit=uploadLimit();}catch{}
   return reply(200,{fileSizeLimit:limit,features:{imageTransformation:{enabled:false},s3Protocol:{enabled:false}}});
  }
  // The Data API settings, as `supabase config push` and the dashboard's API settings read and
  // change them. The signing secret is never part of the answer.
  if(rest==='/postgrest'&&(method==='GET'||method==='PATCH')){
   ready(entry);
   // The platform's empty schema, served when none of the exposed ones exists, is nothing the owner chose,
   // in either list.
   const view=(data:{schemas:string[];extraSearchPath:string[];maxRows:number|null})=>({db_schema:data.schemas.filter(name=>name!==DATA_API_EMPTY).join(','),
    db_extra_search_path:data.extraSearchPath.filter(name=>name!==DATA_API_EMPTY).join(','),max_rows:data.maxRows,db_pool:REST_POOL,db_anon_role:'anon',role_claim_key:'.role'});
   const current=(await call(token,'GET',`/environments/${entry.environment}/data-api`)).data;
   // What PostgREST serves now; a saved change appears once it is applied.
   if(method==='GET')return reply(200,view(current.served));
   writer(entry);
   const input=await json() as Record<string,unknown>;
   if(!input||typeof input!=='object'||Array.isArray(input))refuse(400,'Send a JSON object');
   if(input.db_pool!==undefined&&input.db_pool!==null&&input.db_pool!==REST_POOL)refuse(400,`The connection pool is fixed at ${REST_POOL} on Sbarbase`);
   let settings;
   // A partial change starts from the settings last accepted, not from what a start had to leave out.
   try{settings=mergeDataApi(current.applied,input,{schemas:'db_schema',extraSearchPath:'db_extra_search_path',maxRows:'max_rows'},true);}
   catch(error){return refuse(400,error instanceof Error?error.message:'Invalid settings');}
   const saved=(await call(token,'PUT',`/environments/${entry.environment}/data-api`,settings)).data;
   return reply(200,view(saved));
  }
  if(rest==='/config/database/pooler'&&method==='GET')return reply(200,[]);
  if(rest==='/branches'&&method==='GET')return reply(200,[]);
  return reply(404,{message:'Not available on Sbarbase'});
 }

 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const header=request.headers.get('authorization')??'';
  const bearer=/^Bearer (\S+)$/i.exec(header)?.[1]??'';
  let token:AccessToken|null=null;
  try {
   // The token first: unauthenticated traffic spends only its own budget, never the tokens'.
   token=tokens.resolve(bearer,tokenEpoch(catalog.managementSecurity));
   if(!token){
    if(!catalog.managementSecurity.rate('agent:invalid',600,60_000))return reply(429,{message:'Too many requests'});
    return reply(401,{message:'Unauthorized: send a Sbarbase access token (sbp_...) as Authorization: Bearer'});
   }
   if(!catalog.managementSecurity.rate('agent:token:'+token.id,300,60_000))return reply(429,{message:'Too many requests for this access token'});
   return await route(token,request,url);
  } catch(error) {
   if(error instanceof Refusal)return reply(error.status,{message:error.message});
   if(error instanceof SqlRefusal)return reply(error.status,{message:error.message});
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   return reply(500,{message:'The request failed'});
  }
 };
}
