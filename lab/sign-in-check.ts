// Sign-in check: an operator sets an environment's sign-in settings and an OAuth provider.
//
// Usage: bun lab/sign-in-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one). Saves a site URL, redirect addresses and a GitHub provider through the
// management API, waits for the supervisor to recreate Auth with them, then acts as a
// browser would: it starts a GitHub sign-in and opens an email link with no key, and reads
// Auth's settings with one. No real provider is contacted; the provider's client id is a
// placeholder. Finally it removes the provider again. The operator password is read from the
// private file and never printed; the provider secret never appears in any answer.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/sign-in-checks.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/sign-in-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
const SECRET='placeholder-secret-'+crypto.randomUUID();

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'sign-in',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Sign-in settings for one environment on a running installation: a site URL, '+
  'redirect addresses and a GitHub provider saved through the management API and applied by recreating that environment\'s Auth; '+
  'a keyless OAuth start and email link through the gateway as a browser sends them; the provider removed again. No real provider '+
  'was contacted.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nsign-in check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body?{'content-type':'application/json'}:{})},
   body:body===undefined?undefined:JSON.stringify(body)});
  const text=await response.text();
  return {status:response.status,text,json:(()=>{try{return JSON.parse(text);}catch{return null;}})() as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0];
 const project=(await call('GET',`/organizations/${organization.id}/projects`)).json?.data?.[0];
 const environment=(await call('GET',`/projects/${project.id}/environments`)).json?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))await finish();
 const path=`/environments/${environment.id}/sign-in`;
 const apiPath=(await call('GET',`/environments/${environment.id}/connection`)).json?.apiPath as string;
 const url=`${base}${apiPath}`;
 const key=(await call('POST',`/environments/${environment.id}/keys`)).json?.token as string;
 if(!record('a publishable key is issued for the check',!!key))await finish();

 const initial=await call('GET',path);
 record('an environment starts with the default sign-in settings',initial.status===200&&initial.json?.data?.state==='unconfigured',`status ${initial.status}`);
 const refused=await call('PUT',path,{site_url:'javascript:alert(1)'});
 record('an unsafe site URL is refused',refused.status===400,`status ${refused.status}`);

 const settings={site_url:'https://app.example.com',redirect_urls:['https://app.example.com/**'],signup:true,anonymous:false,
  providers:{github:{enabled:true,client_id:'placeholder-client-id',secret:SECRET,url:''}}};
 const saved=await call('PUT',path,settings);
 record('an owner saves a site URL, redirects and GitHub',saved.status===202&&saved.json?.data?.state==='pending',`status ${saved.status}`);
 record('the provider secret is never sent back',!saved.text.includes(SECRET)&&saved.json?.data?.settings?.providers?.github?.secret_set===true);
 const wait=async()=>{
  let state='pending',failure='';const deadline=Date.now()+3*60_000;
  while(Date.now()<deadline){const current=(await call('GET',path)).json?.data;state=current?.state;failure=current?.failure??'';
   if(state!=='pending')break;await Bun.sleep(2000);}
  return {state,failure};
 };
 const applied=await wait();
 if(!record('the supervisor recreates Auth with the settings',applied.state==='applied',`state ${applied.state} ${applied.failure}`))await finish();
 const callback=(await call('GET',path)).json?.data?.callback_url as string;

 const authSettings=await (await fetch(`${url}/auth/v1/settings`,{headers:{apikey:key}})).json() as any;
 record('Auth offers GitHub sign-in',authSettings?.external?.github===true&&authSettings?.disable_signup===false);
 const start=await fetch(`${url}/auth/v1/authorize?provider=github&redirect_to=${encodeURIComponent('https://app.example.com/welcome')}`,{redirect:'manual'});
 const location=start.headers.get('location')??'';
 record('a browser starts a GitHub sign-in without a key',start.status>=300&&start.status<400&&location.startsWith('https://github.com/login/oauth/authorize'),
  `status ${start.status}`);
 record('GitHub is told this environment\'s callback address',location.includes('client_id=placeholder-client-id')&&
  location.includes('redirect_uri='+encodeURIComponent(callback)),callback);
 const link=await fetch(`${url}/auth/v1/verify?token=not-a-token&type=signup&redirect_to=${encodeURIComponent('https://app.example.com/welcome')}`,{redirect:'manual'});
 record('an email link reaches Auth without a key and returns to the application',link.status>=300&&link.status<400&&
  (link.headers.get('location')??'').startsWith('https://app.example.com'),`status ${link.status}`);
 const app=createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false}});
 const signUp=await app.auth.signUp({email:`sign-in-${crypto.randomUUID().slice(0,8)}@example.com`,password:crypto.randomUUID()});
 record('email sign-up still works after the recreate',!signUp.error&&!!signUp.data.user,signUp.error?.message??'');

 const cleared=await call('PUT',path,{site_url:'https://app.example.com',redirect_urls:[],signup:true,anonymous:false,providers:{}});
 record('the owner removes the provider',cleared.status===202,`status ${cleared.status}`);
 const reapplied=await wait();
 const after=await (await fetch(`${url}/auth/v1/settings`,{headers:{apikey:key}})).json() as any;
 record('GitHub sign-in is gone once applied',reapplied.state==='applied'&&after?.external?.github===false,`state ${reapplied.state}`);
} catch(error) {
 record('sign-in check ran to the end',false,(error as Error).message);
}
await finish();
