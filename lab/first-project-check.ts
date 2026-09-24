// First project check: what a new operator does after installing, end to end.
//
// Usage: bun lab/first-project-check.ts <operator.json> [--evidence PATH]
//
// Against the running installation (the URL in .lab/upstream/server.json), it
// logs in as the operator from the 0600 bootstrap file, creates a project and a
// production environment through the management API, waits for the worker to
// provision it, issues a publishable key and uses supabase-js through the
// gateway: Auth settings, a sign-up, the REST schema, Storage and a browser's
// cross-origin preflight. It then revokes
// the key and requires the gateway to refuse it. The password is read from the
// file and never printed or written; evidence holds identifiers and results only.
// The project and environment are kept, as a first project would be.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/first-project-check.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/first-project-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const ids:Record<string,string>={};

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath,JSON.stringify({check:'first-project',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),ids,checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nfirst project check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in through the management Auth realm',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body?{'content-type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };

 const organizations=await call('GET','/organizations');
 const organization=organizations.json?.data?.[0];
 if(!record('the operator sees the client created at install',organizations.status===200&&!!organization?.id,`status ${organizations.status}`))await finish();
 ids.organization=organization.id;

 const project=await call('POST',`/organizations/${organization.id}/projects`,{name:`First project ${new Date().toISOString().slice(0,19)}`});
 if(!record('a project is created',project.status===201&&!!project.json?.id,`status ${project.status}`))await finish();
 ids.project=project.json.id;

 const environment=await call('POST',`/projects/${ids.project}/environments`,{name:'production'});
 if(!record('an environment is queued for provisioning',environment.status===202&&!!environment.json?.id,`status ${environment.status}`))await finish();
 ids.environment=environment.json.id;

 let state='queued',failure='';
 const deadline=Date.now()+10*60_000;
 while(Date.now()<deadline){
  const provision=await call('GET',`/environments/${ids.environment}/provision`);
  state=provision.json?.state??`http ${provision.status}`;failure=provision.json?.failure??'';
  if(['succeeded','failed','cancelled'].includes(state))break;
  await Bun.sleep(3000);
 }
 if(!record('the worker provisions the environment',state==='succeeded',`state ${state}${failure?' failure '+failure:''} after ${Math.round((Date.now()-started)/1000)} s`))await finish();

 const connection=await call('GET',`/environments/${ids.environment}/connection`);
 const apiPath=connection.json?.apiPath as string|undefined;
 if(!record('connection details name the gateway path',connection.status===200&&!!apiPath?.startsWith('/'),`status ${connection.status}`))await finish();
 ids.apiPath=apiPath!;

 const issued=await call('POST',`/environments/${ids.environment}/keys`);
 const key=issued.json?.token as string|undefined;
 if(!record('a publishable key is issued once',issued.status===201&&!!key,`status ${issued.status}`))await finish();
 ids.keyId=issued.json?.id??'';

 const url=`${base}${apiPath}`;
 const settings=await fetch(`${url}/auth/v1/settings`,{headers:{apikey:key!}});
 record('Auth answers through the gateway',settings.status===200,`status ${settings.status}`);
 const app=createClient(url,key!,{auth:{persistSession:false,autoRefreshToken:false}});
 const email=`first-user-${crypto.randomUUID().slice(0,8)}@example.com`;
 const signUp=await app.auth.signUp({email,password:crypto.randomUUID()});
 record('an application user can sign up with supabase-js',!signUp.error&&!!signUp.data.user,signUp.error?.message??'');
 const schema=await fetch(`${url}/rest/v1/`,{headers:{apikey:key!}});
 record('REST answers through the gateway',schema.status===200,`status ${schema.status}`);
 const buckets=await app.storage.listBuckets();
 record('Storage answers through the gateway with supabase-js',!buckets.error,buckets.error?.message??'');
 // What a browser on the application's own domain sends before and with a call.
 const origin='https://app.example.com';
 const preflight=await fetch(`${url}/rest/v1/`,{method:'OPTIONS',headers:{origin,'access-control-request-method':'GET',
  'access-control-request-headers':'apikey, authorization, x-client-info'}});
 record('a browser on another domain is allowed to call the API',preflight.status===204&&preflight.headers.get('access-control-allow-origin')==='*'&&
  (preflight.headers.get('access-control-allow-headers')??'').includes('apikey'),`status ${preflight.status}`);
 const browser=await fetch(`${url}/rest/v1/`,{headers:{apikey:key!,origin}});
 record('the browser can read the answer',browser.status===200&&browser.headers.get('access-control-allow-origin')==='*',`status ${browser.status}`);

 const revoked=await call('DELETE',`/environments/${ids.environment}/keys/${ids.keyId}`);
 record('the key is revoked',revoked.status===200,`status ${revoked.status}`);
 const refused=await fetch(`${url}/rest/v1/`,{headers:{apikey:key!}});
 record('the gateway refuses the revoked key',refused.status===401,`status ${refused.status}`);
} catch(error) {
 record('the check completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
