// One environment with a tiny table, for the live upgrade measurement (lab/live_upgrade_measure.py).
//
// Usage: bun live-upgrade-setup.ts <operator.json> <out.json>
//
// Runs inside the control-plane container, against the console at .lab/upstream/server.json. It signs in
// as the operator the way lab/first-project-check.ts does, creates a project (its production environment
// is queued with it), waits for provisioning, issues a publishable key, opens the environment's direct
// database access, creates `public.live_measure_items` with one row through a PostgreSQL client and
// grants anon the read, then reads the row back through the gateway with the key. The result
// {apiPath, key, table, environment, project} goes to <out.json> (mode 600) and never to the log: the key
// is a throwaway one of this run, and the log of a public repository is public.
// SBARBASE_ROOT names the checkout, whose lab/live-management-auth.ts does the sign-in. The file is
// copied to .lab/measure/ by the driver, so the import is by that variable and not by a relative path.
import {SQL} from 'bun';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const root=process.env.SBARBASE_ROOT;
if(!root){console.error('SBARBASE_ROOT is not set');process.exit(2);}
const {liveManagementClient,liveManagementLogin}:any=await import(`${root}/lab/live-management-auth`);
const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const [operatorPath,outPath]=process.argv.slice(2);
if(!operatorPath||!outPath){console.error('usage: bun live-upgrade-setup.ts <operator.json> <out.json>');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url.replace(/\/$/,'');
const TABLE='live_measure_items';

function fail(message:string):never{console.error('FAIL: '+message);process.exit(1);}
const management=liveManagementClient(base,MANAGEMENT_KEY);
const login=await liveManagementLogin(management,base,{email:operator.email,password:operator.password});
const token=login.data.session?.access_token;
if(!token)fail('the operator could not sign in: '+(login.error?.message??''));
const call=async(method:string,path:string,body?:unknown)=>{
 const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body!==undefined?{'content-type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body)});
 return {status:response.status,json:await response.json().catch(()=>null) as any};
};
const organization=(await call('GET','/organizations')).json?.data?.[0];
if(!organization?.id)fail('no organization');
const project=await call('POST',`/organizations/${organization.id}/projects`,{name:`Live upgrade measure ${new Date().toISOString().slice(0,19)}`});
if(project.status!==201||!project.json?.environment)fail(`the project was not created (status ${project.status})`);
const environment=project.json.environment as string;
let state='queued',failure='';
for(const deadline=Date.now()+10*60_000;Date.now()<deadline;){
 const provision=await call('GET',`/environments/${environment}/provision`);
 state=provision.json?.state??`http ${provision.status}`;failure=provision.json?.failure??'';
 if(['succeeded','failed','cancelled'].includes(state))break;
 await Bun.sleep(3000);
}
if(state!=='succeeded')fail(`provisioning ended as ${state} ${failure}`);
const connection=await call('GET',`/environments/${environment}/connection`);
const apiPath=connection.json?.apiPath as string|undefined;
if(!apiPath?.startsWith('/'))fail(`no gateway path (status ${connection.status})`);
const issued=await call('POST',`/environments/${environment}/keys`);
const key=issued.json?.token as string|undefined;
if(issued.status!==201||!key)fail(`no key (status ${issued.status})`);

const path=`/environments/${environment}/database`;
const initial=(await call('GET',path)).json?.data;
const on=await call('PUT',path,{enabled:true});
const password=on.json?.data?.password as string;
if(on.status!==202||typeof password!=='string')fail(`direct database access was not turned on (status ${on.status})`);
let dbState='pending';
for(const deadline=Date.now()+3*60_000;Date.now()<deadline;){
 dbState=(await call('GET',path)).json?.data?.state;
 if(dbState!=='pending')break;
 await Bun.sleep(2000);
}
if(dbState!=='on')fail(`direct database access ended as ${dbState}`);
const url=(initial.url as string).replace('[YOUR-PASSWORD]',encodeURIComponent(password));
const sql=new SQL(url+(url.includes('?')?'&':'?')+'sslmode=disable',{max:1,idleTimeout:1,connectionTimeout:10});
try{
 await sql.unsafe(`create table if not exists public.${TABLE}(id int primary key, note text);
insert into public.${TABLE}(id,note) values (1,'pulse') on conflict do nothing;
grant select on public.${TABLE} to anon, authenticated;
notify pgrst, 'reload schema';`);
}catch(error){fail('the table could not be created: '+(error as Error).message);}
finally{await sql.close().catch(()=>{});}

let read=0,rows:any=null;
for(const deadline=Date.now()+60_000;Date.now()<deadline;){
 const response=await fetch(`${base}${apiPath}/rest/v1/${TABLE}?select=*&limit=1`,{headers:{apikey:key}});
 read=response.status;rows=await response.json().catch(()=>null);
 if(read===200&&Array.isArray(rows)&&rows.length===1)break;
 await Bun.sleep(1000);
}
if(read!==200||!Array.isArray(rows)||rows.length!==1)fail(`the gateway read answered ${read}`);
writeFileSync(outPath,JSON.stringify({apiPath,key,table:TABLE,environment,project:project.json.id})+'\n',{mode:0o600});
console.log(`environment ${environment} ready, ${TABLE} read through the gateway (status ${read})`);
