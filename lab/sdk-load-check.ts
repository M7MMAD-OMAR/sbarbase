import {serveLocal} from '../src/http/local-server';
import {createClient} from '@supabase/supabase-js';
import {openUpstreamApplication,internalToken} from './upstream-app';

// Existing lab fixture actor authorizes temporary scoped keys. Setup is excluded
// from timing. Requests use the composed managed gateway and original SDK.
const outputs:Record<string,string>={'--overload-regression':'sdk-overload-regression.json','--policy-regression':'sdk-policy-regression.json'};
if(process.argv.length>3||(process.argv[2]&&!outputs[process.argv[2]]))throw new Error('Unknown SDK probe option');
const app=openUpstreamApplication();
const server=await serveLocal(app.handler);
const base=`http://127.0.0.1:${server.port}`;
const secrets=await Bun.file('.secrets/upstream/runtime.json').json();
const endpoints=await Bun.file('.lab/upstream/endpoints.json').json();
const probe=await Bun.file('.lab/upstream/probe.json').json();
const suffix=crypto.randomUUID().replaceAll('-','');
const table='load_'+suffix,bucket='load-'+suffix,policy='load_'+suffix;
const fixtures:any[]=[];
const samples:{phase:string;environment:number;operation:string;ms:number;ok:boolean;status:number|null}[]=[];
let result:unknown;
const deadline=()=>AbortSignal.timeout(10000);
function require(value:unknown,message:string):asserts value {if(!value)throw new Error(message);}
async function command(args:string[],input?:string) {
 const child=Bun.spawn(args,{stdin:input===undefined?'ignore':'pipe',stdout:'pipe',stderr:'ignore'});
 if(input!==undefined){child.stdin.write(input);child.stdin.end();}
 const text=await new Response(child.stdout).text();
 if(await child.exited)throw new Error('Probe command failed');
 return text.trim();
}
async function sql(database:string,query:string) {
 return command(['docker','exec','-i','sbarbase-durable-db','psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,'-At'],query);
}
function sdkStatus(response:any):number|null {
 const raw=response.status??response.error?.status??response.error?.statusCode;
 return typeof raw==='number'?raw:typeof raw==='string'&&/^\d{3}$/.test(raw)?Number(raw):null;
}
async function phase(name:string,concurrency:number) {
 const began=performance.now();
 const pressure=(async()=>{await Bun.sleep(5000);try{return JSON.parse(await command(['/usr/bin/python3','lab/pressure_admission.py']));}catch{return {measurement_unavailable:true};}})();
 await Promise.all(fixtures.map(async(f,environment)=>{
  let next=0;
  await Promise.all(Array.from({length:concurrency},async()=>{
   while(performance.now()-began<10000) {
    const i=next++,operation=['read','insert','identity','upload','download'][i%5]!;
    const start=performance.now();let ok=false;let status:number|null=0;
    try {
     if(operation==='read') {
      const response=await f.client.from(table).select('payload').eq('id',f.row).single();
      status=sdkStatus(response);
      ok=!response.error&&response.data?.payload===f.content;
     }else if(operation==='insert') {
      const response=await f.client.from(table).insert({id:crypto.randomUUID(),owner_id:f.user,payload:f.content}).select('payload').single();
      status=sdkStatus(response);
      ok=!response.error&&response.data?.payload===f.content;
     }else if(operation==='identity') {
      const response=await f.client.auth.getUser();status=sdkStatus(response);ok=!response.error&&response.data.user?.id===f.user;
     }else if(operation==='upload') {
      const path=`${name}-${i}.txt`;f.paths.push(path);
      const response=await f.client.storage.from(bucket).upload(path,f.content,{contentType:'text/plain'});
      status=sdkStatus(response);
      ok=!response.error&&response.data?.path===path;
     }else {
      const response=await f.client.storage.from(bucket).download('seed.txt');
      status=sdkStatus(response);
      ok=!response.error&&await response.data?.text()===f.content;
     }
    }catch{ok=false;}
    const elapsed=performance.now()-start;
    samples.push({phase:name,environment,operation,ms:elapsed,ok,status});
    await Bun.sleep(Math.max(0,100-elapsed));
   }
  }));
 }));
 const duration_ms=performance.now()-began;
 return {duration_ms,pressure_snapshot:await pressure};
}
try {
 for(const environment of probe.environments.slice(0,2)) {
  const job=app.catalog.getProvision('durable-probe-owner',environment);
  require(job.state==='succeeded','Fixture environment not ready');
  const credential=app.catalog.withReadyEnvironment('durable-probe-owner',environment,true,()=>app.keys.issue(job.runtime));
  const f:any={runtime:job.runtime,key:credential.id,paths:[],row:crypto.randomUUID(),content:'payload-'+job.runtime+'-'+suffix+'x'.repeat(2048)};
  fixtures.push(f);
  f.client=createClient(`${base}/${job.runtime}`,credential.token,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
   global:{fetch:(input,init)=>fetch(input,{...init,signal:deadline()})}});
  await sql(job.runtime,`CREATE TABLE public.${table}(id uuid PRIMARY KEY,owner_id uuid NOT NULL,payload text NOT NULL); ALTER TABLE public.${table} ENABLE ROW LEVEL SECURITY; CREATE POLICY owner_only ON public.${table} TO authenticated USING(owner_id=auth.uid()) WITH CHECK(owner_id=auth.uid()); GRANT SELECT,INSERT ON public.${table} TO authenticated; CREATE POLICY ${policy} ON storage.objects TO authenticated USING(bucket_id='${bucket}' AND owner_id=auth.uid()::text) WITH CHECK(bucket_id='${bucket}' AND owner_id=auth.uid()::text); NOTIFY pgrst,'reload schema';`);
  const signup=await f.client.auth.signUp({email:`load-${suffix}@example.com`,password:`Local-${crypto.randomUUID()}`});
  if(signup.data.user)f.user=signup.data.user.id;
  require(!signup.error&&signup.data.user&&signup.data.session,'SDK fixture signup failed');
  // Wait for asynchronous PostgREST schema-cache reload before measuring.
  let ready=false;
  for(let attempt=0;attempt<30;attempt++) {
   const read=await f.client.from(table).select('id').limit(0);
   if(!read.error){ready=true;break;}await Bun.sleep(100);
  }
  require(ready,'Fixture schema unavailable');
  const seed=await f.client.from(table).insert({id:f.row,owner_id:f.user,payload:f.content});require(!seed.error,'SDK fixture seed failed');
  const storage=endpoints[job.runtime].storage;
  const response=await fetch(storage.url+'/bucket',{method:'POST',signal:deadline(),headers:{authorization:'Bearer '+internalToken(secrets.environments[job.runtime].jwt,'service_role'),'x-forwarded-host':storage.tenantHost,'content-type':'application/json'},body:JSON.stringify({id:bucket,name:bucket,public:false})});
  require(response.status===201||response.status===200,'Private fixture bucket creation failed');f.bucket=true;
  f.paths.push('seed.txt');const upload=await f.client.storage.from(bucket).upload('seed.txt',f.content);require(!upload.error,'SDK fixture upload failed');
 }
 require(fixtures.length===2,'Two environments required');
 const phases={serial:await phase('serial',1),concurrent:await phase('concurrent',4)};
 const groups=[];
 for(const phaseName of ['serial','concurrent'])for(const operation of ['read','insert','identity','upload','download']) {
  const group=samples.filter(s=>s.phase===phaseName&&s.operation===operation),ordered=group.map(s=>s.ms).sort((a,b)=>a-b);
  groups.push({phase:phaseName,operation,samples:group.length,failures:group.filter(s=>!s.ok).length,median_ms:ordered[Math.floor(ordered.length/2)],p95_ms:ordered[Math.ceil(ordered.length*.95)-1]});
 }
 require(groups.every(group=>group.samples>=10),'Insufficient per-operation samples');
 result={scope:'Two retained environments through managed loopback gateway and Supabase SDK. Ten-second closed-loop phases, concurrency 1 then 4 per environment, maximum 8 total. Each worker waits until at least 100 ms after its previous operation began, so nominal rates are approximately 20 then 80 operations/second across both environments, actual rate decreases with latency. Five-operation repeating mix, tiny private files and fixture rows; setup excluded. No open-loop/production-capacity/SLO conclusion.',phases,groups,samples};
}finally{
 const failures:string[]=[];
 for(const f of fixtures) {
  try {
   if(f.bucket) {
    if(f.paths.length){const removed=await f.client.storage.from(bucket).remove(f.paths);require(!removed.error,'Object cleanup failed');}
    const storage=endpoints[f.runtime].storage;
    const response=await fetch(storage.url+'/bucket/'+bucket,{method:'DELETE',signal:deadline(),headers:{authorization:'Bearer '+internalToken(secrets.environments[f.runtime].jwt,'service_role'),'x-forwarded-host':storage.tenantHost}});
    require(response.ok,'Bucket cleanup failed');
   }
  }catch{failures.push('storage cleanup');}
  try{await sql(f.runtime,`DROP TABLE IF EXISTS public.${table}; DROP POLICY IF EXISTS ${policy} ON storage.objects; NOTIFY pgrst,'reload schema';`);}catch{failures.push('SQL cleanup');}
  try{if(f.user){const response=await fetch(endpoints[f.runtime].auth+'/admin/users/'+f.user,{method:'DELETE',signal:deadline(),headers:{authorization:'Bearer '+internalToken(secrets.environments[f.runtime].jwt,'service_role')}});require(response.ok,'Auth cleanup failed');}}catch{failures.push('Auth cleanup');}
  try{if(!app.keys.revoke(f.runtime,f.key))failures.push('key revocation');}catch{failures.push('key revocation');}
 }
 try{server.stop(true);}catch{failures.push('server cleanup');}
 try{app.close();}catch{failures.push('catalog cleanup');}
 try{await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);}catch{failures.push('runtime cleanup');}
 require(failures.length===0,'Probe cleanup incomplete; inspect retained fixture state');
}
await Bun.write('docs/evidence/'+(outputs[process.argv[2]??'']??'sdk-load-checks.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({...result as object,samples:undefined}));

require(samples.every(sample=>sample.ok),'SDK load contained failed or incorrect operations; evidence saved');
