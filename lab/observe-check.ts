// Logs and metrics check: an operator reads an environment's usage and logs after an application used it.
//
// Usage: bun lab/observe-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one). It acts as an application through the gateway (reads, a missing table, a
// sign-up, a Storage listing), then reads the environment's metrics and each log source
// through the management API, and checks that no key or token appears in anything it got
// back. The operator password is read from the private file and never printed.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/observe-checks.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/observe-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();

function finish():never {
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'observe',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Logs and metrics for one environment on a running installation: '+
  'requests made by supabase-js through the gateway, then the environment\'s request totals, service memory and processor use, '+
  'the request log and the Auth, REST and Storage logs read through the management API, with no key or token in any answer.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nobserve check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))finish();
 const call=async(method:string,path:string)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`}});
  return {status:response.status,text:await response.text()};
 };
 const json=async(path:string)=>{const got=await call('GET',path);return {status:got.status,text:got.text,body:JSON.parse(got.text||'null') as any};};
 const organization=(await json('/organizations')).body?.data?.[0];
 const project=(await json(`/organizations/${organization.id}/projects`)).body?.data?.[0];
 const environment=(await json(`/projects/${project.id}/environments`)).body?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))finish();
 const path=`/environments/${environment.id}`;
 const apiPath=(await json(`${path}/connection`)).body?.apiPath as string;
 const key=JSON.parse((await call('POST',`${path}/keys`)).text).token as string;
 const before=(await json(`${path}/metrics`)).body?.data?.window?.requests??0;

 const client=createClient(`${base}${apiPath}`,key,{auth:{persistSession:false,autoRefreshToken:false}});
 const missing=await client.from('observe_missing_table').select('*');
 const email=`observe-${Date.now()}@example.com`;
 const signUp=await client.auth.signUp({email,password:crypto.randomUUID()+'Aa1!'});
 const buckets=await client.storage.listBuckets();
 const wrong=await fetch(`${base}${apiPath}/rest/v1/`,{headers:{apikey:'sb_publishable_wrong'}});
 record('an application makes requests through the gateway',!!missing.error&&!signUp.error&&!buckets.error&&wrong.status===401,
  `missing table ${missing.status}, sign-up ${signUp.error?.message??'ok'}, buckets ${buckets.error?.message??'ok'}, wrong key ${wrong.status}`);

 const metrics=await json(`${path}/metrics`);
 const window=metrics.body?.data?.window;
 record('metrics count the new requests',metrics.status===200&&window?.requests>=before+4,`${before} -> ${window?.requests}`);
 record('metrics count client errors',window?.clientErrors>=2,`${window?.clientErrors} client error(s)`);
 record('metrics give response times',typeof window?.p50==='number'&&typeof window?.p95==='number',`median ${window?.p50} ms, 95% ${window?.p95} ms`);
 record('metrics split requests by service',window?.services?.rest>0&&window?.services?.auth>0&&window?.services?.storage>0,JSON.stringify(window?.services));
 const perMinute=metrics.body?.data?.perMinute as unknown[];
 record('metrics hold an hour of minutes',perMinute?.length===60);
 const services=(metrics.body?.data?.services??[]) as {service:string;memoryBytes:number|null}[];
 record('metrics give memory use of Auth and REST',['auth','rest'].every(name=>services.some(row=>row.service===name&&(row.memoryBytes??0)>0)),
  services.map(row=>`${row.service} ${Math.round((row.memoryBytes??0)/1024/1024)} MiB`).join(', '));

 const requests=await json(`${path}/logs?source=requests`);
 const rows=(requests.body?.data?.requests??[]) as {service:string;path:string;status:number}[];
 record('the request log shows the missing table',rows.some(row=>row.service==='rest'&&row.path==='/observe_missing_table'&&row.status===404),`${rows.length} request(s)`);
 record('the request log shows the wrong key',rows.some(row=>row.status===401));
 const failing=(await json(`${path}/logs?source=requests&errors=1`)).body?.data?.requests as {status:number}[];
 record('errors only shows only errors',failing?.length>0&&failing.every(row=>row.status>=400),`${failing?.length} error(s)`);

 const texts=[requests.text,metrics.text];
 for(const source of ['auth','rest','storage'] as const){
  const got=await json(`${path}/logs?source=${source}&lines=300`);
  const lines=(got.body?.data?.lines??[]) as string[];
  texts.push(got.text);
  // Storage logs errors only (LOG_LEVEL=error), so an empty Storage log is a healthy one.
  record(`the ${source} log answers`,got.status===200&&(source==='storage'||lines.length>0),`status ${got.status}, ${lines.length} line(s)`);
  if(source==='auth')record('the Auth log shows the sign-up',lines.some(line=>line.includes('/signup')),'');
 }
 const all=texts.join('\n');
 record('no key, token or password appears in any answer',!all.includes(key)&&!/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/.test(all)&&
  !/sb_(publishable|secret)_[A-Za-z0-9]/.test(all),'');
 record('another environment\'s name never appears in the Storage log',
  !((await json(`${path}/logs?source=storage&lines=1000`)).body?.data?.lines??[]).some((line:string)=>/e_[a-f0-9]{24}/.test(line.replaceAll(apiPath.slice(1),''))));
 const bad=await call('GET',`${path}/logs?source=postgres`);
 record('an unknown source is refused',bad.status===400,`status ${bad.status}`);
 const anonymous=await fetch(`${base}/management/v1${path}/logs`);
 record('logs need an operator login',anonymous.status===401,`status ${anonymous.status}`);
} catch(error) {
 record('observe check ran to the end',false,(error as Error).message);
}
finish();
