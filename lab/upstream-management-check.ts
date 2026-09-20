import {createClient} from '@supabase/supabase-js';
import {internalToken,managementPublishableKey,openUpstreamApplication} from './upstream-app';

const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
async function command(args:string[]) {
 const child=Bun.spawn(args,{stdout:'pipe',stderr:'ignore'});
 const output=await new Response(child.stdout).text();
 if(await child.exited)throw new Error('Management probe command failed');return output.trim();
}
let app=openUpstreamApplication();
let server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:app.handler});
let base=`http://127.0.0.1:${server.port}`;
const secrets=await Bun.file('.secrets/upstream/runtime.json').json();
const endpoints=await Bun.file('.lab/upstream/endpoints.json').json();
let management=await Bun.file('.lab/upstream/management.json').json();
const probe=await Bun.file('.lab/upstream/probe.json').json();
const job=app.catalog.getProvision('durable-probe-owner',probe.environments[0]);
const neighbor=app.catalog.getProvision('durable-probe-owner',probe.environments[1]);
const suffix=crypto.randomUUID(),email=`manager-${suffix}@example.com`,password=`Local-${suffix}`;
let owner:string|undefined,keyId:string|undefined,rawKey:string|undefined;
const path=`/management/v1/environments/${job.environment}`;
const client=()=>createClient(base+'/management',managementPublishableKey,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
const control=(tail:string,method:string,token:string)=>fetch(base+path+tail,{method,headers:{authorization:'Bearer '+token}});
try {
 const provisioned=await fetch(management.auth+'/admin/users',{method:'POST',headers:{
  authorization:'Bearer '+internalToken(secrets.management.jwt,'service_role'),'content-type':'application/json'},
  body:JSON.stringify({email,password,email_confirm:true})});
 const user=await provisioned.json();check('private operator creates dedicated management identity',provisioned.ok&&!!user.id);owner=user.id;
 app.catalog.setMember('durable-probe-owner',probe.organization,owner!,'owner');
 const directSignup=await fetch(management.auth+'/signup',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:'blocked-'+email,password})});
 check('management Auth itself disables public signup',!directSignup.ok);
 const managementClient=client();
 const blocked=await managementClient.auth.signUp({email:'blocked-'+email,password});
 check('public management gateway blocks signup',!!blocked.error);
 const login=await managementClient.auth.signInWithPassword({email,password});
 check('SDK management password login works',!login.error&&login.data.user?.id===owner);
 if(!login.data.session)throw new Error('No management session');
 let adminToken=login.data.session.access_token;
 const verified=await managementClient.auth.getUser();check('management SDK getUser works through limited gateway',!verified.error&&verified.data.user?.id===owner);
 const settings=await control('/connection','GET',adminToken),connection=await settings.json();
 check('management identity discovers all three configured services',settings.ok&&JSON.stringify(connection.services)===JSON.stringify(['auth','rest','storage']));
 const issue=await control('/keys','POST',adminToken),credential=await issue.json();keyId=credential.id;rawKey=credential.token;
 check('dedicated management identity issues scoped publishable key',issue.status===201&&!!rawKey);
 const listing=await control('/keys','GET',adminToken);check('key listing omits raw key',listing.ok&&!(await listing.text()).includes(rawKey!));
 const applicationClient=createClient(`${base}/${job.runtime}`,rawKey!,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const signup=await applicationClient.auth.signUp({email,password});
 check('issued key accesses application Auth with separate identity',!signup.error&&!!signup.data.session&&signup.data.user?.id!==owner);
 if(!signup.data.session)throw new Error('No application session');
 check('application JWT cannot issue management keys',(await control('/keys','POST',signup.data.session.access_token)).status===401);
 const read=await applicationClient.from('durable_items').select();check('issued key accesses original REST with RLS',!read.error&&read.data?.length===0);
 const objectName=`management-${suffix}.txt`;
 const upload=await applicationClient.storage.from('durable-private').upload(objectName,'management-key-object',{contentType:'text/plain'});
 check('issued key accesses shared Storage upload',!upload.error);
 const download=await applicationClient.storage.from('durable-private').download(objectName);
 check('issued key accesses private Storage download',!download.error&&await download.data!.text()==='management-key-object');
 const crossed=await fetch(`${base}/${neighbor.runtime}/rest/v1/`,{headers:{apikey:rawKey!}});
 check('issued key denied by neighboring runtime',crossed.status===401);
 const blockedAdmin=await fetch(base+'/management/auth/v1/admin/users',{headers:{apikey:managementPublishableKey,authorization:'Bearer '+adminToken}});
 check('management Auth admin API not exposed',blockedAdmin.status===404);
 app.catalog.setMember('durable-probe-owner',probe.organization,owner!,'viewer');
 check('current viewer cannot mint keys',(await control('/keys','POST',adminToken)).status===403);
 check('current viewer can discover connection',(await control('/connection','GET',adminToken)).status===200);
 app.catalog.setMember('durable-probe-owner',probe.organization,owner!,'owner');
 server.stop(true);app.close();
 await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);
 await command(['/usr/bin/python3','lab/durable_runtime.py','up']);
 app=openUpstreamApplication();server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:app.handler});base=`http://127.0.0.1:${server.port}`;
 management=await Bun.file('.lab/upstream/management.json').json();
 const after=await client().auth.signInWithPassword({email,password});
 check('management identity survives runtime restart',!after.error&&after.data.user?.id===owner);
 if(!after.data.session)throw new Error('No restarted management session');adminToken=after.data.session.access_token;
 const persistent=await fetch(`${base}/${job.runtime}/rest/v1/`,{headers:{apikey:rawKey!}});
 check('issued key survives server and runtime restart',persistent.status===200);
 const removed=await control(`/keys/${keyId}`,'DELETE',adminToken);check('management owner revokes key',removed.ok);
 for(const service of ['auth','rest','storage']) {
  const response=await fetch(`${base}/${job.runtime}/${service}/v1/`,{headers:{apikey:rawKey!}});
  check('revocation blocks '+service+' gateway requests',response.status===401);
 }
 server.stop(true);app.close();app=openUpstreamApplication();server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:app.handler});base=`http://127.0.0.1:${server.port}`;
 check('revocation persists after process state reopen',(await fetch(`${base}/${job.runtime}/rest/v1/`,{headers:{apikey:rawKey!}})).status===401);
 // Verify database login boundaries without printing passwords or SQL errors.
 for(const [role,database,passwordValue] of [
  ['management_auth',job.runtime,secrets.management.auth],
  ...['auth','rest','storage'].map(role=>[job.runtime+'_'+role,'management',secrets.environments[job.runtime][role]])]) {
  const child=Bun.spawn(['docker','exec','-i','sbarbase-durable-db','sh','-c',
   'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -Atc "SELECT 1"',
   'probe','sbarbase-durable-db',role,database],{stdin:'pipe',stdout:'ignore',stderr:'ignore'});
  child.stdin.write(passwordValue+'\n');child.stdin.end();
  check(role+' cannot connect to '+database,(await child.exited)!==0);
 }
 console.log(`${checks.length} dedicated management integration checks passed.`);
} finally {
 if(keyId)app.keys.revoke(job.runtime,keyId);
 if(owner) {
  app.catalog.setMember('durable-probe-owner',probe.organization,owner,null);
  await fetch(management.auth+'/admin/users/'+owner,{method:'DELETE',headers:{authorization:'Bearer '+internalToken(secrets.management.jwt,'service_role')}});
 }
 server.stop(true);app.close();
 await command(['/usr/bin/python3','lab/durable_runtime.py','stop']);
 await Bun.write('.lab/upstream/management-verification.json',JSON.stringify({scope:'Dedicated management Auth, actual memberships and durable publishable keys on upstream Auth/REST/shared Storage; runtime restart and crossed realm/DB denial; loopback only',checks},null,2));
}
