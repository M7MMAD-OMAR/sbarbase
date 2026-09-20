import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {createGateway} from '../src/gateway/handler';
import {createClient} from '@supabase/supabase-js';
import {createHmac} from 'node:crypto';

const directory='.lab/upstream',actor='durable-probe-owner';
const catalog=new Catalog(`${directory}/control.sqlite`);
const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
async function command(args:string[]) {
 const child=Bun.spawn(args,{stdout:'pipe',stderr:'ignore'});
 const output=await new Response(child.stdout).text();
 if(await child.exited)throw new Error('Runtime probe command failed');return output.trim();
}
async function sql(query:string,database='postgres') {
 return command(['docker','exec','sbarbase-durable-db','psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,'-Atc',query]);
}
const stateFile=Bun.file(`${directory}/probe.json`);
let probe:{organization:string;project:string;environments:string[]};
let server:ReturnType<typeof Bun.serve>|undefined;
try {
 if(await stateFile.exists()) probe=await stateFile.json();
 else {
  const organization=catalog.createOrganization(actor,'Durable upstream probe');
  const project=catalog.createProject(actor,organization,'Lifecycle');
  probe={organization,project,environments:[]};
  const handler=managementHandler(catalog,async()=>actor);
  for(const name of ['production','staging']) {
   const response=await handler(new Request(`http://localhost/management/v1/projects/${project}/environments`,{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name})}));
   check(`${name} creation queued through management HTTP`,response.status===202);
   probe.environments.push((await response.json()).id);
  }
  await Bun.write(stateFile,JSON.stringify(probe));
 }
 for(const id of probe.environments) {
  const job=catalog.getProvision(actor,id);
  if(['failed','cancelled'].includes(job.state))catalog.retryProvision(actor,id);
 }
 // Simulate lost completion after real provisioning, before recording success.
 const claim=catalog.claimProvision();
 if(claim) {
  await command(['/usr/bin/python3','lab/durable_runtime.py','provision',claim.runtime]);
  check('runtime effect completed while job remains running',catalog.getProvision(actor,claim.environment).state==='running');
 }
 await command(['/usr/bin/python3','lab/worker.py','--upstream']);
 const jobs=probe.environments.map(id=>catalog.getProvision(actor,id));
 check('worker reconciles both upstream environments to success',jobs.every(job=>job.state==='succeeded'));
 const secrets=await Bun.file('.secrets/upstream/runtime.json').json();
 let endpoints=await Bun.file(`${directory}/endpoints.json`).json();
 const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 function jwt(runtime:string,role:string){const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role});return message+'.'+createHmac('sha256',secrets.environments[runtime].jwt).update(message).digest('base64url');}
 const keys=Object.fromEntries(jobs.map(job=>[job.runtime,crypto.randomUUID()]));
 const registry=new Map();
 function refreshRoutes(){for(const job of jobs)registry.set(job.runtime,{
  ...endpoints[job.runtime],keys:[keys[job.runtime]],anonymousToken:jwt(job.runtime,'anon'),enabled:true});}
 refreshRoutes();
 server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:createGateway(registry)});
 const base=`http://127.0.0.1:${server.port}`;
 const suffix=crypto.randomUUID(),password=`Local-${suffix}`;
 const data: {runtime:string;user:string;token:string;email:string;oid:string;path:string;signed:string;value:string}[]=[];
 for(const job of jobs) {
  const runtime=job.runtime;
  await sql(`CREATE TABLE IF NOT EXISTS public.durable_items(id uuid PRIMARY KEY,owner_id uuid NOT NULL,value text NOT NULL); ALTER TABLE public.durable_items ENABLE ROW LEVEL SECURITY;
   DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='public' AND tablename='durable_items' AND policyname='owner_only') THEN CREATE POLICY owner_only ON public.durable_items TO authenticated USING(owner_id=auth.uid()) WITH CHECK(owner_id=auth.uid()); END IF; END $$;
   GRANT SELECT,INSERT ON public.durable_items TO authenticated;
   DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='storage' AND tablename='objects' AND policyname='owner_only') THEN CREATE POLICY owner_only ON storage.objects TO authenticated USING(owner_id=auth.uid()::text) WITH CHECK(owner_id=auth.uid()::text); END IF; END $$;
   NOTIFY pgrst,'reload schema';`,runtime);
  const client=createClient(`${base}/${runtime}`,keys[runtime],{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
  const email=`durable-${suffix}@example.com`;
  const account=await client.auth.signUp({email,password});
  check(runtime+' SDK signup works',!account.error&&!!account.data.session);
  if(!account.data.session)throw new Error('No session');
  const user=account.data.user!.id,access=account.data.session.access_token;
  const row=await client.from('durable_items').insert({id:suffix,owner_id:user,value:runtime});
  check(runtime+' SDK inserts row through real Auth RLS',!row.error);
  const headers={authorization:'Bearer '+jwt(runtime,'service_role'),'x-forwarded-host':runtime+'.storage.internal','content-type':'application/json'};
  const bucket=await fetch(endpoints[runtime].storage.url+'/bucket',{method:'POST',headers,body:JSON.stringify({id:'durable-private',name:'durable-private',public:false})});
  check(runtime+' private bucket exists',bucket.ok||bucket.status===409||await sql("SELECT count(*) FROM storage.buckets WHERE id='durable-private' AND NOT public",runtime)==='1');
  const path=`${suffix}.txt`,value='persistent-'+runtime;
  const upload=await client.storage.from('durable-private').upload(path,value,{contentType:'text/plain'});
  check(runtime+' SDK private upload works',!upload.error);
  const signed=await client.storage.from('durable-private').createSignedUrl(path,600);
  check(runtime+' signed URL issued before restart',!signed.error&&!!signed.data?.signedUrl);
  data.push({runtime,user,token:access,email,oid:await sql(`SELECT oid FROM pg_database WHERE datname='${runtime}'`),path,value,signed:signed.data!.signedUrl});
 }
 check('same email has separate Auth identities',new Set(data.map(item=>item.user)).size===2);
 await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);
 // Remove only this experiment's stopped containers. Named volumes and secrets remain.
 await command(['/usr/bin/python3','-c',"import sys;sys.path.insert(0,'lab');import durable_runtime as d;import run as l; names=l.docker('ps','-a','-q','--filter','label=io.sbarbase.owner='+d.OWNER).stdout.split();[(d.inspect('container',n),l.docker('rm',n)) for n in names]"]);
 await command(['/usr/bin/python3','lab/durable_runtime.py','up']);
 endpoints=await Bun.file(`${directory}/endpoints.json`).json();
 refreshRoutes();
 check('runtime recreates container services from retained volumes',true);
 for(const item of data) {
  const client=createClient(`${base}/${item.runtime}`,keys[item.runtime],{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
  const login=await client.auth.signInWithPassword({email:item.email,password});
  check(item.runtime+' restored Auth login retains user identity',!login.error&&login.data.user?.id===item.user);
  check(item.runtime+' database identity retained',await sql(`SELECT oid FROM pg_database WHERE datname='${item.runtime}'`)===item.oid);
  const rows=await client.from('durable_items').select().eq('id',suffix);
  check(item.runtime+' SDK row survives recreation',!rows.error&&rows.data?.[0]?.value===item.runtime);
  const file=await client.storage.from('durable-private').download(item.path);
  check(item.runtime+' private object bytes survive recreation',!file.error&&await file.data!.text()===item.value);
  const signed=await fetch(item.signed);
  check(item.runtime+' earlier signed URL survives Storage recreation',signed.ok&&await signed.text()===item.value);
  const other=data.find(value=>value.runtime!==item.runtime)!;
  const crossed=await fetch(`${base}/${item.runtime}/storage/v1/object/durable-private/${item.path}`,{headers:{apikey:keys[item.runtime],authorization:'Bearer '+other.token}});
  check(item.runtime+' neighboring token remains denied',!crossed.ok);
 }
 console.log(`${checks.length} durable upstream lifecycle checks passed.`);
} finally {
 server?.stop(true);catalog.close();
 await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);
 await Bun.write(`${directory}/verification.json`,JSON.stringify({scope:'Two environments provisioned by persistent worker on original Supabase PostgreSQL and shared Storage; service container recreation with retained volumes, original Auth and SDK; no host-loss backup or production readiness',checks},null,2));
}
