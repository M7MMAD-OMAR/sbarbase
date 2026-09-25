// Restore drill: backups from another installation restored onto this one.
//
// Usage: bun lab/restore-drill-check.ts <operator.json> <export dir> [--evidence PATH]
//
// The export directory holds what lab/vm-milestones.sh export copied off the first
// installation: backups/<runtime>/<backup>/ and drill-users.json, the application users
// that signed up there before the backup. For each backup this runs the documented
// operator path on a fresh installation (copy under .lab/backups/, `sbarbase relink`,
// wait for the worker, `sbarbase restore --yes`), then issues a new publishable key and
// signs each drill user in through the gateway with the password they chose on the old
// installation. The users' sessions and keys from the old installation are expected to
// be gone; their accounts and passwords must not be.
import {createClient} from '@supabase/supabase-js';
import {cpSync,existsSync,mkdirSync,readFileSync,readdirSync,statSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const [operatorPath,exportDir]=args;
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/restore-drill.json';
if(!operatorPath||!exportDir||!evidencePath){console.error('usage: bun lab/restore-drill-check.ts <operator.json> <export dir> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const restored:Record<string,unknown>[]=[];

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath,JSON.stringify({check:'restore-drill',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),restored,checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nrestore drill: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}
async function sbarbase(...command:string[]){
 const child=Bun.spawn(['deploy/sbarbase',...command],{stdout:'pipe',stderr:'pipe'});
 const [out,err]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text()]);
 return {code:await child.exited,out:(out+err).trim()};
}
const published=()=>existsSync('.lab/upstream/endpoints.json')?JSON.parse(readFileSync('.lab/upstream/endpoints.json','utf8')) as Record<string,unknown>:{};

try {
 const drill=JSON.parse(readFileSync(join(exportDir,'drill-users.json'),'utf8')) as {users:{environment:string;label:string;email:string;password:string}[]};
 const backups:{runtime:string;name:string;manifest:any}[]=[];
 for(const runtime of readdirSync(join(exportDir,'backups')).filter(name=>/^e_[a-f0-9]{24}$/.test(name)))
  for(const name of readdirSync(join(exportDir,'backups',runtime)).filter(name=>/^\d{8}T\d{6}Z$/.test(name)))
   backups.push({runtime,name,manifest:JSON.parse(readFileSync(join(exportDir,'backups',runtime,name,'manifest.json'),'utf8'))});
 if(!record('the export holds backups with their ownership',backups.length>0&&backups.every(b=>!!b.manifest.ownership),`${backups.length} backup(s)`))await finish();

 for(const backup of backups) {
  const label=`${backup.manifest.ownership.project.name}/${backup.manifest.ownership.environment.name}`;
  const target=join('.lab','backups',backup.runtime);
  mkdirSync(target,{recursive:true,mode:0o700});
  cpSync(join(exportDir,'backups',backup.runtime,backup.name),join(target,backup.name),{recursive:true});
  const relink=await sbarbase('relink',backup.runtime,backup.name,'--operator-file',operatorPath);
  if(!record(`relink recreates ${label} with its original ids`,relink.code===0,relink.out.split('\n')[0]??''))continue;
  const deadline=Date.now()+10*60_000;
  while(Date.now()<deadline&&!(backup.runtime in published()))await Bun.sleep(3000);
  if(!record(`the worker provisions ${label} as ${backup.runtime}`,backup.runtime in published()))continue;
  const restore=await sbarbase('restore',backup.runtime,backup.name,'--yes');
  record(`the backup restores into ${label}`,restore.code===0,restore.code===0?'':restore.out.split('\n').slice(-2).join(' '));
  restored.push({runtime:backup.runtime,backup:backup.name,counts:backup.manifest.counts,relink:relink.code,restore:restore.code});
 }

 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('the operator signs in on the new installation',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`}});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 for(const user of drill.users) {
  const backup=backups.find(b=>b.manifest.ownership.environment.id===user.environment);
  if(!record(`a backup exists for the drill user of ${user.label}`,!!backup))continue;
  const environment=backup!.manifest.ownership.environment.id as string;
  const connection=await call('GET',`/environments/${environment}/connection`);
  const issued=await call('POST',`/environments/${environment}/keys`);
  if(!record(`a new key is issued for ${user.label}`,connection.status===200&&issued.status===201,`status ${connection.status}/${issued.status}`))continue;
  const app=createClient(`${base}${connection.json.apiPath}`,issued.json.token,{auth:{persistSession:false,autoRefreshToken:false}});
  const signIn=await app.auth.signInWithPassword({email:user.email,password:user.password});
  record(`the user from the old installation signs in to ${user.label} with their old password`,!signIn.error&&!!signIn.data.session,signIn.error?.message??'');
  const revoked=await call('DELETE',`/environments/${environment}/keys/${issued.json.id}`);
  record(`the drill key is revoked in ${user.label}`,revoked.status===200,`status ${revoked.status}`);
 }
} catch(error) {
 record('the drill completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
