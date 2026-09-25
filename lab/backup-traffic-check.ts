// Backup under traffic: every environment keeps serving while `backup.py create all` runs.
//
// Usage: bun lab/backup-traffic-check.ts <operator.json> [--evidence PATH] [--drill PATH]
//
// Against the running installation, the operator from the 0600 bootstrap file makes sure
// two environments are provisioned (creating a project for the second when needed),
// issues a publishable key for each and signs up one known application user per
// environment. Then supabase-js clients call REST, Auth and Storage in each environment
// continuously and sign up new users, while the daily backup command runs for every
// environment. Any failed request fails the check. The known users are written to the
// private drill file (mode 600), so a restore on another installation can prove they came
// back. The keys are revoked at the end.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const option=(name:string,fallback:string)=>{const at=args.indexOf(name);return at>=0?args[at+1]:fallback;};
const evidencePath=option('--evidence','docs/evidence/backup-traffic-check.json');
const drillPath=option('--drill','.lab/drill-users.json');
if(!operatorPath||!evidencePath||!drillPath){console.error('usage: bun lab/backup-traffic-check.ts <operator.json> [--evidence PATH] [--drill PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const traffic:Record<string,{requests:number;failures:number;slowest_ms:number;signups:number;errors:string[]}>={};
let backupRun={exit:-1,seconds:0,lines:[] as string[]};

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath,JSON.stringify({check:'backup-under-traffic',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),traffic,backup:{exit:backupRun.exit,seconds:backupRun.seconds,lines:backupRun.lines},checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nbackup under traffic check: ${passed?'passed':'failed'}`);
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
 const provisioned=async()=>{
  const found:{id:string;project:string;name:string}[]=[];
  for(const organization of (await call('GET','/organizations')).json?.data??[])
   for(const project of (await call('GET',`/organizations/${organization.id}/projects`)).json?.data??[])
    for(const environment of (await call('GET',`/projects/${project.id}/environments`)).json?.data??[]) {
     const state=(await call('GET',`/environments/${environment.id}/provision`)).json?.state;
     if(state==='succeeded')found.push({id:environment.id,project:project.name,name:environment.name});
    }
  return found;
 };
 let environments=await provisioned();
 if(environments.length<2) {
  const organization=(await call('GET','/organizations')).json?.data?.[0]?.id;
  const project=await call('POST',`/organizations/${organization}/projects`,{name:`Traffic ${new Date().toISOString().slice(0,19)}`});
  const created=await call('POST',`/projects/${project.json?.id}/environments`,{name:'production'});
  let state='queued';const deadline=Date.now()+10*60_000;
  while(Date.now()<deadline&&!['succeeded','failed','cancelled'].includes(state)){await Bun.sleep(3000);state=(await call('GET',`/environments/${created.json?.id}/provision`)).json?.state??'unknown';}
  record('a second environment is provisioned for the check',state==='succeeded',`state ${state}`);
  environments=await provisioned();
 }
 if(!record('two environments are provisioned',environments.length>=2,`${environments.length} found`))await finish();
 const chosen=environments.slice(0,2);

 const clients:{id:string;label:string;url:string;key:string;keyId:string}[]=[];
 const drill:{environment:string;label:string;email:string;password:string}[]=[];
 for(const environment of chosen) {
  const connection=await call('GET',`/environments/${environment.id}/connection`);
  const issued=await call('POST',`/environments/${environment.id}/keys`);
  const label=`${environment.project}/${environment.name}`;
  if(!record(`a key is issued for ${label}`,connection.status===200&&issued.status===201,`status ${connection.status}/${issued.status}`))await finish();
  const url=`${base}${connection.json.apiPath}`;
  clients.push({id:environment.id,label,url,key:issued.json.token,keyId:issued.json.id});
  const app=createClient(url,issued.json.token,{auth:{persistSession:false,autoRefreshToken:false}});
  const email=`drill-${crypto.randomUUID().slice(0,8)}@example.com`,password=crypto.randomUUID();
  const signUp=await app.auth.signUp({email,password});
  record(`a known user signs up in ${label}`,!signUp.error&&!!signUp.data.user,signUp.error?.message??'');
  drill.push({environment:environment.id,label,email,password});
 }
 writeFileSync(drillPath,JSON.stringify({recorded:new Date().toISOString(),users:drill},null,2)+'\n',{mode:0o600});

 let running=true;
 const worker=async(client:typeof clients[number])=>{
  const stats={requests:0,failures:0,slowest_ms:0,signups:0,errors:[] as string[]};traffic[client.label]=stats;
  const app=createClient(client.url,client.key,{auth:{persistSession:false,autoRefreshToken:false}});
  const fail=(what:string)=>{stats.failures++;if(stats.errors.length<5)stats.errors.push(what);};
  let round=0;
  while(running) {
   const began=Date.now();
   try {
    stats.requests+=3;
    const rest=await fetch(`${client.url}/rest/v1/`,{headers:{apikey:client.key}});
    if(rest.status!==200)fail(`rest ${rest.status}`);
    const settings=await fetch(`${client.url}/auth/v1/settings`,{headers:{apikey:client.key}});
    if(settings.status!==200)fail(`auth ${settings.status}`);
    const buckets=await app.storage.listBuckets();
    if(buckets.error)fail(`storage ${buckets.error.message}`);
    // A few sign-ups only: Auth limits how many confirmation mails an hour it sends.
    if(round%10===0&&stats.signups<3) {
     stats.requests++;
     const signUp=await app.auth.signUp({email:`traffic-${crypto.randomUUID().slice(0,8)}@example.com`,password:crypto.randomUUID()});
     if(signUp.error)fail(`signup ${signUp.error.message}`);else stats.signups++;
    }
   } catch(error){fail(error instanceof Error?error.message:String(error));}
   round++;
   stats.slowest_ms=Math.max(stats.slowest_ms,Date.now()-began);
  }
 };
 const workers=clients.map(worker);
 await Bun.sleep(5000);
 const backupStarted=Date.now();
 const child=Bun.spawn(['/usr/bin/python3','lab/backup.py','create','all','--local-only'],{stdout:'pipe',stderr:'pipe'});
 const [out,err]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text()]);
 backupRun={exit:await child.exited,seconds:Math.round((Date.now()-backupStarted)/1000),
  lines:(out+err).split('\n').filter(line=>/^(backup|installation)/.test(line)).map(line=>line.replace(/[\w.+-]+@[\w.-]+/g,'<email>'))};
 await Bun.sleep(5000);
 running=false;
 await Promise.all(workers);
 record('the backup of every environment completes while they serve',backupRun.exit===0,`exit ${backupRun.exit} after ${backupRun.seconds} s`);
 for(const client of clients) {
  const stats=traffic[client.label]!;
  record(`no request failed in ${client.label} during the backup`,stats.requests>0&&stats.failures===0,
   `${stats.requests} requests, ${stats.signups} sign-ups, ${stats.failures} failed, slowest round ${stats.slowest_ms} ms`);
 }
 for(const client of clients) {
  const revoked=await call('DELETE',`/environments/${client.id}/keys/${client.keyId}`);
  record(`the check's key is revoked in ${client.label}`,revoked.status===200,`status ${revoked.status}`);
 }
} catch(error) {
 record('the check completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
