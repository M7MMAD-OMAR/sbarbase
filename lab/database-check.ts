// Direct database access check: a developer connects with a PostgreSQL client and runs a migration.
//
// Usage: bun lab/database-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one). It turns direct access on through the management API, connects through the
// loopback listener with the password shown once, and runs what a Supabase project's
// migration does: a table with row level security and a policy, a trigger on auth.users that
// creates a profile at sign-up, and a Storage policy. It then checks through supabase-js that
// the API sees the result, that other logins and databases are refused, that a new password
// replaces the old one, and that turning access off closes it.
// The operator password is read from the private file and never printed.
import {createClient} from '@supabase/supabase-js';
import {SQL} from 'bun';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/database-checks.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/database-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();
function finish():never {
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'database',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Direct database access for one environment on a running installation: turned on '+
  'through the management API, a PostgreSQL client through the loopback listener running a Supabase-style migration (a table with row '+
  'level security, a trigger on auth.users, a Storage policy), the result seen through supabase-js, other logins and databases refused, '+
  'a password reset, and access turned off.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\ndatabase check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}
const withPassword=(url:string,password:string)=>url.replace('[YOUR-PASSWORD]',encodeURIComponent(password));
async function query(url:string,text:string):Promise<{ok:true;rows:any[]}|{ok:false;error:string}> {
 const sql=new SQL(url+(url.includes('?')?'&':'?')+'sslmode=disable',{max:1,idleTimeout:1,connectionTimeout:10});
 try{return {ok:true,rows:[...await sql.unsafe(text)]};}
 catch(error){return {ok:false,error:(error as Error).message};}
 finally{await sql.close().catch(()=>{});}
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body!==undefined?{'content-type':'application/json'}:{})},
   body:body===undefined?undefined:JSON.stringify(body)});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0];
 const project=(await call('GET',`/organizations/${organization.id}/projects`)).json?.data?.[0];
 const environment=(await call('GET',`/projects/${project.id}/environments`)).json?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))finish();
 const path=`/environments/${environment.id}/database`;
 const initial=(await call('GET',path)).json?.data;
 record('direct access starts off',initial?.state==='off',initial?.url);
 const waitFor=async(want:string)=>{let state='pending',failure='';const until=Date.now()+3*60_000;
  while(Date.now()<until){const current=(await call('GET',path)).json?.data;state=current?.state;failure=current?.failure??'';if(state!=='pending')break;await Bun.sleep(2000);}
  return {state,failure,ok:state===want};};
 const on=await call('PUT',path,{enabled:true});
 const password=on.json?.data?.password as string;
 record('an owner turns it on and sees the password once',on.status===202&&typeof password==='string'&&password.length>=24,`status ${on.status}`);
 const applied=await waitFor('on');
 if(!record('the supervisor opens the developer login',applied.ok,`${applied.state} ${applied.failure}`))finish();
 record('the password is never shown again',!JSON.stringify((await call('GET',path)).json).includes(password));
 const url=withPassword(initial.url,password),runtime=initial.connection.database as string;

 const who=await query(url,'select current_user as user, current_database() as database');
 record('a PostgreSQL client connects as the developer login to its own database',who.ok&&who.rows[0]?.user===`${runtime}_developer`&&who.rows[0]?.database===runtime,
  who.ok?JSON.stringify(who.rows[0]):who.error);
 const migration=await query(url,`
create table if not exists public.profiles (id uuid primary key references auth.users(id) on delete cascade, email text, created_at timestamptz default now());
alter table public.profiles enable row level security;
drop policy if exists "profiles are readable" on public.profiles;
create policy "profiles are readable" on public.profiles for select to anon, authenticated using (true);
create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path = public as $$
begin insert into public.profiles (id, email) values (new.id, new.email); return new; end; $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();
drop policy if exists "avatars are public" on storage.objects;
create policy "avatars are public" on storage.objects for select using (bucket_id = 'avatars');
notify pgrst, 'reload schema';`);
 record('a Supabase-style migration runs: a table with a policy, a trigger on auth.users and a Storage policy',migration.ok,migration.ok?'':migration.error);
 const key=(await call('POST',`/environments/${environment.id}/keys`)).json?.token as string;
 const client=createClient(`${base}/${runtime}`,key,{auth:{persistSession:false,autoRefreshToken:false}});
 const email=`database-${Date.now()}@example.com`;
 const signUp=await client.auth.signUp({email,password:crypto.randomUUID()+'Aa1!'});
 await Bun.sleep(1000);
 const profile=await client.from('profiles').select('email').eq('email',email);
 record('the trigger created a profile at sign-up, readable through the API',!signUp.error&&profile.data?.length===1,
  signUp.error?.message??profile.error?.message??`${profile.data?.length} row(s)`);
 const adminLogin=initial.url.replace(`${runtime}_developer:[YOUR-PASSWORD]`,`supabase_admin:${encodeURIComponent(password)}`);
 const superuser=await query(adminLogin,'select 1');
 record('the superuser is refused at the listener',!superuser.ok,superuser.ok?'connected':superuser.error.slice(0,120));
 const elsewhere=await query(url.replace(new RegExp(`/${runtime}$`),'/postgres'),'select 1');
 record('another database is refused at the listener',!elsewhere.ok,elsewhere.ok?'connected':elsewhere.error.slice(0,120));
 const wrong=await query(withPassword(initial.url,'wrong-password-0000000000000'),'select 1');
 record('a wrong password is refused by PostgreSQL',!wrong.ok,wrong.ok?'connected':wrong.error.slice(0,120));

 const reset=await call('POST',`${path}/password`);
 const next=reset.json?.data?.password as string;
 const resetApplied=await waitFor('on');
 record('a new password is issued and applied',reset.status===202&&!!next&&next!==password&&resetApplied.ok,`status ${reset.status} ${resetApplied.state}`);
 const old=await query(url,'select 1');
 const fresh=await query(withPassword(initial.url,next),'select 1');
 record('the old password stops working and the new one works',!old.ok&&fresh.ok,old.ok?'old still works':fresh.ok?'':fresh.error);

 const off=await call('PUT',path,{enabled:false});
 const closed=await waitFor('off');
 record('the owner turns access off',off.status===202&&closed.ok,`${closed.state} ${closed.failure}`);
 const after=await query(withPassword(initial.url,next),'select 1');
 record('the developer login cannot connect once access is off',!after.ok,after.ok?'connected':after.error.slice(0,120));
} catch(error) {
 record('database check ran to the end',false,(error as Error).message);
}
finish();
