/** Parent cutover controller owns operation.lock and validates stopped source. */
import {createClient} from '@supabase/supabase-js';
import {createHash} from 'node:crypto';
import {serveLocal} from '../src/http/local-server';
import {openUpstreamApplication} from './upstream-app';
const input=await Bun.stdin.json(),e:string=input.environment;
if(!/^e_[a-f0-9]{24}$/.test(e))throw new Error('Invalid runtime');
const app=openUpstreamApplication();const server=await serveLocal(app.handler),base=`http://127.0.0.1:${server.port}/${e}`;
const key=app.keys.issue(e),checks:string[]=[];let revision=app.catalog.runtimeRouting(e).revision;
const client=createClient(base,key.token,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
const id=crypto.randomUUID(),path=`cutover-${id}.txt`;let upload=false,insert=false;
function check(name:string,ok:unknown):asserts ok {if(!ok)throw new Error(name);checks.push(name);}
async function sql(query:string){
 const child=Bun.spawn(['docker','exec','-i',input.database,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',e],{stdin:'pipe',stdout:'pipe',stderr:'ignore'});
 child.stdin.write(query);child.stdin.end();const output=await new Response(child.stdout).text();
 if(await child.exited)throw new Error('Target verification SQL failed');return output.trim();
}
try {
 check('environment maintenance is durable before target staging',app.catalog.runtimeRouting(e).maintenance&&revision===input.expectedRevision);
 check('managed gateway rejects traffic before target staging',(await fetch(base+'/rest/v1/durable_items',{headers:{apikey:key.token}})).status===503);
 revision=app.catalog.changeRuntimeRouting(e,revision,'stage',input.placement);
 check('staged target remains unavailable during maintenance',(await fetch(base+'/rest/v1/durable_items',{headers:{apikey:key.token}})).status===503);
 revision=app.catalog.changeRuntimeRouting(e,revision,'resume');
 const login=await client.auth.signInWithPassword({email:input.email,password:input.password});
 check('real SDK login through published target retains identity',!login.error&&login.data.user?.id===input.user);
 const rows=await client.from('durable_items').select();
 check('real SDK reads original RLS rows through target',!rows.error&&!!rows.data?.length&&rows.data.every(row=>row.owner_id===input.user));
 const added=await client.from('durable_items').insert({id,owner_id:input.user,value:'cutover-write'});insert=!added.error;
 check('real SDK writes through target placement',insert);
 check('new SDK row exists on independent target database',await sql(`SELECT count(*) FROM public.durable_items WHERE id='${id}' AND value='cutover-write';`)==='1');
 const put=await client.storage.from('durable-private').upload(path,'cutover-object',{contentType:'text/plain'});upload=!put.error;
 check('real SDK uploads through target Storage',upload);
 const get=await client.storage.from('durable-private').download(path);
 check('real SDK downloads exact target upload',!get.error&&await get.data!.text()==='cutover-object');
 const old=await fetch(base+'/storage/v1'+input.signed.path);
 check('pre-export signed URL works through managed gateway without API key',old.ok&&createHash('sha256').update(Buffer.from(await old.arrayBuffer())).digest('hex')===input.signed.sha256);
}finally{
 // Remove only this probe's uniquely named data before closing admission.
 try {
  if(upload){const removed=await client.storage.from('durable-private').remove([path]);if(removed.error)throw new Error('Upload fixture cleanup failed');}
  if(insert)await sql(`DELETE FROM public.durable_items WHERE id='${id}';`);
 }finally{
  try{
   const state=app.catalog.runtimeRouting(e);
   if(!state.maintenance){revision=app.catalog.changeRuntimeRouting(e,revision,'pause');}
  }finally{app.keys.revoke(e,key.id);server.stop(true);app.close();}
 }
}
checks.push('probe data removed, temporary key revoked and routing paused');
await Bun.write('docs/evidence/cutover-sdk-checks.json',JSON.stringify({scope:'Real Supabase SDK through composed managed gateway and persistent target placement. Login, RLS read/write, target SQL confirmation, Storage upload/download and pre-export signed URL. Source stopped. Target route paused after cleanup; no public DNS cutover or multi-worker drain.',checks,count:checks.length,finalRevision:revision},null,2)+'\n');
console.log(`${checks.length} managed target cutover checks passed`);
