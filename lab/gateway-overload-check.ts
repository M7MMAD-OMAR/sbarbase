import {serveLocal} from '../src/http/local-server';
import {createClient} from '@supabase/supabase-js';
import {openUpstreamApplication,internalToken} from './upstream-app';
import {managedGateway} from '../src/gateway/managed';
const sustained=process.argv.includes('--sustained');
if(process.argv.slice(2).some(arg=>arg!=='--sustained'))throw new Error('Unknown probe option');
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
try{
 for(const [index,id] of probe.environments.slice(0,2).entries()){
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
 if(sustained)await Bun.write('docs/evidence/gateway-sustained-failure.json',JSON.stringify({scope:'Failed slow-RPC arrival probe; no capacity or isolation pass',checks,skipped,peakPending,samples},null,2)+'\n');
 throw error;
}finally{
 await Promise.allSettled(pending);
 const failures=[];
 for(const f of fixtures){try{await sql(f.runtime,`DROP FUNCTION IF EXISTS public.${name}(); NOTIFY pgrst,'reload schema';`);}catch{failures.push('SQL cleanup');}try{if(!app.keys.revoke(f.runtime,f.key))failures.push('key cleanup');}catch{failures.push('key cleanup');}}
 try{server?.stop(true);}catch{failures.push('server cleanup');}
 try{app.close();}catch{failures.push('catalog cleanup');}
 try{await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);}catch{failures.push('runtime cleanup');}
 if(failures.length)throw new Error('Overload fixture cleanup incomplete');
}
await Bun.write(sustained?'docs/evidence/gateway-sustained-checks.json':'docs/evidence/gateway-overload-checks.json',JSON.stringify({sustained:sustained?{duration_seconds:30,target_arrivals_per_second:20,neighbor_arrivals_per_second:2,rest_budget:3,skipped,peakPending,samples}:undefined,scope:'Actual managed gateway, SDK, pinned PostgREST and PostgreSQL. One environment fills its configured admission budget with temporary two-second RPCs, next request refused; neighbor returns a correct distinct value and target recovers. Temporary RPCs removed and keys revoked. Not global socket or multi-process DDoS protection.',checks,count:checks.length},null,2)+'\n');
console.log(`${checks.length} real gateway overload checks passed.`);
