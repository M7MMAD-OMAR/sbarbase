// Realtime check: an operator turns Realtime on for an environment and two supabase-js clients use it.
//
// Usage: bun lab/realtime-check.ts <operator.json> [--evidence PATH]
//
// Needs a running installation with one provisioned environment (the first-project check
// leaves one) and the Docker CLI, which it uses once to create a test table as the database
// administrator, as an application's migration would. It turns Realtime on through the
// management API, then acts as two browser clients through the gateway: a broadcast from
// one reaches the other, presence is shared, a row inserted through REST arrives as a
// database change, and the broadcast REST API answers. It checks that a wrong key never
// reaches Realtime, then turns Realtime off and checks the socket is refused. The operator
// password is read from the private file and never printed.
import {createClient,type RealtimeChannel} from '@supabase/supabase-js';
import {readFileSync,statSync,writeFileSync} from 'node:fs';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const args=process.argv.slice(2);
const operatorPath=args[0];
const evidenceAt=args.indexOf('--evidence');
const evidencePath=evidenceAt>=0?args[evidenceAt+1]:'docs/evidence/realtime-checks.json';
if(!operatorPath||!evidencePath){console.error('usage: bun lab/realtime-check.ts <operator.json> [--evidence PATH]');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url;

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:   ':'FAIL: ')+check+(detail?'  '+detail:''));return ok;}
const started=Date.now();

async function finish(){
 const passed=checks.length>0&&checks.every(row=>row.ok);
 writeFileSync(evidencePath!,JSON.stringify({check:'realtime',recorded:new Date().toISOString(),passed,count:checks.length,
  seconds:Math.round((Date.now()-started)/1000),scope:'Realtime for one environment on a running installation: turned on through the '+
  'management API, then used by two supabase-js clients through the gateway for broadcast, presence and database changes on a '+
  'table with row level security, and the broadcast REST API; a wrong key refused; turned off again.',checks},null,2)+'\n');
 console.log(`evidence: ${evidencePath}\nrealtime check: ${passed?'passed':'failed'}`);
 process.exit(passed?0:1);
}

function within<T>(promise:Promise<T>,ms:number,label:string):Promise<T> {
 return Promise.race([promise,new Promise<T>((_,reject)=>setTimeout(()=>reject(new Error(label+' timed out')),ms))]);
}
function subscribed(channel:RealtimeChannel):Promise<string> {
 return new Promise(resolve=>channel.subscribe((status,error)=>{
  if(status==='SUBSCRIBED'||status==='CHANNEL_ERROR'||status==='TIMED_OUT')resolve(status+(error?' '+error.message:''));
 }));
}
function socketRefused(url:string):Promise<boolean> {
 return new Promise(resolve=>{
  const socket=new WebSocket(url);
  socket.onopen=()=>{socket.close();resolve(false);};
  socket.onerror=()=>resolve(true);socket.onclose=()=>resolve(true);
  setTimeout(()=>resolve(true),5000);
 });
}

try {
 const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
 const login=await management.auth.signInWithPassword({email:operator.email,password:operator.password});
 const token=login.data.session?.access_token;
 if(!record('operator logs in',!!token,login.error?.message??''))await finish();
 const call=async(method:string,path:string,body?:unknown)=>{
  const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body!==undefined?{'content-type':'application/json'}:{})},
   body:body===undefined?undefined:JSON.stringify(body)});
  return {status:response.status,json:await response.json().catch(()=>null) as any};
 };
 const organization=(await call('GET','/organizations')).json?.data?.[0];
 const project=(await call('GET',`/organizations/${organization.id}/projects`)).json?.data?.[0];
 const environment=(await call('GET',`/projects/${project.id}/environments`)).json?.data?.find((item:any)=>item.state==='succeeded');
 if(!record('a provisioned environment exists',!!environment?.id))await finish();
 const path=`/environments/${environment.id}/realtime`;
 const apiPath=(await call('GET',`/environments/${environment.id}/connection`)).json?.apiPath as string;
 const runtime=apiPath.slice(1);
 const url=`${base}${apiPath}`;
 const key=(await call('POST',`/environments/${environment.id}/keys`)).json?.token as string;

 record('Realtime starts off',(await call('GET',path)).json?.data?.state==='off');
 const socketUrl=`${url.replace(/^http/,'ws')}/realtime/v1/websocket?vsn=1.0.0&apikey=`;
 record('a socket is refused while Realtime is off',await socketRefused(socketUrl+encodeURIComponent(key)));
 const on=await call('PUT',path,{enabled:true});
 record('an owner turns Realtime on',on.status===202,`status ${on.status}`);
 let state='pending',failure='';const deadline=Date.now()+5*60_000;
 while(Date.now()<deadline){const current=(await call('GET',path)).json?.data;state=current?.state;failure=current?.failure??'';
  if(state!=='pending')break;await Bun.sleep(3000);}
 if(!record('the supervisor starts the environment\'s Realtime',state==='on',`state ${state} ${failure} after ${Math.round((Date.now()-started)/1000)} s`))await finish();
 const services=(await call('GET',`/environments/${environment.id}/connection`)).json?.services as string[];
 record('connection details list Realtime',services?.includes('realtime'),services?.join(','));

 // A table an application's migration would create: readable and writable by visitors, row security on.
 const sql=Bun.spawnSync(['docker','exec','-i','sbarbase-durable-db','psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',runtime],
  {stdin:Buffer.from(`create table if not exists public.realtime_probe(id bigint generated always as identity primary key, body text not null);
alter table public.realtime_probe enable row level security;
drop policy if exists probe_read on public.realtime_probe; create policy probe_read on public.realtime_probe for select to anon using (true);
drop policy if exists probe_write on public.realtime_probe; create policy probe_write on public.realtime_probe for insert to anon with check (true);
grant select, insert on public.realtime_probe to anon;
notify pgrst, 'reload schema';
select count(*) from pg_publication_tables where pubname = 'supabase_realtime' and tablename = 'realtime_probe';`)});
 record('a new public table is published to Realtime',sql.exitCode===0&&sql.stdout.toString().trim().endsWith('1'),sql.stderr.toString().slice(0,200));

 record('a wrong key never reaches Realtime',await socketRefused(socketUrl+'sb_publishable_wrong'));
 const options={auth:{persistSession:false,autoRefreshToken:false}};
 const first=createClient(url,key,options),second=createClient(url,key,options);
 const received:{broadcast?:unknown;change?:any;presence?:number}={};
 const room=first.channel('room-one',{config:{presence:{key:'first'}}})
  .on('broadcast',{event:'hello'},message=>{received.broadcast=message.payload;})
  .on('postgres_changes',{event:'INSERT',schema:'public',table:'realtime_probe'},change=>{received.change=change;})
  .on('presence',{event:'sync'},()=>{received.presence=Object.keys(room.presenceState()).length;});
 const firstStatus=await within(subscribed(room),30_000,'first subscribe');
 record('a client subscribes to broadcast, presence and database changes',firstStatus==='SUBSCRIBED',firstStatus);
 const other=second.channel('room-one',{config:{presence:{key:'second'}}});
 const secondStatus=await within(subscribed(other),30_000,'second subscribe');
 record('a second client joins the same channel',secondStatus==='SUBSCRIBED',secondStatus);
 await room.track({online:true});await other.track({online:true});
 await other.send({type:'broadcast',event:'hello',payload:{text:'from the second client'}});
 const until=Date.now()+15_000;
 while(Date.now()<until&&(!received.broadcast||(received.presence??0)<2))await Bun.sleep(200);
 record('a broadcast from one client reaches the other',(received.broadcast as any)?.text==='from the second client');
 record('both clients see each other in presence',(received.presence??0)>=2,`${received.presence??0} present`);
 // Realtime starts reading changes once a subscriber asks; give its poller a moment.
 await Bun.sleep(2000);
 const inserted=await second.from('realtime_probe').insert({body:'hello database'});
 record('a visitor inserts a row through REST',!inserted.error,inserted.error?.message??'');
 const changeUntil=Date.now()+20_000;
 while(Date.now()<changeUntil&&!received.change)await Bun.sleep(200);
 record('the insert arrives as a database change',received.change?.new?.body==='hello database',received.change?JSON.stringify(received.change.new):'nothing');
 const rest=await fetch(`${url}/realtime/v1/api/broadcast`,{method:'POST',headers:{apikey:key,'content-type':'application/json'},
  body:JSON.stringify({messages:[{topic:'room-one',event:'hello',payload:{text:'from the REST API'}}]})});
 record('the broadcast REST API answers through the gateway',rest.status===202||rest.status===200,`status ${rest.status}`);
 await first.removeAllChannels();await second.removeAllChannels();
 if(checks.some(row=>!row.ok)){
  // What Realtime itself said, before turning it off removes its container. It never logs secrets.
  const logs=Bun.spawnSync(['docker','logs','--tail','120',`sbarbase-durable-${runtime}-realtime`]);
  console.log(`== sbarbase-durable-${runtime}-realtime\n${logs.stdout.toString()}${logs.stderr.toString()}`);
 }

 const off=await call('PUT',path,{enabled:false});
 record('the owner turns Realtime off',off.status===202,`status ${off.status}`);
 const offUntil=Date.now()+3*60_000;
 while(Date.now()<offUntil){state=(await call('GET',path)).json?.data?.state;if(state!=='pending')break;await Bun.sleep(2000);}
 record('Realtime is off again',state==='off',`state ${state}`);
 record('a socket is refused once Realtime is off',await socketRefused(socketUrl+encodeURIComponent(key)));
} catch(error) {
 record('realtime check ran to the end',false,(error as Error).message);
}
await finish();
