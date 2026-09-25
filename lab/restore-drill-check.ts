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
import {cpSync,existsSync,mkdirSync,readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {checkList,installationUrl,managementCaller,operatorFrom,option,signIn} from './check-kit';

const args=process.argv.slice(2);
const usage='usage: bun lab/restore-drill-check.ts <operator.json> <export dir> [--evidence PATH]';
const [operatorPath,exportDir]=args;
const operator=operatorFrom(operatorPath,usage);
if(!exportDir){console.error(usage);process.exit(2);}
const base=installationUrl();
const restored:Record<string,unknown>[]=[];
const {record,finish}=checkList('restore-drill',option(args,'--evidence','docs/evidence/restore-drill.json'),()=>({restored}));

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
  const relink=await sbarbase('relink',backup.runtime,backup.name,'--operator-file',operatorPath!);
  if(!record(`relink recreates ${label} with its original ids`,relink.code===0,relink.out.split('\n')[0]??''))continue;
  const deadline=Date.now()+10*60_000;
  while(Date.now()<deadline&&!(backup.runtime in published()))await Bun.sleep(3000);
  if(!record(`the worker provisions ${label} as ${backup.runtime}`,backup.runtime in published()))continue;
  const restore=await sbarbase('restore',backup.runtime,backup.name,'--yes');
  record(`the backup restores into ${label}`,restore.code===0,restore.code===0?'':restore.out.split('\n').slice(-2).join(' '));
  restored.push({runtime:backup.runtime,backup:backup.name,counts:backup.manifest.counts,relink:relink.code,restore:restore.code});
 }

 const login=await signIn(base,operator.email,operator.password);
 if(!record('the operator signs in on the new installation',!!login.token,login.error))await finish();
 const call=managementCaller(base,login.token!);
 for(const user of drill.users) {
  const backup=backups.find(b=>b.manifest.ownership.environment.id===user.environment);
  if(!record(`a backup exists for the drill user of ${user.label}`,!!backup))continue;
  const environment=backup!.manifest.ownership.environment.id as string;
  const connection=await call('GET',`/environments/${environment}/connection`);
  const issued=await call('POST',`/environments/${environment}/keys`);
  if(!record(`a new key is issued for ${user.label}`,connection.status===200&&issued.status===201,`status ${connection.status}/${issued.status}`))continue;
  const app=createClient(`${base}${connection.json.apiPath}`,issued.json.token,{auth:{persistSession:false,autoRefreshToken:false}});
  const session=await app.auth.signInWithPassword({email:user.email,password:user.password});
  record(`the user from the old installation signs in to ${user.label} with their old password`,!session.error&&!!session.data.session,session.error?.message??'');
  const revoked=await call('DELETE',`/environments/${environment}/keys/${issued.json.id}`);
  record(`the drill key is revoked in ${user.label}`,revoked.status===200,`status ${revoked.status}`);
 }
} catch(error) {
 record('the drill completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
