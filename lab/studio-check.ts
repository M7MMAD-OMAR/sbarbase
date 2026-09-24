// Studio check: an operator opens Supabase Studio for an environment, end to end.
//
// Usage: bun lab/studio-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one). Starts Studio through the management API, enters it with the console's
// ticket at <id>.studio.localhost, then uses Studio's own API the way its pages do: the
// table list and SQL through postgres-meta, users and buckets through Studio's server-side
// calls to Auth and Storage. Checks that a request without the session, or with a session
// for another environment, never reaches Studio, then stops it. The operator password is
// read from the private file and never printed.
import {createClient} from '@supabase/supabase-js';
import {request as httpRequest} from 'node:http';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/studio-check.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/studio-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;
const port=new URL(base).port;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'studio',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Supabase Studio for one environment on a running installation: started on demand '+
  'through the management API, entered with the console ticket on its own origin, and used through Studio\'s own API for tables, '+
  'SQL, users and buckets; refused without the session or with another environment\'s session; stopped.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nstudio check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

/** A request to the console's listener with a chosen Host, as a browser on the server sends it. */
function studio(host:string,path:string,options:{method?:string;cookie?:string;body?:unknown}={}):Promise<{status:number;headers:Record<string,string|string[]|undefined>;text:string}> {
 return new Promise((resolve,reject)=>{
  const body=options.body===undefined?undefined:JSON.stringify(options.body);
  const request=httpRequest({host:'127.0.0.1',port:Number(port),path,method:options.method??'GET',
   headers:{host:`${host}:${port}`,...(options.cookie?{cookie:options.cookie}:{}),...(body?{'content-type':'application/json','content-length':Buffer.byteLength(body)}:{})}},
   response=>{let text='';response.setEncoding('utf8');response.on('data',part=>text+=part);
    response.on('end',()=>resolve({status:response.statusCode??0,headers:response.headers,text}));});
  request.on('error',reject);request.setTimeout(60_000,()=>request.destroy(new Error('timeout')));
  if(body)request.write(body);request.end();
 });
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`}});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0];
 const project=(await call('GET',`/organizations/${organization.id}/projects`)).json?.data?.[0];
 const environment=(await call('GET',`/projects/${project.id}/environments`)).json?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))await finish();
 const path=`/environments/${environment.id}/studio`;

 record('Studio is stopped until someone asks for it',(await call('GET',path)).json?.data?.state==='stopped');
 record('opening before it runs is refused',(await call('POST',path+'/session')).status===409);
 const start=await call('POST',path);
 record('an owner starts Studio',start.status===202,`status ${start.status}`);
 let state='',failure='';const deadline=Date.now()+5*60_000;
 while(Date.now()<deadline){
  const current=(await call('GET',path)).json?.data;state=current?.state;failure=current?.failure??'';
  if(state==='running'||(state==='failed'&&failure))break;
  await Bun.sleep(2000);
 }
 if(!record('the supervisor starts Studio and postgres-meta',state==='running',`state ${state}${failure?' '+failure:''}`))await finish();

 const session=await call('POST',path+'/session');
 if(!record('the console issues a one-minute ticket',session.status===201&&!!session.json?.host,`status ${session.status}`))await finish();
 const host=session.json.host as string;
 record('the environment has its own Studio origin',/^[a-f0-9]{24}\.studio\.localhost$/.test(host),host);
 const unsigned=await studio(host,'/project/default');
 record('Studio refuses a browser without the session',unsigned.status===401,`status ${unsigned.status}`);
 const forged=await studio(host,'/project/default',{cookie:'sbarbase_studio=forged.value'});
 record('Studio refuses a forged session',forged.status===401,`status ${forged.status}`);
 const enter=await studio(host,session.json.path);
 const setCookie=[enter.headers['set-cookie']].flat().find(value=>typeof value==='string'&&value.startsWith('sbarbase_studio='));
 record('the ticket opens a session for this origin only',enter.status===302&&!!setCookie&&setCookie.includes('HttpOnly'),`status ${enter.status}`);
 const cookie=(setCookie??'').split(';')[0]!;

 const profile=await studio(host,'/api/platform/profile',{cookie});
 record('Studio answers behind the session',profile.status===200,`status ${profile.status}`);
 const page=await studio(host,'/project/default',{cookie});
 record('the project page loads',page.status===200&&page.text.includes('<'),`status ${page.status}`);
 const tables=await studio(host,'/api/platform/pg-meta/default/tables?included_schemas=public',{cookie});
 record('the table list reads the environment database',tables.status===200&&tables.text.startsWith('['),`status ${tables.status}${tables.status===200?'':' '+tables.text.slice(0,300)}`);
 const users=await studio(host,'/api/platform/pg-meta/default/query',{method:'POST',cookie,body:{query:'select count(*)::int as n from auth.users'}});
 const count=users.status===200?(JSON.parse(users.text)[0]?.n??-1):-1;
 record('the SQL editor sees the environment users despite row security',count>=1,`${count} user(s)${users.status===200?'':' '+users.text.slice(0,300)}`);
 const created=await studio(host,'/api/platform/pg-meta/default/query',{method:'POST',cookie,body:{query:'create table if not exists public.studio_probe(id bigint generated always as identity primary key, note text); insert into public.studio_probe(note) values (\'from studio\') returning note'}});
 record('the SQL editor creates a table and a row',created.status===200&&created.text.includes('from studio'),`status ${created.status}${created.status===200?'':' '+created.text.slice(0,300)}`);
 if(tables.status!==200||created.status!==200){
  // What postgres-meta itself said, so a failure names its cause. It never logs the password.
  for(const name of [`sbarbase-durable-e_${host.split(".")[0]}-meta`,`sbarbase-durable-e_${host.split(".")[0]}-studio`]){
   const logs=Bun.spawnSync(['docker','logs','--tail','30',name]);
   console.log(`== ${name}\n${logs.stdout.toString()}${logs.stderr.toString()}`);
  }
 }
 const email=`studio-${crypto.randomUUID().slice(0,8)}@example.com`;
 const user=await studio(host,'/api/platform/auth/default/users',{method:'POST',cookie,body:{email,password:crypto.randomUUID(),email_confirm:true}});
 record('Studio creates a user through the environment Auth',user.status===200&&user.text.includes(email),`status ${user.status}${user.status===200?'':' '+user.text.slice(0,300)}`);
 const buckets=await studio(host,'/api/platform/storage/default/buckets',{cookie});
 record('Studio lists the environment buckets through Storage',buckets.status===200&&buckets.text.startsWith('['),`status ${buckets.status}${buckets.status===200?'':' '+buckets.text.slice(0,300)}`);
 const other='f'.repeat(24)+'.studio.localhost';
 const elsewhere=await studio(other,'/api/platform/profile',{cookie});
 record('the session does not open another environment',elsewhere.status===401,`status ${elsewhere.status}`);

 const stop=await call('DELETE',path);
 record('the owner stops Studio',stop.status===202,`status ${stop.status}`);
 const until=Date.now()+2*60_000;
 while(Date.now()<until){if((await call('GET',path)).json?.data?.state==='stopped')break;await Bun.sleep(2000);}
 record('Studio is stopped again',(await call('GET',path)).json?.data?.state==='stopped');
 const after=await studio(host,'/api/platform/profile',{cookie});
 record('a stopped Studio is not reachable with the old session',after.status===503,`status ${after.status}`);
} catch(error) {
 record('studio check ran to the end',false,(error as Error).message);
}
await finish();
