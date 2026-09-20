import {createClient} from '@supabase/supabase-js';
import {KeyStore} from '../src/control/keys';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';

// Secret-bearing configuration arrives over stdin and is never written or logged.
const input=JSON.parse(await Bun.stdin.text()) as {storage:string;tenants:Record<string,{anonymousToken:string;token:string}>};
const keys=new KeyStore(':memory:'),issued=new Map<string,{id:string;token:string}>();
const routes=new Map<string,EnvironmentRoute>();
for(const [e,tenant] of Object.entries(input.tenants)) {
 const key=keys.issue(e);issued.set(e,key);
 routes.set(e,{auth:'http://unused.invalid',rest:'http://unused.invalid',storage:{url:input.storage,tenantHost:e+'.storage.internal'},
  keys:[],anonymousToken:tenant.anonymousToken,enabled:true});
}
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:createGateway(routes,fetch,(e,key)=>keys.resolve(e,key)==='publishable')});
const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
try {
 const base=`http://127.0.0.1:${server.port}`;
 for(const [e,tenant] of Object.entries(input.tenants)) {
  const credential=issued.get(e)!;
  const client=createClient(base+'/'+e,credential.token,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
   global:{headers:{Authorization:`Bearer ${tenant.token}`}}});
  const bucket=client.storage.from('private');
  const upload=await bucket.upload('sdk.txt',new Blob(['sdk-'+e],{type:'text/plain'}));
  check(e+' SDK upload through Storage gateway',!upload.error);
  const upsert=await bucket.upload('sdk.txt',new Blob(['updated-'+e],{type:'text/plain'}),{upsert:true});
  check(e+' SDK upsert through Storage gateway',!upsert.error);
  const download=await bucket.download('sdk.txt');
  check(e+' SDK download returns updated bytes',!download.error&&await download.data!.text()==='updated-'+e);
  const listing=await bucket.list();check(e+' SDK list includes owned upload',!listing.error&&!!listing.data?.some(x=>x.name==='sdk.txt'));
  for(const other of Object.keys(input.tenants)) {
   if(other===e)continue;
   const forged=await fetch(`${base}/${e}/storage/v1/object/private/same.txt`,{headers:{apikey:credential.token,authorization:`Bearer ${tenant.token}`,
    'x-forwarded-host':other+'.storage.internal','x-project-id':other}});
   check(e+' injected tenant header cannot select neighbor',forged.status===200&&await forged.text()===e);
   const crossed=await fetch(`${base}/${other}/storage/v1/object/private/same.txt`,{headers:{apikey:credential.token,authorization:`Bearer ${tenant.token}`}});
   check(e+' API key cannot select neighbor Storage route',crossed.status===401);
  }
  const publicUrl=client.storage.from('public').getPublicUrl('public.txt').data.publicUrl;
  const publicRead=await fetch(publicUrl);
  check(e+' SDK public URL downloads without API headers',publicRead.status===200&&await publicRead.text()===e);
  const privatePublic=client.storage.from('private').getPublicUrl('same.txt').data.publicUrl;
  check(e+' public URL cannot disclose private bucket',(await fetch(privatePublic)).status!==200);
  const signed=await bucket.createSignedUrl('same.txt',60);
  check(e+' SDK creates signed download URL',!signed.error&&!!signed.data?.signedUrl);
  const signedUrl=signed.data!.signedUrl;
  const signedRead=await fetch(signedUrl);
  check(e+' signed URL downloads without API headers',signedRead.status===200&&await signedRead.text()===e);
  const expiring=await bucket.createSignedUrl('same.txt',1);
  check(e+' short-lived signed URL created',!expiring.error&&!!expiring.data?.signedUrl);
  await new Promise(resolve=>setTimeout(resolve,2100));
  check(e+' expired signed URL denied',(await fetch(expiring.data!.signedUrl)).status!==200);
  const tampered=new URL(signedUrl);tampered.searchParams.set('token','invalid-signature');
  check(e+' invalid signature denied upstream',(await fetch(tampered)).status!==200);
  const otherPath=new URL(signedUrl);otherPath.pathname=otherPath.pathname.replace('same.txt','sdk.txt');
  check(e+' signed URL cannot select another object',(await fetch(otherPath)).status!==200);
  for(const other of Object.keys(input.tenants)) {
   if(other===e)continue;
   const crossed=new URL(signedUrl);crossed.pathname=crossed.pathname.replace('/'+e+'/', '/'+other+'/');
   check(e+' signed URL cannot select another environment',(await fetch(crossed)).status!==200);
  }
  const removed=await bucket.remove(['sdk.txt']);check(e+' SDK remove own upload',!removed.error);
  keys.revoke(e,credential.id);
  const rejected=await bucket.download('same.txt');check(e+' revoked key denied by Storage gateway',!!rejected.error);
  const stillSigned=await fetch(signedUrl);
  check(e+' signed URL remains independent of API-key revocation until expiry',stillSigned.status===200&&await stillSigned.text()===e);
 }
 console.log(JSON.stringify({checks}));
} finally {server.stop(true);keys.close();}
