import {serveLocal} from '../src/http/local-server';
import {createClient} from '@supabase/supabase-js';
import {openUpstreamApplication,internalToken} from './upstream-app';
import {managedGateway} from '../src/gateway/managed';
const sustained=process.argv.includes('--sustained');
const cancellation=process.argv.includes('--cancellation');
const deadlineProbe=process.argv.includes('--sql-deadline');
let deadlineObservation:unknown;
let cancellationObservation:unknown;
if([sustained,cancellation,deadlineProbe].filter(Boolean).length>1)throw new Error('Choose one workload mode');
if(process.argv.slice(2).some(arg=>arg!=='--sustained'&&arg!=='--cancellation'&&arg!=='--sql-deadline'))throw new Error('Unknown probe option');
const samples:{environment:string;status:number;duration_ms:number;correct:boolean;lag_ms:number;started_ms:number}[]=[];
let skipped=0,peakPending=0;
const app=openUpstreamApplication();
const secrets=await Bun.file('.secrets/upstream/runtime.json').json();
const endpoints=await Bun.file('.lab/upstream/endpoints.json').json();
const probe=await Bun.file('.lab/upstream/probe.json').json();
const name='overload_'+crypto.randomUUID().replaceAll('-','');
const fixtures:{runtime:string;key:string;token:string}[]=[];
const checks:string[]=[];let forwarded=0,finished=0;
let server:Awaited<ReturnType<typeof serveLocal>>|undefined;
let pending:Promise<boolean>[]=[];
function check(name:string,ok:boolean){if(!ok)throw new Error(name);checks.push(name);}
async function command(args:string[],input?:string){const child=Bun.spawn(args,{stdin:input===undefined?'ignore':'pipe',stdout:'ignore',stderr:'ignore'});if(input!==undefined){child.stdin.write(input);child.stdin.end();}if(await child.exited)throw new Error('Probe command failed');}
async function sql(runtime:string,query:string){await command(['docker','exec','-i','sbarbase-durable-db','psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',runtime],query);}
let primaryError:unknown=undefined;
try{
 // A fixture list is a claim, not a fact. An environment can be retired while its
 // catalog row survives, and then this probe dies on its first SQL statement with
 // an error that says nothing about the cause and a cleanup error on top of it.
 // The candidates are therefore probed, and only the ones that answer are used.
 const live:string[]=[];
 for(const id of probe.environments){
  const candidate=app.catalog.getProvision('durable-probe-owner',id);
  if(!candidate?.runtime){console.error('fixture environment has no provision record and is skipped:',id);continue;}
  try{await sql(candidate.runtime,'SELECT 1;');live.push(id);}
  catch{console.error('fixture environment does not answer and is skipped:',id);}
 }
 if(live.length<2)throw new Error(`Two answering fixture environments are required; ${live.length} of ${probe.environments.length} answered. The probe fixture is written by lab/durable-check.ts, which is disabled pending the container generation migration.`);
 for(const [index,id] of live.slice(0,2).entries()){
  const job=app.catalog.getProvision('durable-probe-owner',id);
  const key=app.catalog.withReadyEnvironment('durable-probe-owner',id,true,()=>app.keys.issue(job.runtime));
  fixtures.push({runtime:job.runtime,key:key.id,token:key.token});
  await sql(job.runtime,`CREATE FUNCTION public.${name}() RETURNS integer LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN ${index===0?'PERFORM pg_sleep(2);':''} RETURN ${index+1}; END $$; REVOKE ALL ON FUNCTION public.${name}() FROM PUBLIC; GRANT EXECUTE ON FUNCTION public.${name}() TO anon; NOTIFY pgrst,'reload schema';`);
 }
 const first=fixtures[0]!,second=fixtures[1]!;
 const transport=(async(input,init)=>{if(String(input)===endpoints[first.runtime].rest+'/rpc/'+name)forwarded++;return fetch(input,init);}) as typeof fetch;
 const handler=managedGateway(app.catalog,app.keys,runtime=>({...endpoints[runtime],keys:[],anonymousToken:internalToken(secrets.environments[runtime].jwt,'anon'),enabled:true}),transport);
 server=await serveLocal(handler);
 const base=`http://127.0.0.1:${server.port}`;
 const client=(f:typeof first)=>createClient(`${base}/${f.runtime}`,f.token,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:(input,init)=>fetch(input,{...init,signal:AbortSignal.timeout(15000)})}});
 const a=client(first),b=client(second);
 const admitted=endpoints[first.runtime].serviceConcurrency?.rest??8;
 if(sustained)check('published REST budget matches the three-connection lab pool',fixtures.every(f=>endpoints[f.runtime].serviceConcurrency?.rest===3));
 // Check schema discovery without executing the delaying function.
 for(const f of fixtures){let ready=false;for(let i=0;i<30;i++){const response=await fetch(`${base}/${f.runtime}/rest/v1/`,{headers:{apikey:f.token}});if((await response.text()).includes(name)){ready=true;break;}await Bun.sleep(100);}check('temporary RPC schema ready '+fixtures.indexOf(f),ready);}
 pending=Array.from({length:admitted},()=>a.rpc(name).then(response=>{finished++;return !response.error&&response.data===1;}));
 const deadline=Date.now()+5000;
 while(forwarded<admitted&&Date.now()<deadline)await Bun.sleep(10);
 check('configured concurrent requests admitted to actual REST',forwarded===admitted&&finished===0);
 const denied=await fetch(`${base}/${first.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers:{apikey:first.token,'content-type':'application/json'},body:'{}'});
 check('next request receives retryable overload',denied.status===429&&denied.headers.get('retry-after')==='1');await denied.text();
 check('rejected request never reaches REST',forwarded===admitted);
 const neighbor=await b.rpc(name);check('neighbor succeeds while target requests remain active',!neighbor.error&&neighbor.data===2&&finished<admitted);
 check('all admitted requests complete correctly',(await Promise.all(pending)).every(Boolean));
 const recovered=await a.rpc(name);check('target accepts requests after draining',!recovered.error&&recovered.data===1);
 if(deadlineProbe){
  const settingsName=name+'_settings';
  await sql(first.runtime,`CREATE FUNCTION public.${settingsName}() RETURNS json LANGUAGE sql SECURITY INVOKER AS $$ SELECT json_build_object('statement',current_setting('statement_timeout'),'transaction',current_setting('transaction_timeout')) $$; REVOKE ALL ON FUNCTION public.${settingsName}() FROM PUBLIC; GRANT EXECUTE ON FUNCTION public.${settingsName}() TO anon,service_role; NOTIFY pgrst,'reload schema';`);
  const headers={apikey:first.token,'content-type':'application/json',authorization:'Bearer '+internalToken(secrets.environments[first.runtime].jwt,'service_role')};
  await Bun.sleep(1000);
  const settingsResponse=await fetch(`${base}/${first.runtime}/rest/v1/rpc/${settingsName}`,{method:'POST',headers,body:'{}'});
  const settings=await settingsResponse.json();
  check('effective REST service defaults are eight and twelve seconds',settingsResponse.ok&&settings.statement==='8s'&&settings.transaction==='12s');
  await sql(first.runtime,`CREATE OR REPLACE FUNCTION public.${name}() RETURNS integer LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN PERFORM pg_sleep(20); RETURN 1; END $$; GRANT EXECUTE ON FUNCTION public.${name}() TO service_role; NOTIFY pgrst,'reload schema';`);
  const statementStarted=performance.now();
  const statementResponse=await fetch(`${base}/${first.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers,body:'{}',signal:AbortSignal.timeout(14000)});
  const statementBody=await statementResponse.json(),statementElapsed=performance.now()-statementStarted;
  check('ordinary service RPC obeys statement timeout',statementResponse.status===500&&statementBody.code==='57014'&&statementElapsed>7000&&statementElapsed<10000);
  await sql(first.runtime,`CREATE OR REPLACE FUNCTION public.${name}() RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET statement_timeout='0' AS $$ BEGIN PERFORM pg_sleep(20); RETURN 1; END $$; NOTIFY pgrst,'reload schema';`);
  await Bun.sleep(1000);
  const started=performance.now();
  const response=await fetch(`${base}/${first.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers:{apikey:first.token,'content-type':'application/json'},body:'{}',signal:AbortSignal.timeout(16000)});
  const value=await response.json(),elapsed=performance.now()-started;
  check('SQL transaction deadline ends RPC before gateway fetch timeout',response.status===503&&value.code==='PGRST001'&&elapsed>10000&&elapsed<14500);
  // This SELECT fails if the old RPC is still executing on any target backend.
  await sql(first.runtime,`DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname='${first.runtime}' AND usename='${first.runtime}_rest' AND state='active' AND query LIKE '%${name}%') THEN RAISE EXCEPTION 'RPC still active'; END IF; END $$;`);
  check('expired SQL is no longer active',true);
  const neighbor=await b.rpc(name);check('neighbor remains correct after SQL deadline',!neighbor.error&&neighbor.data===2);
  await sql(first.runtime,`CREATE OR REPLACE FUNCTION public.${name}() RETURNS integer LANGUAGE sql SECURITY INVOKER AS $$ SELECT 1 $$; ALTER FUNCTION public.${name}() RESET statement_timeout; NOTIFY pgrst,'reload schema';`);
  await Bun.sleep(1000);
  const after=await a.rpc(name);check('target reconnects and recovers after SQL deadline',!after.error&&after.data===1);
  deadlineObservation={scope:'Twenty-second RPC hoists statement_timeout=0. A per-login per-database transaction_timeout=12s must terminate SQL before the 15s gateway fetch timeout. Trusted functions that change transaction_timeout itself are outside this test.',effectiveServiceSettings:settings,statement:{status:statementResponse.status,code:statementBody.code,elapsed_ms:statementElapsed},status:response.status,code:typeof value.code==='string'?value.code:null,elapsed_ms:elapsed};
 }
 if(cancellation){
  const observe=async()=>{
   const child=Bun.spawn(['docker','exec','-i','sbarbase-durable-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres'],{stdin:'pipe',stdout:'pipe',stderr:'ignore'});
   child.stdin.write(`SELECT count(*) FROM pg_stat_activity WHERE datname='${first.runtime}' AND usename='${first.runtime}_rest' AND state='active' AND query LIKE '%${name}%' AND wait_event='PgSleep';`);child.stdin.end();
   const output=(await new Response(child.stdout).text()).trim();
   if(await child.exited||!/^\d+$/.test(output))throw new Error('Activity observation failed');
   return Number(output);
  };
  const abort=new AbortController();
  const start=performance.now();
  const request=Promise.all(Array.from({length:admitted},()=>fetch(`${base}/${first.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers:{apikey:first.token,'content-type':'application/json'},body:'{}',signal:abort.signal}).then(async response=>{await response.text();return false;},()=>true)));
  let active=0;const waitUntil=performance.now()+1500;
  while(!active&&performance.now()<waitUntil){active=await observe();if(!active)await Bun.sleep(20);}
  check('cancellation fixture observed actively sleeping in PostgreSQL',active===admitted);
  const abortedAt=performance.now();abort.abort();
  check('client observes request cancellation',(await request).every(Boolean));
  await Bun.sleep(500);
  const activeAfter500ms=await observe();
  const denied=await fetch(`${base}/${first.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers:{apikey:first.token,'content-type':'application/json'},body:'{}'});
  check('cancelled REST requests retain admission while SQL runs',activeAfter500ms===admitted&&denied.status===429);await denied.text();
  const during=await b.rpc(name);check('neighbor succeeds while cancelled SQL still runs',!during.error&&during.data===2);

  const observations=[{elapsed_after_abort_ms:performance.now()-abortedAt,active:activeAfter500ms}];
  const end=performance.now()+5000;
  while(activeAfter500ms&&performance.now()<end){
   await Bun.sleep(100);const count=await observe();
   observations.push({elapsed_after_abort_ms:performance.now()-abortedAt,active:count});
   if(!count)break;
  }
  check('cancelled fixture eventually leaves active SQL state',observations.at(-1)!.active===0);
  const other=await b.rpc(name);check('neighbor remains correct after cancellation',!other.error&&other.data===2);
  const after=await a.rpc(name);check('target responds correctly after cancelled SQL drains',!after.error&&after.data===1);
  cancellationObservation={scope:'Configured REST capacity of two-second sleep RPCs. Active backend observed before client abort; polls observe SQL, not only gateway slots. Prompt cancellation is observed only if activeAfter500ms is zero, not implied by probe completion.',activeAfter500ms,abort_after_start_ms:abortedAt-start,observations};
 }
 if(sustained){
  const starts=performance.now();let active=0;
  const send=(fixture:typeof first,expected:number,lag:number)=>{
   active++;peakPending=Math.max(peakPending,active);
   const began=performance.now();
   const task=(async()=>{
    let status=0,correct=false;
    try{
     const response=await fetch(`${base}/${fixture.runtime}/rest/v1/rpc/${name}`,{method:'POST',headers:{apikey:fixture.token,'content-type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)});
     status=response.status;const body=await response.json();
     correct=response.ok?body===expected:status===429&&response.headers.get('retry-after')==='1';
    }catch{}
    samples.push({environment:expected===1?'target':'neighbor',status,duration_ms:performance.now()-began,correct,lag_ms:lag,started_ms:began-starts});
    active--;return correct;
   })();
   pending.push(task);
  };
  // Offered arrivals do not wait for responses. Up to 100 ms scheduling jitter is allowed.
  for(let tick=0;tick<600;tick++){
   const due=starts+tick*50;
   await Bun.sleep(Math.max(0,due-performance.now()));
   const lag=performance.now()-due;
   if(lag>100||active>=128){skipped++;continue;}
   send(first,1,lag);
   if(tick%10===0)send(second,2,lag);
  }
  await Promise.all(pending);
  check('open-loop generator kept schedule without missed arrivals',skipped===0);
  check('sustained responses all match expected data or retryable rejection',samples.every(sample=>sample.correct));
  const target=samples.filter(sample=>sample.environment==='target');
  const neighbor=samples.filter(sample=>sample.environment==='neighbor');
  check('sustained target includes successful and rejected work',target.some(sample=>sample.status===200)&&target.some(sample=>sample.status===429));
  check('all scheduled neighbor requests succeed',neighbor.length===60&&neighbor.every(sample=>sample.status===200));
  const after=await a.rpc(name);
  check('target recovers after sustained arrivals drain',!after.error&&after.data===1);
 }

}catch(error){
 // An empty result is not evidence. Writing this when the probe died in setup is
 // what replaced a previous run's 660 sample artifact with an empty record, so the
 // artifact is only written when the run actually produced samples.
 if(sustained&&samples.length)await Bun.write('docs/evidence/gateway-sustained-failure.json',JSON.stringify({scope:'Failed slow-RPC arrival probe; no capacity or isolation pass',checks,skipped,peakPending,samples},null,2)+'\n');
 primaryError=error;
}finally{
 await Promise.allSettled(pending);
 const failures=[];
 for(const f of fixtures){try{await sql(f.runtime,`DROP FUNCTION IF EXISTS public.${name}(); DROP FUNCTION IF EXISTS public.${name}_settings(); NOTIFY pgrst,'reload schema';`);}catch{failures.push('SQL cleanup');}try{if(!app.keys.revoke(f.runtime,f.key))failures.push('key cleanup');}catch{failures.push('key cleanup');}}
 try{server?.stop(true);}catch{failures.push('server cleanup');}
 try{app.close();}catch{failures.push('catalog cleanup');}
 try{await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);}catch{failures.push('runtime cleanup');}
 if(failures.length&&primaryError!==undefined)console.error('fixture cleanup failures:',failures.join(','));
 if(failures.length&&primaryError===undefined)throw new Error('Overload fixture cleanup incomplete: '+failures.join(','));
}
if(primaryError!==undefined)throw primaryError;
await Bun.write(sustained?'docs/evidence/gateway-sustained-checks.json':cancellation?'docs/evidence/gateway-cancellation-checks.json':deadlineProbe?'docs/evidence/sql-deadline-checks.json':'docs/evidence/gateway-overload-checks.json',JSON.stringify({sqlDeadline:deadlineObservation,cancellation:cancellationObservation,sustained:sustained?{duration_seconds:30,target_arrivals_per_second:20,neighbor_arrivals_per_second:2,rest_budget:3,skipped,peakPending,samples}:undefined,scope:'Actual managed gateway, SDK, pinned PostgREST and PostgreSQL. One environment fills its configured admission budget with temporary two-second RPCs, next request refused; neighbor returns a correct distinct value and target recovers. Temporary RPCs removed and keys revoked. Not global socket or multi-process DDoS protection.',checks,count:checks.length},null,2)+'\n');
console.log(`${checks.length} real gateway overload checks passed.`);
