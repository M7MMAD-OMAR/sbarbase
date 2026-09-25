// Environment limit check: fill one installation to its environment limit and measure it.
//
// Usage: bun lab/environment-limit-check.ts <operator.json> [--evidence PATH]
//
// Against the running installation, the operator from the 0600 bootstrap file adds
// production environments to new projects until the catalog's limit refuses one. After
// each environment the host's available memory, load and every container's measured
// memory are sampled, so the evidence shows what an environment costs on this host next
// to what admission reserves for it. The refusal must be a 409 before any job is queued.
// The environments stay, as an operator's would.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/environment-limit-check.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/environment-limit-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const samples:unknown[]=[];

function meminfo(){
 const fields=Object.fromEntries(readFileSync('/proc/meminfo','utf8').split('\n').filter(Boolean)
  .map(line=>{const [name,value]=line.split(':');return [name!,Math.round(parseInt(value!,10)/1024)];}));
 return {total_mib:fields.MemTotal,available_mib:fields.MemAvailable,swap_used_mib:(fields.SwapTotal??0)-(fields.SwapFree??0)};
}
function mib(text:string){
 const match=text.match(/^([\d.]+)\s*([KMG]i?B|B)$/);if(!match)return 0;
 const unit=match[2]!;const value=parseFloat(match[1]!);
 return Math.round(unit.startsWith('G')?value*1024:unit.startsWith('M')?value:unit.startsWith('K')?value/1024:value/1048576);
}
async function sample(label:string,environments:number){
 const child=Bun.spawn(['docker','stats','--no-stream','--format','{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'],{stdout:'pipe',stderr:'ignore'});
 const rows=(await new Response(child.stdout).text()).trim().split('\n').filter(Boolean).map(line=>{
  const [name,usage,cpu]=line.split('\t');return {name:name!,used_mib:mib(usage!.split('/')[0]!.trim()),cpu:cpu!};});
 const containers=rows.filter(row=>row.name.startsWith('sbarbase-'));
 const entry={label,environments,host:meminfo(),load:readFileSync('/proc/loadavg','utf8').split(' ').slice(0,3).join(' '),
  containers_used_mib:containers.reduce((sum,row)=>sum+row.used_mib,0),containers};
 samples.push(entry);
 console.log(`sample ${label}: ${environments} environment(s), host available ${entry.host.available_mib} MiB, containers ${entry.containers_used_mib} MiB`);
}
async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath,JSON.stringify({check:'environment-limit',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),samples,checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nenvironment limit check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('the operator signs in',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body?{'content-type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0]?.id as string;
 const count=async()=>{
  let held=0;
  for(const project of (await call('GET',`/organizations/${organization}/projects`)).json?.data??[])
   for(const environment of (await call('GET',`/projects/${project.id}/environments`)).json?.data??[])
    if(['queued','running','succeeded'].includes((await call('GET',`/environments/${environment.id}/provision`)).json?.state))held++;
  return held;
 };
 let held=await count();
 await sample('start',held);
 for(let round=0;round<8;round++) {
  const project=await call('POST',`/organizations/${organization}/projects`,{name:`Limit ${round+1} ${new Date().toISOString().slice(11,19)}`});
  const environment=await call('POST',`/projects/${project.json?.id}/environments`,{name:'production'});
  if(environment.status===409) {
   const after=await count();
   record('the next environment is refused with 409 at the limit',environment.json?.message==='Environment capacity reached',`at ${held} environment(s)`);
   record('no job was queued for the refused environment',after===held,`${after} held`);
   break;
  }
  if(!record(`environment ${held+1} is accepted`,environment.status===202,`status ${environment.status}`))break;
  let state='queued';const deadline=Date.now()+10*60_000;
  while(Date.now()<deadline&&!['succeeded','failed','cancelled'].includes(state)){await Bun.sleep(3000);state=(await call('GET',`/environments/${environment.json.id}/provision`)).json?.state??'unknown';}
  if(!record(`environment ${held+1} is provisioned`,state==='succeeded',`state ${state}`))break;
  held++;
  await Bun.sleep(20_000);
  await sample(`after environment ${held}`,held);
 }
 const refused=checks.some(row=>row.check.startsWith('the next environment is refused'));
 record('the limit was reached',refused,`${held} environment(s)`);
} catch(error) {
 record('the check completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
