import {Catalog} from '../src/control/catalog';
import {createGateway} from '../src/gateway/handler';
import {createClient} from '@supabase/supabase-js';
import {createHmac} from 'node:crypto';
const candidate=Bun.argv[2];
if(!candidate||!/^sbar-fresh-[a-f0-9]{16}-db$/.test(candidate))throw new Error('Fixture database identity required');
const database:string=candidate;
const state='.lab/upstream';
const probe=await Bun.file(`${state}/fresh-probe.json`).json();
const catalog=new Catalog(`${state}/control.sqlite`);
const job=catalog.getProvision('fresh-owner',probe.environment);catalog.close();
if(job.state!=='succeeded')throw new Error('Worker must complete first');
const runtime=job.runtime;
const values=(await Bun.file('.secrets/upstream/runtime.json').json()).environments[runtime];
const endpoints=(await Bun.file(`${state}/endpoints.json`).json())[runtime];
const encode=(v:unknown)=>Buffer.from(JSON.stringify(v)).toString('base64url');
const token=(role:string)=>{const value=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role});return value+'.'+createHmac('sha256',values.jwt).update(value).digest('base64url');};
async function sql(query:string){
 const child=Bun.spawn(['docker','exec','-i',database,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',runtime],{stdin:'pipe',stdout:'ignore',stderr:'ignore'});
 child.stdin.write(query);child.stdin.end();if(await child.exited)throw new Error('Fixture schema setup failed');
}
await sql(`CREATE TABLE public.fresh_items(id uuid PRIMARY KEY,owner_id uuid NOT NULL,value text NOT NULL);
ALTER TABLE public.fresh_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_items ON public.fresh_items TO authenticated USING(owner_id=auth.uid()) WITH CHECK(owner_id=auth.uid());
GRANT SELECT,INSERT ON public.fresh_items TO authenticated;
CREATE POLICY own_objects ON storage.objects TO authenticated USING(owner_id=auth.uid()::text) WITH CHECK(owner_id=auth.uid()::text);
NOTIFY pgrst,'reload schema';`);
const key='fresh-fixture-key';
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:createGateway(new Map([[runtime,{...endpoints,keys:[key],anonymousToken:token('anon'),enabled:true}]]))});
try{
 const client=createClient(`http://127.0.0.1:${server.port}/${runtime}`,key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const account=await client.auth.signUp({email:`fresh-${crypto.randomUUID()}@example.com`,password:'Fixture-'+crypto.randomUUID()});
 if(account.error||!account.data.user||!account.data.session)throw new Error('SDK signup failed');
 const id=crypto.randomUUID();let inserted=false;
 for(let attempt=0;attempt<30;attempt++){
  const write=await client.from('fresh_items').insert({id,owner_id:account.data.user.id,value:'worker-created'});
  if(!write.error){inserted=true;break;}if(write.error.code!=='PGRST205')throw new Error('SDK RLS insert failed');await Bun.sleep(100);
 }
 if(!inserted)throw new Error('REST schema refresh deadline');
 const read=await client.from('fresh_items').select('value').eq('id',id).single();
 if(read.error||read.data?.value!=='worker-created')throw new Error('SDK RLS read failed');
 const forbidden=await client.from('fresh_items').insert({id:crypto.randomUUID(),owner_id:crypto.randomUUID(),value:'forbidden'});
 if(!forbidden.error||forbidden.error.code!=='42501')throw new Error('RLS did not reject mismatched owner');
 const stranger=createClient(`http://127.0.0.1:${server.port}/${runtime}`,key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const other=await stranger.auth.signUp({email:`other-${crypto.randomUUID()}@example.com`,password:'Fixture-'+crypto.randomUUID()});
 if(other.error||!other.data.session)throw new Error('Second identity failed');
 const hidden=await stranger.from('fresh_items').select('id').eq('id',id);
 if(hidden.error||hidden.data?.length!==0)throw new Error('RLS exposed another user row');
 const bucket='fresh';
 const created=await fetch(endpoints.storage.url+'/bucket',{method:'POST',headers:{authorization:'Bearer '+token('service_role'),'x-forwarded-host':endpoints.storage.tenantHost,'content-type':'application/json'},body:JSON.stringify({id:bucket,name:bucket,public:false})});
 if(!created.ok)throw new Error('Fixture bucket creation failed');
 const upload=await client.storage.from(bucket).upload('proof.txt',new Blob(['fresh-worker-proof'],{type:'text/plain'}));
 if(upload.error)throw new Error('SDK Storage upload failed');
 const downloaded=await client.storage.from(bucket).download('proof.txt');
 if(downloaded.error||await downloaded.data?.text()!=='fresh-worker-proof')throw new Error('SDK Storage download failed');
 const hiddenFile=await stranger.storage.from(bucket).download('proof.txt');
 if(!hiddenFile.error)throw new Error('Storage exposed another user file');
 const stillOwned=await client.storage.from(bucket).download('proof.txt');
 if(stillOwned.error||await stillOwned.data?.text()!=='fresh-worker-proof')throw new Error('Owner Storage access lost during denial probe');
 console.log('Fresh worker SDK passed');
}finally{server.stop(true);}
