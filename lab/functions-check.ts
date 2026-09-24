// Edge Functions check: functions from a Supabase-style folder are deployed and called through the gateway.
//
// Usage: bun lab/functions-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one) and internet access for the functions' npm imports. It writes a small
// `supabase/functions` folder (with `_shared` and a config.toml that turns verify_jwt off
// for a webhook), sets a secret, deploys with lab/functions-deploy.ts, and then acts as an
// application: supabase-js `functions.invoke`, a function that uses supabase-js from npm with
// the service role against the environment's own Auth, REST and Storage, a keyless webhook,
// a redeploy, the Edge Functions log, a removal, and turning the runtime off.
// The operator password is read from the private file and never printed.
import {createClient} from '@supabase/supabase-js';
import {mkdirSync,mkdtempSync,readFileSync,rmSync,statSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/functions-checks.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/functions-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const project=mkdtempSync(join(tmpdir(),'functions-check-'));
function finish():never {
 rmSync(project,{recursive:true,force:true});
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'functions',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Edge Functions for one environment on a running installation: a Supabase-style '+
  'functions folder deployed with lab/functions-deploy.ts, then called through the gateway with supabase-js, including a function that '+
  'uses supabase-js from npm with the service role against the environment\'s own Auth, REST and Storage, a keyless webhook, a secret, '+
  'a redeploy, the log, a removal and turning the runtime off.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nfunctions check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}
function write(path:string,content:string){mkdirSync(join(project,path,'..'),{recursive:true});writeFileSync(join(project,path),content);}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body!==undefined?{'content-type':'application/json'}:{})},
   body:body===undefined?undefined:JSON.stringify(body)});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0];
 const projectRow=(await call('GET',`/organizations/${organization.id}/projects`)).json?.data?.[0];
 const environment=(await call('GET',`/projects/${projectRow.id}/environments`)).json?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))finish();
 const path=`/environments/${environment.id}/functions`;
 record('Edge Functions start off',(await call('GET',path)).json?.data?.state==='off');

 // A project folder as the Supabase CLI lays it out.
 write('supabase/functions/_shared/cors.ts',`export const corsHeaders = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type'};\n`);
 write('supabase/functions/hello/index.ts',`import {corsHeaders} from '../_shared/cors.ts';
Deno.serve(async (request) => {
  const body = request.method === 'POST' ? await request.json().catch(() => ({})) : {};
  return new Response(JSON.stringify({message: 'Hello ' + (body.name ?? 'world'), greeting: Deno.env.get('GREETING') ?? null,
    hasUrl: !!Deno.env.get('SUPABASE_URL'), version: 1}), {headers: {...corsHeaders, 'content-type': 'application/json'}});
});
`);
 write('supabase/functions/admin-stats/index.ts',`import {createClient} from 'npm:@supabase/supabase-js@2';
Deno.serve(async () => {
  const admin = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {auth: {persistSession: false}});
  const users = await admin.auth.admin.listUsers();
  const buckets = await admin.storage.listBuckets();
  const rest = await admin.from('function_check_missing').select('*');
  return Response.json({users: users.data?.users?.length ?? null, usersError: users.error?.message ?? null,
    buckets: buckets.data?.length ?? null, bucketsError: buckets.error?.message ?? null, restStatus: rest.status});
});
`);
 write('supabase/functions/stripe-webhook/index.ts',`Deno.serve(async (request) => Response.json({received: true, signature: request.headers.get('stripe-signature')}));\n`);
 write('supabase/config.toml',`[functions.stripe-webhook]\nverify_jwt = false\n`);

 const secret=await call('PUT',`/environments/${environment.id}/function-secrets`,{secrets:{GREETING:'from a secret'}});
 record('an owner sets a secret',secret.status===200&&secret.json?.data?.secrets?.includes('GREETING'),`status ${secret.status}`);
 const deploy=Bun.spawnSync(['bun','lab/functions-deploy.ts',base,environment.id,join(project,'supabase/functions')],
  {env:{...process.env,SBARBASE_EMAIL:operator.email,SBARBASE_PASSWORD:operator.password}});
 const deployed=deploy.stdout.toString();
 record('the deploy command sends every function in the folder',deploy.exitCode===0&&['hello','admin-stats','stripe-webhook'].every(name=>deployed.includes('deployed '+name)),
  (deployed+deploy.stderr.toString()).trim().replace(/\n/g,'; ').slice(0,300));
 let state='pending',failure='';const deadline=Date.now()+5*60_000;
 while(Date.now()<deadline){const current=(await call('GET',path)).json?.data;state=current?.state;failure=current?.failure??'';
  if(state!=='pending')break;await Bun.sleep(3000);}
 if(!record('the first deploy starts the environment\'s Edge Functions',state==='on',`state ${state} ${failure} after ${Math.round((Date.now()-started)/1000)} s`))finish();
 const listed=(await call('GET',path)).json?.data;
 record('the console lists the functions and the webhook without JWT checks',listed?.functions?.length===3&&
  listed.functions.find((item:any)=>item.name==='stripe-webhook')?.verify_jwt===false&&listed.functions.find((item:any)=>item.name==='hello')?.verify_jwt===true,
  listed?.functions?.map((item:any)=>`${item.name}:${item.verify_jwt}`).join(','));
 const connection=(await call('GET',`/environments/${environment.id}/connection`)).json;
 record('connection details list Edge Functions',connection?.services?.includes('functions'),connection?.services?.join(','));

 const key=(await call('POST',`/environments/${environment.id}/keys`)).json?.token as string;
 const url=`${base}${connection.apiPath}`;
 const client=createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false}});
 const hello=await client.functions.invoke('hello',{body:{name:'Sbarbase'}});
 record('supabase-js invokes a function through the gateway',hello.data?.message==='Hello Sbarbase',JSON.stringify(hello.data??hello.error?.message));
 record('the function imports from _shared and reads its secret',hello.data?.greeting==='from a secret'&&hello.data?.hasUrl===true);
 const stats=await client.functions.invoke('admin-stats',{method:'GET'});
 record('a function uses supabase-js from npm with the service role on the environment\'s own Auth',typeof stats.data?.users==='number'&&stats.data.users>=1,
  JSON.stringify(stats.data??stats.error?.message));
 record('the same function reaches the environment\'s own Storage and REST',typeof stats.data?.buckets==='number'&&stats.data?.restStatus===404,
  `buckets ${stats.data?.buckets} ${stats.data?.bucketsError??''}, REST ${stats.data?.restStatus}`);

 const direct=(name:string,init:RequestInit={})=>fetch(`${url}/functions/v1/${name}`,{method:'POST',...init});
 const keyless=await direct('hello');
 record('a function that checks JWTs refuses a call without a key or token',keyless.status===401,`status ${keyless.status}`);
 const webhook=await direct('stripe-webhook',{headers:{'stripe-signature':'t=1,v1=abc','content-type':'application/json'},body:'{}'});
 const hook=await webhook.json().catch(()=>null) as any;
 record('a webhook without a key reaches a function that does not check JWTs, with its own headers',webhook.status===200&&hook?.signature==='t=1,v1=abc',`status ${webhook.status}`);
 record('a wrong key is refused',(await direct('hello',{headers:{apikey:'sb_publishable_wrong'}})).status===401);
 record('the runtime\'s internal route is not reachable',(await direct('_sb/rest/v1/',{headers:{apikey:key}})).status===404);
 record('an unknown function answers 404',(await direct('missing',{headers:{apikey:key}})).status===404);
 const preflight=await fetch(`${url}/functions/v1/hello`,{method:'OPTIONS',headers:{origin:'https://app.example.com','access-control-request-method':'POST'}});
 record('a browser preflight is answered',preflight.status===204&&preflight.headers.get('access-control-allow-origin')==='*',`status ${preflight.status}`);

 write('supabase/functions/hello/index.ts',`Deno.serve(() => Response.json({message: 'Hello again', version: 2}));\n`);
 const again=Bun.spawnSync(['bun','lab/functions-deploy.ts',base,environment.id,join(project,'supabase/functions'),'hello'],
  {env:{...process.env,SBARBASE_EMAIL:operator.email,SBARBASE_PASSWORD:operator.password}});
 const second=await client.functions.invoke('hello',{body:{}});
 record('a redeploy takes effect without a restart',again.exitCode===0&&second.data?.version===2,JSON.stringify(second.data??second.error?.message));
 const logs=await call('GET',`/environments/${environment.id}/logs?source=functions&lines=200`);
 record('the Edge Functions log answers',logs.status===200&&Array.isArray(logs.json?.data?.lines),`status ${logs.status}, ${logs.json?.data?.lines?.length} line(s)`);
 const removed=await call('DELETE',`${path}/admin-stats`);
 const gone=await direct('admin-stats',{headers:{apikey:key}});
 record('a removed function answers 404',removed.status===200&&gone.status===404,`status ${gone.status}`);
 if(checks.some(row=>!row.ok)){
  const output=Bun.spawnSync(['docker','logs','--tail','80',`sbarbase-durable-${connection.apiPath.slice(1)}-functions`]);
  console.log(`== functions log\n${output.stdout.toString()}${output.stderr.toString()}`);
 }

 const off=await call('PUT',path,{enabled:false});
 record('the owner turns Edge Functions off',off.status===202,`status ${off.status}`);
 const offUntil=Date.now()+3*60_000;
 while(Date.now()<offUntil){state=(await call('GET',path)).json?.data?.state;if(state!=='pending')break;await Bun.sleep(2000);}
 record('Edge Functions are off again',state==='off',`state ${state}`);
 record('a call answers 404 once they are off',(await direct('hello',{headers:{apikey:key}})).status===404);
} catch(error) {
 record('functions check ran to the end',false,(error as Error).message);
}
finish();
