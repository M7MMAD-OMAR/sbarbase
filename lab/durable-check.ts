import {Catalog} from '../src/control/catalog';
import {createGateway} from '../src/gateway/handler';
import {createClient} from '@supabase/supabase-js';
import {createHash,createHmac} from 'node:crypto';
import {selectFixture,writeFixture,type Candidate} from './durable-fixture';

// Non-destructive lifecycle probe of the retained durable runtime on its current database
// generation. Two published, unfenced environments carry an SDK data path; the runtime is
// then stopped and started through lab/durable_runtime.py, which resumes the retained
// containers, and everything is checked again. Nothing is provisioned and no container is
// removed: recreating the database is lab/migrate-generation.py's job and recreating
// services is lab/upgrade.py's. The environment the cutover exported is never touched.

const directory='.lab/upstream',actor='durable-probe-owner',OWNER='durable-upstream',DB='sbarbase-durable-db';
const RUNTIMES=['e_f61bf85dccd73890ec63997c','e_1f0624c545789214eef426c9'];
const EXCLUDED=['e_60332245e3a0426dd242492f'];
const EVIDENCE='docs/evidence/durable-lifecycle-restart.json';
const SCOPE='Two published, unfenced retained environments on the migrated database generation: original Supabase Auth, REST and shared Storage through the SDK and a loopback gateway, then a full stop and start of the retained runtime through lab/durable_runtime.py, resuming every owned container rather than recreating it. Container recreation is covered by lab/migrate-generation.py (database) and lab/upgrade.py (services), not by this probe. The environment exported by the cutover is not touched and nothing is provisioned. No host-loss backup, load or production readiness claim.';

const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
async function command(args:string[]) {
 const child=Bun.spawn(args,{stdout:'pipe',stderr:'ignore'});
 const output=await new Response(child.stdout).text();
 if(await child.exited)throw new Error('Runtime probe command failed: '+args.slice(0,3).join(' '));return output.trim();
}
async function sql(query:string,database='postgres') {
 return command(['docker','exec',DB,'psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,'-Atc',query]);
}
async function owned():Promise<Map<string,string>> {
 const lines=(await command(['docker','ps','--no-trunc','--filter','label=io.sbarbase.owner='+OWNER,'--format','{{.Names}} {{.ID}}'])).split('\n').filter(Boolean);
 return new Map(lines.map(line=>{const [name,id]=line.split(' ');return [name!,id!];}));
}
async function pin() {
 const text=await Bun.file(`${directory}/hba-generation.json`).text();
 const record=JSON.parse(text).record;
 return {digest:createHash('sha256').update(text).digest('hex'),container:record.target.container_id as string,generation:record.generation as string};
}
const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));
const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
let secrets:any,endpoints:any;
function jwt(runtime:string,role:string){const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role});return message+'.'+createHmac('sha256',secrets.environments[runtime].jwt).update(message).digest('base64url');}

const catalog=new Catalog(`${directory}/control.sqlite`);
const stateFile=Bun.file(`${directory}/probe.json`);
let server:ReturnType<typeof Bun.serve>|undefined;
let passed=false,failure:unknown;
const cleanupFailures:string[]=[];
const data:{runtime:string;user:string;token:string;email:string;oid:string;path:string;signed:string;value:string}[]=[];
let fixture:ReturnType<typeof selectFixture>|undefined;
let before:Awaited<ReturnType<typeof pin>>|undefined,containers=0;
const suffix=crypto.randomUUID(),password=`Local-${suffix}`;
try {
 // Start the retained runtime through its supported path; startup refuses on headroom or a changed pin.
 await command(['/usr/bin/python3','lab/durable_runtime.py','up']);
 before=await pin();
 const ids=await owned();
 check('the running database is the pinned generation container',ids.get(DB)===before.container);

 secrets=await Bun.file('.secrets/upstream/runtime.json').json();
 endpoints=await Bun.file(`${directory}/endpoints.json`).json();
 const previous=await stateFile.exists()?await stateFile.json():null;
 const candidates:Candidate[]=[];
 for(const organization of catalog.listOrganizations(actor))
  for(const project of catalog.listProjects(actor,organization.id))
   for(const environment of catalog.listEnvironments(actor,project.id)) {
    let runtime:string|null=null;
    try{runtime=catalog.getProvision(actor,environment.id).runtime;}catch{runtime=null;}
    candidates.push({environment:environment.id,project:project.id,organization:organization.id,runtime,state:environment.state});
   }
 const observed=new Map<string,{published:boolean;maintenance:boolean;fenced:boolean}>();
 for(const runtime of RUNTIMES) {
  if(EXCLUDED.includes(runtime))continue;
  const published=!!endpoints[runtime]&&!!secrets.environments?.[runtime];
  const fence=published?await sql(`SELECT NOT datallowconn OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('${runtime}_auth','${runtime}_rest','${runtime}_storage') AND NOT rolcanlogin) FROM pg_database WHERE datname='${runtime}';`):'';
  observed.set(runtime,{published:published&&fence!=='',maintenance:catalog.runtimeRouting(runtime).maintenance,fenced:fence!=='f'});
 }
 const project=previous?.project??candidates.find(item=>item.runtime===RUNTIMES[0])?.project;
 fixture=selectFixture(candidates,RUNTIMES,EXCLUDED,runtime=>observed.get(runtime)??{published:false,maintenance:false,fenced:false},project);
 check('both fixture environments are published, unfenced and served by the source',fixture.environments.length===2);

 const keys=Object.fromEntries(RUNTIMES.map(runtime=>[runtime,crypto.randomUUID()]));
 const registry=new Map();
 function refreshRoutes(){for(const runtime of RUNTIMES)registry.set(runtime,{
  ...endpoints[runtime],keys:[keys[runtime]],anonymousToken:jwt(runtime,'anon'),enabled:true});}
 refreshRoutes();
 server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:createGateway(registry)});
 const base=`http://127.0.0.1:${server.port}`;
 const client=(runtime:string)=>createClient(`${base}/${runtime}`,keys[runtime]!,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const denied=async(runtime:string,path:string,token:string)=>
  !(await fetch(`${base}/${runtime}/storage/v1/object/durable-private/${path}`,{headers:{apikey:keys[runtime]!,authorization:'Bearer '+token}})).ok;

 for(const runtime of RUNTIMES) {
  await sql(`CREATE TABLE IF NOT EXISTS public.durable_items(id uuid PRIMARY KEY,owner_id uuid NOT NULL,value text NOT NULL); ALTER TABLE public.durable_items ENABLE ROW LEVEL SECURITY;
   DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='public' AND tablename='durable_items' AND policyname='owner_only') THEN CREATE POLICY owner_only ON public.durable_items TO authenticated USING(owner_id=auth.uid()) WITH CHECK(owner_id=auth.uid()); END IF; END $$;
   GRANT SELECT,INSERT ON public.durable_items TO authenticated;
   DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='storage' AND tablename='objects' AND policyname='owner_only') THEN CREATE POLICY owner_only ON storage.objects TO authenticated USING(owner_id=auth.uid()::text) WITH CHECK(owner_id=auth.uid()::text); END IF; END $$;
   NOTIFY pgrst,'reload schema';`,runtime);
  const sdk=client(runtime);
  const email=`durable-${suffix}@example.com`;
  const account=await sdk.auth.signUp({email,password});
  check(runtime+' SDK signup works',!account.error&&!!account.data.session);
  if(!account.data.session)throw new Error('No session');
  const user=account.data.user!.id,access=account.data.session.access_token;
  // PostgREST reloads its schema cache asynchronously after the NOTIFY above.
  let row=await sdk.from('durable_items').insert({id:suffix,owner_id:user,value:runtime});
  for(let attempt=0;row.error&&attempt<20;attempt++){await sleep(500);row=await sdk.from('durable_items').insert({id:suffix,owner_id:user,value:runtime});}
  check(runtime+' SDK inserts row through real Auth RLS',!row.error);
  const headers={authorization:'Bearer '+jwt(runtime,'service_role'),'x-forwarded-host':runtime+'.storage.internal','content-type':'application/json'};
  const bucket=await fetch(endpoints[runtime].storage.url+'/bucket',{method:'POST',headers,body:JSON.stringify({id:'durable-private',name:'durable-private',public:false})});
  check(runtime+' private bucket exists',bucket.ok||bucket.status===409||await sql("SELECT count(*) FROM storage.buckets WHERE id='durable-private' AND NOT public",runtime)==='1');
  const path=`${suffix}.txt`,value='persistent-'+runtime;
  const upload=await sdk.storage.from('durable-private').upload(path,value,{contentType:'text/plain'});
  check(runtime+' SDK private upload works',!upload.error);
  const signed=await sdk.storage.from('durable-private').createSignedUrl(path,600);
  check(runtime+' signed URL issued before restart',!signed.error&&!!signed.data?.signedUrl);
  data.push({runtime,user,token:access,email,oid:await sql(`SELECT oid FROM pg_database WHERE datname='${runtime}'`),path,value,signed:signed.data!.signedUrl});
 }
 check('same email has separate Auth identities',new Set(data.map(item=>item.user)).size===2);
 for(const item of data) {
  const other=data.find(value=>value.runtime!==item.runtime)!;
  check(item.runtime+' neighboring token denied before restart',await denied(item.runtime,item.path,other.token));
 }

 // The supported full stop and start: retained containers are resumed, never removed.
 const running=await owned();containers=running.size;
 await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);
 check('stop leaves no owned container running',(await owned()).size===0);
 await command(['/usr/bin/python3','lab/durable_runtime.py','up']);
 const resumed=await owned();
 check('the database container is the same migrated generation after restart',resumed.get(DB)===before.container);
 const after=await pin();
 check('the generation pin is unchanged by stop and start',after.digest===before.digest&&after.generation===before.generation);
 check('every owned container that ran before is resumed with the same id',
  [...running].every(([name,id])=>resumed.get(name)===id)&&resumed.size===running.size);
 endpoints=await Bun.file(`${directory}/endpoints.json`).json();
 refreshRoutes();
 for(const item of data) {
  const sdk=client(item.runtime);
  const login=await sdk.auth.signInWithPassword({email:item.email,password});
  check(item.runtime+' Auth login retains user identity after restart',!login.error&&login.data.user?.id===item.user);
  check(item.runtime+' database identity retained',await sql(`SELECT oid FROM pg_database WHERE datname='${item.runtime}'`)===item.oid);
  const rows=await sdk.from('durable_items').select().eq('id',suffix);
  check(item.runtime+' SDK row survives restart',!rows.error&&rows.data?.[0]?.value===item.runtime);
  const file=await sdk.storage.from('durable-private').download(item.path);
  check(item.runtime+' private object bytes survive restart',!file.error&&await file.data!.text()===item.value);
  const signed=await fetch(item.signed);
  check(item.runtime+' earlier signed URL survives restart',signed.ok&&await signed.text()===item.value);
  const other=data.find(value=>value.runtime!==item.runtime)!;
  check(item.runtime+' neighboring token remains denied after restart',await denied(item.runtime,item.path,other.token));
 }
 passed=true;
 console.log(`${checks.length} durable lifecycle restart checks passed.`);
} catch(error) {
 failure=error;
} finally {
 // This run's own rows, objects and Auth users; the table, policies and bucket stay.
 for(const item of data) {
  try{await sql(`DELETE FROM public.durable_items WHERE id='${suffix}';`,item.runtime);}catch{cleanupFailures.push(item.runtime+' row');}
  try{
   const storage=endpoints[item.runtime].storage;
   const response=await fetch(`${storage.url}/object/durable-private/${item.path}`,{method:'DELETE',headers:{
    authorization:'Bearer '+jwt(item.runtime,'service_role'),'x-forwarded-host':storage.tenantHost}});
   if(!response.ok)cleanupFailures.push(item.runtime+' object');
  }catch{cleanupFailures.push(item.runtime+' object');}
  try{
   const response=await fetch(endpoints[item.runtime].auth+'/admin/users/'+item.user,{method:'DELETE',headers:{authorization:'Bearer '+jwt(item.runtime,'service_role')}});
   if(!response.ok)cleanupFailures.push(item.runtime+' Auth user');
  }catch{cleanupFailures.push(item.runtime+' Auth user');}
 }
 server?.stop(true);catalog.close();
 let stopped=false;
 try{await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);stopped=(await owned()).size===0;}catch{stopped=false;}
 const complete=passed&&stopped&&cleanupFailures.length===0;
 let archived:string|null=null;
 if(complete&&fixture) {
  archived=writeFixture(directory,fixture,new Date().toISOString().slice(0,10).replaceAll('-','')).archived;
  await Bun.write(EVIDENCE,JSON.stringify({scope:SCOPE,date:new Date().toISOString().slice(0,10),
   fixture:{runtimes:fixture.runtimes,excluded:EXCLUDED,previous_fixture_archived:archived!==null},
   database:{container:before!.container,generation:before!.generation,pin_sha256:before!.digest},
   owned_containers_resumed:containers,cleanup:'rows, objects and Auth users created by this run removed; table, policies and bucket kept',
   runtime_stopped_after:true,checks},null,2)+'\n');
 }
 await Bun.write(`${directory}/verification.json`,JSON.stringify({scope:SCOPE,passed,runtime_stopped_after:stopped,
  cleanup_failures:cleanupFailures,fixture_written:complete,checks},null,2));
 if(!complete)console.error('Durable lifecycle probe incomplete:',failure instanceof Error?failure.message:(cleanupFailures.join(', ')||'runtime stop'));
 if(failure||!complete)process.exitCode=1;
}
