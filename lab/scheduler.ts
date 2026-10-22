import {existsSync,readFileSync} from 'node:fs';
import {networkInterfaces} from 'node:os';
import {join} from 'node:path';
import {EnvironmentRunner,bounded} from '../src/scheduler/runner';
import {WireSession} from '../src/scheduler/wire';
import {policy,systemResolver,type Policy} from '../src/scheduler/egress';

/** The scheduler (docs/engineering/SCHEDULED-JOBS.md): one runner per environment whose Scheduled jobs are
 * on, each in a process of its own (`--environment`), under this parent, which is a child of the supervisor
 * (lab/dev.py); the supervisor stops it before an upgrade or rollback and starts it again afterwards. It
 * holds no lock: each environment is reached only through its own `<e>_cron` login, which a write pause, a
 * fence or a restore closes inside PostgreSQL.
 *
 * It reads the published environments (`.lab/upstream/endpoints.json`, its `cron` entries), the write
 * pauses, the database address (`database.json`) and each login's password from the runtime file. */

const ROOT=join(import.meta.dir,'..');
const STATE=process.env.SBARBASE_SCHEDULER_STATE??join(ROOT,'.lab/upstream');
const PRIVATE=process.env.SBARBASE_SCHEDULER_PRIVATE??join(ROOT,'.secrets/upstream');
const RUNTIME=/^e_[a-f0-9]{24}$/;
// The shared database container (lab/durable_runtime.py DB); the live check names its own.
const DB=process.env.SBARBASE_SCHEDULER_DB_CONTAINER??'sbarbase-durable-db';
// Lookups at once per environment. Requests in flight are bounded per environment only (runner.ts
// MAX_REQUESTS), so no environment ever waits for another's turn.
const LOOKUPS=4;

function json(path:string):unknown {try{return JSON.parse(readFileSync(path,'utf8'));}catch{return null;}}

function wanted():Set<string> {
 const endpoints=json(join(STATE,'endpoints.json')) as Record<string,{cron?:unknown}>|null;
 const out=new Set<string>();
 if(!endpoints||typeof endpoints!=='object')return out;
 for(const [runtime,entry] of Object.entries(endpoints))
  if(RUNTIME.test(runtime)&&entry&&typeof entry==='object'&&entry.cron&&!existsSync(join(STATE,'write-pauses',runtime+'.json')))out.add(runtime);
 return out;
}

function connection(runtime:string):{hostname:string;port:number;password:string}|null {
 const database=json(join(STATE,'database.json')) as {host?:unknown;port?:unknown}|null;
 const values=json(join(PRIVATE,'runtime.json')) as {environments?:Record<string,{cron?:unknown}>}|null;
 const password=values?.environments?.[runtime]?.cron;
 if(typeof database?.host!=='string'||typeof database.port!=='number'||typeof password!=='string'||!/^[0-9a-f]{64}$/.test(password))return null;
 return {hostname:database.host,port:database.port,password};
}

async function dockerSubnets():Promise<string[]|null> {
 try {
  const list=Bun.spawn(['docker','network','ls','-q'],{stdout:'pipe',stderr:'ignore'});
  const ids=(await new Response(list.stdout).text()).split(/\s+/).filter(Boolean);
  if(await list.exited!==0)return null;
  if(!ids.length)return [];
  const inspect=Bun.spawn(['docker','network','inspect','--format','{{range .IPAM.Config}}{{.Subnet}} {{end}}',...ids],{stdout:'pipe',stderr:'ignore'});
  const text=await new Response(inspect.stdout).text();
  if(await inspect.exited!==0)return null;
  return text.split(/\s+/).filter(Boolean);
 } catch {return null;}
}

let rules:Policy|null=null;
let subnets:string[]|null=null;
async function refresh(){
 // A Docker answer this time or an earlier one; without any, no request is performed (fail closed).
 subnets=await dockerSubnets()??subnets;
 if(subnets===null){rules=null;return;}
 const addresses=Object.values(networkInterfaces()).flat().filter(Boolean).map(item=>item!.address);
 const ports=(process.env.SBARBASE_SCHEDULER_PORTS??'').split(',').map(value=>Number(value.trim())).filter(value=>Number.isInteger(value)&&value>0&&value<65536);
 rules=policy({ports:ports.length?[80,443,...ports]:[80,443],publicUrl:(process.env.SBARBASE_PUBLIC_URL??'').trim()||'http://localhost',addresses,subnets});
}

// A line may carry text tenant code chose (a server's error message): control characters are written out, so no
// line can pass for another environment's.
const log=(line:string)=>console.error(`Scheduler: ${line.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))}`);
let stopping=false;

function runner(runtime:string):EnvironmentRunner|null {
 if(!connection(runtime))return null;
 const openRun=async(application:string)=>{
  const details=connection(runtime);
  if(!details)throw new Error('The scheduler login is not configured');
  return WireSession.open({host:details.hostname,port:details.port,user:`${runtime}_cron`,database:runtime,password:details.password,application});
 };
 // The last way to end a run whose job locked the scheduler login out and ended its sessions: as the superuser,
 // through the database container, as the platform's own scripts run SQL, bounded to 10 s.
 const lastResort=async(pid:number)=>{
  if(!Number.isSafeInteger(pid)||pid<=0)return false;
  const child=Bun.spawn(['docker','exec',DB,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres','-c',
   `SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(pid)) FROM pg_catalog.pg_stat_activity WHERE pid = ${pid} AND usename = '${runtime}_cron'`],
   {stdout:'pipe',stderr:'ignore'});
  const timer=setTimeout(()=>child.kill(),10_000);
  try{return await child.exited===0&&(await new Response(child.stdout).text()).trim()==='1';}
  finally{clearTimeout(timer);}
 };
 return new EnvironmentRunner(runtime,openRun,()=>rules,bounded(systemResolver(),LOOKUPS),log,Date.now,undefined,undefined,lastResort).start();
}

// One process per environment (`--environment e_...`), started and stopped by this parent, which never connects
// to a database itself: whatever one environment's tenant code makes its runner do (a key derivation, a burst of
// work, a crash) stays in that environment's process. Each child is bound to this process (lab/parent_bound.py)
// and gets SIGTERM if the parent dies.
const own=process.argv.indexOf('--environment');
if(own>=0){
 const runtime=process.argv[own+1]??'';
 if(!RUNTIME.test(runtime))process.exit(2);
 await refresh();
 const current=runner(runtime);
 if(!current){log(`${runtime}: the scheduler login is not configured`);process.exit(1);}
 const shutdown=async()=>{
  if(stopping)return;
  stopping=true;
  await Promise.race([current.stop(),Bun.sleep(15_000)]);
  process.exit(0);
 };
 process.on('SIGTERM',()=>void shutdown());
 process.on('SIGINT',()=>void shutdown());
 while(!stopping){await Bun.sleep(10_000);if(!stopping)await refresh().catch(()=>{});}
} else {
 type Child={process:ReturnType<typeof Bun.spawn>;exited:boolean};
 const children=new Map<string,Child>();
 const after=new Map<string,number>();
 const start=(runtime:string)=>{
  const child:Child={exited:false,process:Bun.spawn(['/usr/bin/python3',join(ROOT,'lab/parent_bound.py'),String(process.pid),'bun',import.meta.path,'--environment',runtime],
   {cwd:ROOT,stdio:['ignore','inherit','inherit'],env:process.env})};
  void child.process.exited.then(()=>{child.exited=true;});
  children.set(runtime,child);
 };
 const end=async(child:Child)=>{
  child.process.kill('SIGTERM');
  if(await Promise.race([child.process.exited.then(()=>true),Bun.sleep(17_000).then(()=>false)]))return;
  child.process.kill('SIGKILL');
  await child.process.exited;
 };
 const reconcile=async()=>{
  const want=wanted();
  for(const [runtime,child] of children){
   if(!want.has(runtime)){children.delete(runtime);await end(child);}
   else if(child.exited){children.delete(runtime);after.set(runtime,Date.now()+5000);log(`${runtime}: its runner exited; starting it again shortly`);}
  }
  for(const runtime of want)if(!children.has(runtime)&&Date.now()>=(after.get(runtime)??0)&&connection(runtime))start(runtime);
 };
 const shutdown=async()=>{
  if(stopping)return;
  stopping=true;
  await Promise.all([...children.values()].map(end));
  process.exit(0);
 };
 process.on('SIGTERM',()=>void shutdown());
 process.on('SIGINT',()=>void shutdown());
 while(!stopping){
  try{await reconcile();}
  catch(error){log((error as Error).message??'reconcile failed');}
  await Bun.sleep(2000);
 }
}
