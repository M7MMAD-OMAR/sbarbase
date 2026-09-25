// Environment limit check: fill one installation to its environment limit and measure it.
//
// Usage: bun lab/environment-limit-check.ts <operator.json> [--evidence PATH]
//
// Against the running installation, the operator from the 0600 bootstrap file adds
// production environments to new projects until the catalog's limit refuses one. After
// each environment the host's available memory, load and every container's measured
// memory are sampled, so the evidence shows what an environment costs on this host next
// to what admission reserves for it. The limit is either the count guard (a 409 before any
// job is queued) or, on a smaller host, the worker's admission (capacity_exceeded).
// The environments stay, as an operator's would.
import {readFileSync} from 'node:fs';
import {checkList,installationUrl,managementCaller,operatorFrom,option,signIn,waitProvisioned} from './check-kit';

const args=process.argv.slice(2);
const operator=operatorFrom(args[0],'usage: bun lab/environment-limit-check.ts <operator.json> [--evidence PATH]');
const base=installationUrl();
const samples:unknown[]=[];
const {record,finish}=checkList('environment-limit',option(args,'--evidence','docs/evidence/environment-limit-check.json'),()=>({samples}));

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
try {
 const login=await signIn(base,operator.email,operator.password);
 if(!record('the operator signs in',!!login.token,login.error))await finish();
 const call=managementCaller(base,login.token!);
 const organization=(await call('GET','/organizations')).json?.data?.[0]?.id as string;
 const count=async()=>{
  let held=0;
  for(const project of (await call('GET',`/organizations/${organization}/projects`)).json?.data??[])
   for(const environment of (await call('GET',`/projects/${project.id}/environments`)).json?.data??[])
    if(['queued','running','succeeded'].includes((await call('GET',`/environments/${environment.id}/provision`)).json?.state))held++;
  return held;
 };
 let held=await count(),limitReached=false;
 await sample('start',held);
 for(let round=0;round<8;round++) {
  const project=await call('POST',`/organizations/${organization}/projects`,{name:`Limit ${round+1} ${new Date().toISOString().slice(11,19)}`});
  const environment=await call('POST',`/projects/${project.json?.id}/environments`,{name:'production'});
  if(environment.status===409) {
   const after=await count();
   record('the next environment is refused with 409 at the limit',environment.json?.message==='Environment capacity reached',`at ${held} environment(s)`);
   record('no job was queued for the refused environment',after===held,`${after} held`);
   limitReached=true;
   break;
  }
  if(!record(`environment ${held+1} is accepted`,environment.status===202,`status ${environment.status}`))break;
  const {state,failure}=await waitProvisioned(call,environment.json.id);
  if(state==='failed'&&failure==='capacity_exceeded') {
   // The worker's memory, pressure and connection admission refused before the count guard:
   // on a smaller host that is the limit that binds, and it is the one worth measuring.
   record(`the worker's admission refuses environment ${held+1} before the count guard`,true,`capacity_exceeded at ${held} environment(s)`);
   await sample(`refused at environment ${held+1}`,held);
   limitReached=true;
   break;
  }
  if(!record(`environment ${held+1} is provisioned`,state==='succeeded',`state ${state}${failure?' '+failure:''}`))break;
  held++;
  await Bun.sleep(20_000);
  await sample(`after environment ${held}`,held);
 }
 record('the limit was reached',limitReached,`${held} environment(s)`);
} catch(error) {
 record('the check completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
