// Shared pieces of the live checks that sign in as the operator and write one evidence file:
// the operator file, the installation's address, the check list with its evidence, the
// management sign-in and a caller for the management API. Used by the checks added for the
// rehearsal VM (invitation, backup-traffic, environment-limit, restore-drill); the older
// checks still carry their own copies.
import {createClient} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';
import {managementPublishableKey} from './upstream-app';

export type Check={check:string;ok:boolean;detail:string};

/** The value after `name` on the command line, or the fallback. */
export function option(args:string[],name:string,fallback:string){const at=args.indexOf(name);return at>=0?args[at+1]??fallback:fallback;}

/** The operator's email and password from the private 0600 bootstrap file; exits 2 otherwise. */
export function operatorFrom(path:string|undefined,usage:string):{email:string;password:string} {
 if(!path){console.error(usage);process.exit(2);}
 if((statSync(path).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
 return JSON.parse(readFileSync(path,'utf8'));
}

/** The loopback address the running installation publishes. */
export function installationUrl(){return (JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;}

/** A list of checks that prints each one, and writes the evidence file and exits on finish. */
export function checkList(name:string,evidencePath:string,extra:()=>Record<string,unknown>=()=>({})) {
 const checks:Check[]=[],started=Date.now();
 const record=(check:string,ok:boolean,detail='')=>{checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;};
 const finish=async():Promise<never>=>{
  const passed=checks.length>0&&checks.every(row=>row.ok);
  writeFileSync(evidencePath,JSON.stringify({check:name,recorded:new Date().toISOString(),passed,count:checks.length,
   seconds:Math.round((Date.now()-started)/1000),...extra(),checks},null,2)+'\n');
  console.log(`evidence: ${evidencePath}\n${name} check: ${passed?'passed':'failed'}`);
  process.exit(passed?0:1);
 };
 return {checks,record,finish};
}

/** Signs in through the management Auth realm; the token is undefined when it refuses. */
export async function signIn(base:string,email:string,password:string) {
 const client=createClient(`${base}/management`,managementPublishableKey,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await client.auth.signInWithPassword({email,password});
 return {token:login.data.session?.access_token,userId:login.data.user?.id,error:login.error?.message??''};
}

/** Calls the management API as the holder of `token`, answering the status and parsed body. */
export function managementCaller(base:string,token:string) {
 return async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body?{'content-type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
}

/** Polls an environment's provisioning until it settles or ten minutes pass. */
export async function waitProvisioned(call:ReturnType<typeof managementCaller>,environment:string) {
 let state='queued',failure='';const deadline=Date.now()+10*60_000;
 while(Date.now()<deadline&&!['succeeded','failed','cancelled'].includes(state)) {
  await Bun.sleep(3000);
  const job=(await call('GET',`/environments/${environment}/provision`)).json;
  state=job?.state??'unknown';failure=job?.failure??'';
 }
 return {state,failure};
}
