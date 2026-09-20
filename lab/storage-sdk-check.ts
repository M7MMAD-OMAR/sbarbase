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
  const removed=await bucket.remove(['sdk.txt']);check(e+' SDK remove own upload',!removed.error);
  keys.revoke(e,credential.id);
  const rejected=await bucket.download('same.txt');check(e+' revoked key denied by Storage gateway',!!rejected.error);
 }
 console.log(JSON.stringify({checks}));
} finally {server.stop(true);keys.close();}
