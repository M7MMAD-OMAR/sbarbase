// Upload check: files larger than a MiB go through the gateway to Storage and come back intact.
//
// Usage: bun lab/upload-check.ts <operator.json> --limit-mb N [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one). As an application with supabase-js it creates a bucket, uploads a 20 MiB
// file and downloads it byte for byte, uploads a file just under the installation's upload
// limit (SBARBASE_UPLOAD_LIMIT_MB, given as --limit-mb), and checks that one just over it is
// refused. The operator password is read from the private file and never printed.
import {createClient} from '@supabase/supabase-js';
import {createHash,randomBytes} from 'node:crypto';
import {existsSync,readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const MiB=1024*1024;
const args=process.argv.slice(2);
const operatorPath=args[0];
const option=(name:string)=>{const at=args.indexOf(name);return at>=0?args[at+1]:undefined;};
const limit=Number(option('--limit-mb'));
const evidencePath=option('--evidence')??'docs/evidence/upload-checks.json';
if(!operatorPath||!Number.isInteger(limit)||limit<22){console.error('usage: bun lab/upload-check.ts <operator.json> --limit-mb N (N >= 22) [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=existsSync(evidencePath)&&args.includes('--append')?JSON.parse(readFileSync(evidencePath,'utf8')).checks:[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
function finish():never {
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath,JSON.stringify({check:'upload',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'File uploads larger than a MiB through the gateway to the shared Storage with supabase-js: '+
  'a 20 MiB file uploaded and downloaded byte for byte, a file just under the installation upload limit accepted and one just over it refused.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nupload check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}
const digest=(bytes:Uint8Array)=>createHash('sha256').update(bytes).digest('hex');

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))finish();
 const get=async(path:string,method='GET')=>(await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`}})).json() as Promise<any>;
 const organization=(await get('/organizations')).data?.[0];
 const project=(await get(`/organizations/${organization.id}/projects`)).data?.[0];
 const environment=(await get(`/projects/${project.id}/environments`)).data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))finish();
 const apiPath=(await get(`/environments/${environment.id}/connection`)).apiPath as string;
 const key=(await get(`/environments/${environment.id}/keys`,'POST')).token as string;
 // Storage writes need a signed-in user; an application's own user does it here.
 const client=createClient(`${base}${apiPath}`,key,{auth:{persistSession:false,autoRefreshToken:false}});
 const signIn=await client.auth.signUp({email:`upload-${Date.now()}@example.com`,password:randomBytes(18).toString('base64url')+'Aa1!'});
 if(!record('an application user signs in',!!signIn.data.session,signIn.error?.message??''))finish();
 const bucket=`uploads-${Date.now()}`;
 // Creating a bucket needs the service role; policies let the user write to it instead.
 const policy=Bun.spawnSync(['docker','exec','-i','sbarbase-durable-db','psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',apiPath.slice(1)],
  {stdin:Buffer.from(`insert into storage.buckets(id,name,public) values ('${bucket}','${bucket}',false);
drop policy if exists upload_check_write on storage.objects; create policy upload_check_write on storage.objects for insert to authenticated with check (bucket_id like 'uploads-%');
drop policy if exists upload_check_read on storage.objects; create policy upload_check_read on storage.objects for select to authenticated using (bucket_id like 'uploads-%');`)});
 record('a private bucket exists for the check',policy.exitCode===0,policy.stderr.toString().slice(0,200));

 const twenty=randomBytes(20*MiB);
 const up=await client.storage.from(bucket).upload('twenty.bin',twenty,{contentType:'application/octet-stream'});
 record('a 20 MiB file uploads through the gateway',!up.error,up.error?.message??'');
 const down=await client.storage.from(bucket).download('twenty.bin');
 const back=down.data?new Uint8Array(await down.data.arrayBuffer()):new Uint8Array();
 record('the 20 MiB file downloads byte for byte',back.byteLength===twenty.byteLength&&digest(back)===digest(twenty),`${back.byteLength} bytes`);
 const under=await client.storage.from(bucket).upload('under.bin',randomBytes((limit-1)*MiB),{contentType:'application/octet-stream'});
 record(`a ${limit-1} MiB file, under the ${limit} MiB limit, uploads`,!under.error,under.error?.message??'');
 const over=await client.storage.from(bucket).upload('over.bin',randomBytes((limit+1)*MiB),{contentType:'application/octet-stream'});
 const status=(over.error as any)?.statusCode??(over.error as any)?.status;
 record(`a ${limit+1} MiB file, over the limit, is refused`,!!over.error,`${status??''} ${over.error?.message??'accepted'}`);
 const listed=await client.storage.from(bucket).list();
 record('the refused file is not stored',!listed.data?.some(item=>item.name==='over.bin'),listed.data?.map(item=>item.name).join(','));
} catch(error) {
 record('upload check ran to the end',false,(error as Error).message);
}
finish();
