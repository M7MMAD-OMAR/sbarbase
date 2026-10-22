// Live check of scheduled jobs and outgoing HTTP (lab/scheduled_jobs.py, src/scheduler/, lab/scheduler.ts,
// docs/engineering/SCHEDULED-JOBS.md).
//
// Starts one disposable Supabase Postgres container from the pinned image, with two environment databases:
// `a` has a developer role, `b` has none. The `cron` and `net` schemas, the scheduler login and the grants
// come from the runtime's own SQL (lab/scheduled_jobs.py). It checks that the plpgsql schedule check and
// the TypeScript parser agree; that pg_cron's functions behave as pg_cron's for the developer and refuse
// another database, another user and the 101st job; that app roles reach only `net.http_*` and no role
// reaches `cron` without the grant; that the scheduler runs a job as the developer, with its search path,
// records it as pg_cron does, gains nothing by RESET ROLE, ends a run at its deadline, never runs a job
// twice at once, marks lost runs failed, and says so when there is no developer; that requests to
// loopback, private, metadata and the database's own address are refused and a public one is answered;
// that the scheduler process follows endpoints.json and a write pause and records the runs it ends on
// SIGTERM; that turning off closes the login, takes back its grants, keeps the jobs, and leaves a dump
// that names no such role; that neither a tenant holding the tables nor a job holding the login's role row
// holds up the platform's SQL; that a queued POST reaches a server with its body; and that an archive
// replays into a stage database of another name through the in-place restore's planner. Removes its container at the end. Needs Docker, about 1 GiB, and for one check
// outbound HTTPS (example.com); without it that check fails and says why.
//
//   bun lab/scheduled-jobs-check.ts [evidence.json]
//
// Prints one JSON object: {"total", "failed", "skipped", "checks"}.
import {spawn,spawnSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {createServer} from 'node:http';
import {parseSchedule} from '../src/scheduler/cron';
import {EnvironmentRunner,bounded} from '../src/scheduler/runner';
import {WireSession} from '../src/scheduler/wire';
import {perform,policy,refusal,systemResolver,type Policy} from '../src/scheduler/egress';

const pins=JSON.parse(readFileSync(new URL('./distro-image.lock.json',import.meta.url),'utf8'));
const image=pins.digests[0] as string;
const evidence=process.argv[2];
const name='sbarbase-scheduled-jobs-check-'+randomBytes(3).toString('hex');
const a='e_'+randomBytes(12).toString('hex'),b='e_'+randomBytes(12).toString('hex');
const developerPassword=randomBytes(18).toString('base64url');
const cronPasswords={[a]:randomBytes(32).toString('hex'),[b]:randomBytes(32).toString('hex')};
const results:{check:string;result:'pass'|'fail';detail?:string}[]=[];
const docker=(...args:string[])=>{const out=spawnSync('docker',args,{encoding:'utf8'});if(out.status)throw new Error(out.stderr.slice(0,500));return out.stdout.trim();};
const run=(user:string,database:string,sql:string,password?:string)=>spawnSync('docker',['exec','-i',...(password?['-e','PGPASSWORD='+password]:[]),name,
 'psql','-X','-qAt','-v','ON_ERROR_STOP=1',...(password?['-h','127.0.0.1']:[]),'-U',user,'-d',database],{input:sql,encoding:'utf8'});
const superuser=(database:string,sql:string)=>{const out=run('supabase_admin',database,sql);if(out.status)throw new Error(out.stderr.slice(0,800));return out.stdout.trim();};
const developer=(sql:string)=>run(`${a}_developer`,a,sql,developerPassword);
const check=async(label:string,work:()=>boolean|string|Promise<boolean|string>)=>{
 try{const value=await work();results.push(value===true?{check:label,result:'pass'}:{check:label,result:'fail',detail:String(value)});}
 catch(error){results.push({check:label,result:'fail',detail:(error as Error).message.slice(0,300)});}
};
const works=(sql:string)=>{const out=developer(sql);return out.status===0||out.stderr.slice(0,300);};
const refused=(sql:string,expect:RegExp,as=developer)=>{const out=as(sql);return out.status!==0&&expect.test(out.stderr)||`status ${out.status}: ${out.stderr.slice(0,300)}`;};
const python=(code:string)=>{
 const out=spawnSync('/usr/bin/python3',['-c',`import sys;sys.path.insert(0,'lab');import scheduled_jobs as s\n${code}`],{encoding:'utf8'});
 if(out.status)throw new Error(out.stderr.slice(0,800));
 return out.stdout;
};
const sqlFor=(builder:string,runtime:string,extra='')=>python(`print(s.${builder}(${JSON.stringify(runtime)}${extra}))`);
const until=async(test:()=>boolean|Promise<boolean>,ms:number)=>{const end=Date.now()+ms;while(Date.now()<end){if(await test())return true;await Bun.sleep(250);}return false;};
let host='127.0.0.1',port=0,databaseAddress='';

function environment(runtime:string,withDeveloper:boolean){
 superuser('postgres',`CREATE DATABASE ${runtime};`);
 if(withDeveloper){
  superuser('postgres',`CREATE ROLE ${runtime}_developer LOGIN INHERIT BYPASSRLS PASSWORD '${developerPassword}';
GRANT CONNECT, TEMPORARY ON DATABASE ${runtime} TO ${runtime}_developer;
GRANT anon, authenticated, service_role TO ${runtime}_developer;
ALTER ROLE ${runtime}_developer IN DATABASE ${runtime} SET search_path TO public, extensions;`);
  superuser(runtime,`GRANT USAGE, CREATE ON SCHEMA public TO ${runtime}_developer;`);
 }
 superuser('postgres',sqlFor('login_sql',runtime,`,${JSON.stringify(cronPasswords[runtime])}`));
 superuser(runtime,sqlFor('shim_sql',runtime));
 superuser('postgres',sqlFor('open_sql',runtime));
 superuser(runtime,sqlFor('grants_sql',runtime));
}

const rules=()=>policy({publicUrl:'https://api.example.invalid',addresses:[databaseAddress],subnets:[]});
const openRun=(runtime:string)=>(application:string)=>WireSession.open({host,port,user:`${runtime}_cron`,database:runtime,password:cronPasswords[runtime]!,application});
// The platform's last way to end a run, as lab/scheduler.ts does it: the superuser through the container.
const lastResort=(runtime:string)=>async(pid:number)=>run('supabase_admin','postgres',
 `SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE pid = ${pid} AND usename = '${runtime}_cron'`).stdout.trim()==='1';
const runner=(runtime:string,runMs=600_000)=>new EnvironmentRunner(runtime,openRun(runtime),rules,bounded(systemResolver(),4),()=>{},Date.now,runMs,undefined,lastResort(runtime)).start();
const timed=(work:()=>void)=>{const begun=Date.now();work();return Date.now()-begun;};

const CORPUS=['* * * * *','*/5 * * * *','0 3 * * *','5 4 * * sun','0 0 1,15 * *','0-59/10 1-3 * * *','30 12 * jan,JUL *','0 0 * * mon-fri','0 0 * * 7',
 '0 0 $ * *','0 0 * Jan-Mar Mon','  1 2 3 4 5  ','0 0 * * */2','0 0 1-31/2 * *','1 second','30 seconds','59 SECONDS',' 2 seconds ','5seconds','0005 seconds',
 '\t1\t2\t3\t4\t5\t','@hourly','@daily','@midnight','@weekly','@monthly','@yearly','@annually',
 '','*','* * * *','60 * * * *','* 24 * * *','* * 0 * *','* * 32 * *','* * * 13 *','* * * * 8','1/20 * * * *','5-64/30 * * * *','*/0 * * * *',
 '* * * foo *','* * * * funday','@reboot','@restart','@never','@HOURLY','5x * * * *','* * * * * extra','5; * * * *','0 0 $5 * *','0 0 1,$ * *',
 '0 seconds','60 seconds','5 secondsx','5 sec','-1 seconds','100 seconds','1,,2 * * * *','1, * * * *','1-2-3 * * * *','0000000001 * * * *','*/1000000000 * * * *'];

const started=Date.now();
try {
 docker('run','-d','--rm','--name',name,'--label','io.sbarbase.owner=scheduled-jobs-check','-e','POSTGRES_PASSWORD='+randomBytes(16).toString('hex'),
  '--memory','1g','-p','127.0.0.1::5432',image);
 for(let i=0,streak=0;streak<10;i++){streak=run('supabase_admin','postgres','SELECT 1').status===0?streak+1:0;if(i>180)throw new Error('PostgreSQL did not start');await Bun.sleep(1000);}
 port=Number(docker('port',name,'5432/tcp').split('\n')[0]!.split(':').pop());
 databaseAddress=docker('inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',name);
 environment(a,true);environment(b,false);

 await check('the plpgsql schedule check and the TypeScript parser agree on every schedule in the corpus',()=>{
  const values=CORPUS.map(item=>`(${CORPUS.indexOf(item)}, ${"'"+item.replaceAll("'","''")+"'"})`).join(',');
  const rows=superuser(a,`SELECT i, cron.schedule_valid(s) FROM (VALUES ${values}) v(i, s) ORDER BY i;`).split('\n');
  const differ=rows.map(line=>line.split('|')).filter(([i,ok])=>(ok==='t')!==(parseSchedule(CORPUS[Number(i)])!==null)).map(([i,ok])=>`${JSON.stringify(CORPUS[Number(i)])}: sql ${ok}`);
  return differ.length===0||differ.join('; ');
 });
 await check('cron.schedule by name inserts once, replaces by name, and records the developer as user',()=>{
  const first=developer(`SELECT cron.schedule('nightly', '0 3 * * *', 'select 1');`).stdout.trim();
  const second=developer(`SELECT cron.schedule('nightly', '0 4 * * *', 'select 2');`).stdout.trim();
  const row=developer(`SELECT schedule||'|'||command||'|'||username||'|'||database||'|'||active FROM cron.job WHERE jobname = 'nightly';`).stdout.trim();
  return first===second&&row===`0 4 * * *|select 2|${a}_developer|${a}|true`||`${first} ${second} ${row}`;
 });
 await check('an invalid schedule is refused with pg_cron\'s message, at the function and at the table',()=>{
  const fn=refused(`SELECT cron.schedule('x', '61 * * * *', 'select 1');`,/invalid schedule: 61 \* \* \* \*/);
  const table=refused(`INSERT INTO cron.job (schedule, command) VALUES ('nope', 'select 1');`,/job_schedule_valid/);
  return fn===true&&table===true||`${fn} ${table}`;
 });
 await check('scheduling into another database or as another user is refused',()=>{
  const other=refused(`SELECT cron.schedule_in_database('x', '* * * * *', 'select 1', 'postgres');`,/own database only/);
  const user=refused(`SELECT cron.schedule_in_database('x', '* * * * *', 'select 1', '${a}', 'postgres');`,/another role/);
  const alter=refused(`SELECT cron.alter_job((SELECT jobid FROM cron.job WHERE jobname='nightly'), username := 'postgres');`,/alter username/);
  return [other,user,alter].every(item=>item===true)||[other,user,alter].join(' | ');
 });
 await check('alter_job and unschedule work on the caller\'s own jobs and say so for others',()=>{
  const id=developer(`SELECT jobid FROM cron.job WHERE jobname='nightly';`).stdout.trim();
  const altered=works(`SELECT cron.alter_job(${id}, schedule := '*/10 * * * *', active := false);`);
  const read=developer(`SELECT schedule||'|'||active FROM cron.job WHERE jobid=${id};`).stdout.trim();
  const missing=refused(`SELECT cron.unschedule(999999);`,/could not find valid entry for job 999999/);
  const byName=developer(`SELECT cron.unschedule('nightly');`).stdout.trim();
  return altered===true&&read==='*/10 * * * *|false'&&missing===true&&byName==='t'||`${altered} ${read} ${missing} ${byName}`;
 });
 await check('the 101st job is refused',()=>{
  works(`SELECT cron.schedule('j'||i, '0 0 1 1 *', 'select 1') FROM generate_series(1, 100) i;`);
  const over=refused(`SELECT cron.schedule('j101', '0 0 1 1 *', 'select 1');`,/at most 100 scheduled jobs/);
  works(`DELETE FROM cron.job;`);
  return over;
 });
 await check('anon, authenticated and service_role reach net.http_* but no role reaches cron without the grant',()=>{
  const as=(role:string)=>(sql:string)=>run('supabase_admin',a,`SET ROLE ${role}; ${sql}`);
  const results=['anon','authenticated','service_role'].map(role=>refused(`SELECT cron.schedule('x', '* * * * *', 'select 1');`,/permission denied for schema cron/,as(role)));
  const queued=as('anon')(`SELECT net.http_get('https://example.com/from-anon') > 0;`);
  const read=refused(`SELECT * FROM net._http_response;`,/permission denied/,as('authenticated'));
  const login=refused(`INSERT INTO cron.job (schedule, command) VALUES ('* * * * *', 'select 1');`,/permission denied for table job/,as(`${a}_cron`));
  superuser(a,`DELETE FROM net.http_request_queue;`);
  return results.every(item=>item===true)&&queued.stdout.trim()==='t'&&read===true&&login===true||[...results,queued.stdout+queued.stderr,read,login].join(' | ');
 });
 await check('net.http_post keeps pg_net\'s Content-Type rule and encodes params as curl does',()=>{
  const wrong=refused(`SELECT net.http_post('https://example.com', headers := '{"Content-Type":"text/plain"}');`,/Content-Type header must be "application\/json"/);
  works(`SELECT net.http_get('https://example.com/p?x=1', params := '{"a b":"c&d/é"}');`);
  const url=developer(`SELECT url FROM net.http_request_queue ORDER BY id DESC LIMIT 1;`).stdout.trim();
  superuser(a,`DELETE FROM net.http_request_queue;`);
  return wrong===true&&url==='https://example.com/p?x=1&a%20b=c%26d%2F%C3%A9'||`${wrong} ${url}`;
 });

 // A run left as running by a scheduler that stopped is marked failed at the next start, as pg_cron does.
 superuser(a,`INSERT INTO cron.job_run_details (jobid, status, start_time) VALUES (0, 'running', now());`);
 superuser(a,`CREATE TABLE public.seen (who text, path text, at timestamptz DEFAULT now()); GRANT ALL ON public.seen TO ${a}_developer;
CREATE TABLE public.secret (x int); REVOKE ALL ON public.secret FROM PUBLIC;`);
 // History older than 7 days and responses older than 6 hours, which the runner's first tick prunes.
 superuser(a,`INSERT INTO cron.job_run_details (jobid, status, start_time, end_time) VALUES (0, 'succeeded', now() - interval '9 days', now() - interval '8 days');
INSERT INTO net._http_response (id, status_code, created) VALUES (-1, 200, now() - interval '7 hours'), (-2, 200, now() - interval '1 hour');`);
 const one=runner(a,3000);
 await check('history older than 7 days and responses older than 6 hours are pruned, and newer ones kept',async()=>{
  const pruned=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE end_time < now() - interval '7 days';`)==='0'
   &&superuser(a,`SELECT string_agg(id::text, ',' ORDER BY id) FROM net._http_response WHERE id < 0;`)==='-2',10000);
  return pruned||superuser(a,`SELECT string_agg(id::text, ',') FROM net._http_response WHERE id < 0;`);
 });
 await check('a run lost by an earlier scheduler is marked failed with "server restarted"',async()=>
  await until(()=>superuser(a,`SELECT status||'|'||return_message FROM cron.job_run_details WHERE jobid = 0;`)==='failed|server restarted',8000)||'not marked');
 works(`SELECT cron.schedule('as-developer', '1 second', $$insert into public.seen (who, path) values (current_user, current_setting('search_path'))$$);`);
 await check('a job runs as the developer, with its search path, and records succeeded with the command tag and its backend',async()=>{
  const ran=await until(()=>superuser(a,`SELECT count(*) FROM public.seen;`)!=='0',20000);
  const row=superuser(a,`SELECT who||'|'||path FROM public.seen LIMIT 1;`);
  const detail=superuser(a,`SELECT status||'|'||return_message||'|'||(job_pid IS NOT NULL)||'|'||username FROM cron.job_run_details
   WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='as-developer') AND status='succeeded' LIMIT 1;`);
  return ran&&row===`${a}_developer|public, extensions`&&detail===`succeeded|INSERT 0 1|true|${a}_developer`||`${ran} ${row} ${detail} runs: `+superuser(a,`SELECT string_agg(jobid||':'||status||':'||coalesce(return_message,''), ' ; ') FROM cron.job_run_details;`);
 });
 works(`SELECT cron.unschedule('as-developer');`);
 works(`SELECT cron.schedule('escape', '1 second', $$reset role; insert into public.secret values (1)$$);`);
 await check('a job that runs RESET ROLE falls back to the scheduler login and gains nothing',async()=>{
  const failed=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='escape') AND status='failed';`)!=='0',8000);
  const message=superuser(a,`SELECT return_message FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='escape') AND status='failed' LIMIT 1;`);
  return failed&&/permission denied for table secret/.test(message)&&superuser(a,'SELECT count(*) FROM public.secret;')==='0'||`${failed} ${message}`;
 });
 works(`SELECT cron.unschedule('escape');`);
 works(`SELECT cron.schedule('many', '1 second', $$create temp table t1 (x int); insert into t1 values (1), (2); select * from t1$$);`);
 await check('a command runs as one simple query, several statements together, and records the last tag',async()=>{
  const done=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='many') AND status <> 'running' AND status <> 'starting';`)!=='0',8000);
  const row=superuser(a,`SELECT status||'|'||return_message FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='many') ORDER BY runid LIMIT 1;`);
  return done&&row==='succeeded|2 rows'||`${done} ${row}`;
 });
 works(`SELECT cron.unschedule('many');`);
 works(`SELECT cron.schedule('vacuum', '1 second', 'vacuum public.seen');`);
 await check('VACUUM alone works, as with pg_cron',async()=>{
  const done=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='vacuum') AND status IN ('succeeded','failed');`)!=='0',8000);
  const row=superuser(a,`SELECT status||'|'||return_message FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='vacuum') ORDER BY runid LIMIT 1;`);
  return done&&row==='succeeded|VACUUM'||`${done} ${row}`;
 });
 works(`SELECT cron.unschedule('vacuum');`);
 works(`SELECT cron.schedule('slow', '1 second', $$set statement_timeout = 0; select pg_sleep(60)$$);`);
 await check('a run that ignores its own timeout is ended at the deadline by the scheduler and its backend is gone',async()=>{
  const ended=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='slow') AND status='failed';`)!=='0',12000);
  const row=superuser(a,`SELECT return_message||'|'||job_pid FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='slow') AND status='failed' ORDER BY runid LIMIT 1;`);
  const pid=row.split('|')[1];
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE pid = ${Number(pid)||0};`)==='0',5000);
  return ended&&/exceeded the 3 second limit/.test(row)&&gone||`${ended} ${row} ${gone}`;
 });
 await check('a job is never run twice at once',async()=>{
  await Bun.sleep(4000);
  const overlaps=superuser(a,`SELECT count(*) FROM cron.job_run_details x JOIN cron.job_run_details y ON x.jobid = y.jobid AND x.runid < y.runid
   WHERE x.jobid = (SELECT jobid FROM cron.job WHERE jobname='slow') AND y.start_time < coalesce(x.end_time, now());`);
  const total=superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='slow');`);
  return overlaps==='0'&&Number(total)>=2||`overlaps ${overlaps}, runs ${total}`;
 });
 works(`SELECT cron.unschedule('slow');`);
 works(`SELECT cron.schedule('changing', '1 second', 'select pg_sleep(2)');`);
 await check('a job whose schedule changes while it runs keeps running on the new schedule',async()=>{
  await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='changing') AND status = 'running';`)==='1',8000);
  works(`SELECT cron.schedule('changing', '2 seconds', 'select pg_sleep(2)');`);
  const before=Number(superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='changing');`));
  const more=await until(()=>Number(superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='changing') AND status = 'succeeded';`))>=2,15000);
  return more||`runs before the change ${before}, after: `+superuser(a,`SELECT string_agg(status, ',') FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='changing');`);
 });
 works(`SELECT cron.unschedule('changing');`);
 works(`SELECT cron.schedule('blocker', '1 second', $$set statement_timeout = 0; lock table cron.job_run_details in access exclusive mode; select pg_sleep(60)$$);`);
 await check('a run is ended at its deadline even while it keeps the bookkeeping session failing',async()=>{
  const locked=await until(()=>superuser(a,`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%lock table cron.job_run_details%' AND state = 'active';`)==='1',10000);
  works(`SELECT cron.unschedule('blocker');`);
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%lock table cron.job_run_details%';`)==='0',15000);
  return locked&&gone||`locked ${locked}, gone ${gone}`;
 });
 works(`SELECT cron.schedule('stream', '1 second', $$set statement_timeout = 0; select generate_series(1, 400000000)$$);`);
 await check('a job streaming hundreds of millions of small rows is ended at its deadline and never holds up the process\'s event loop',async()=>{
  let worst=0,last=performance.now();
  const sampler=setInterval(()=>{const now=performance.now();worst=Math.max(worst,now-last-20);last=now;},20);
  const ended=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='stream') AND return_message LIKE 'job exceeded%';`)!=='0',20000);
  clearInterval(sampler);
  works(`SELECT cron.unschedule('stream');`);
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE query LIKE '%generate_series(1, 400000000)%' AND usename = '${a}_cron';`)==='0',10000);
  return ended&&gone&&worst<200||`ended ${ended}, gone ${gone}, worst event loop delay ${Math.round(worst)} ms`;
 });
 works(`SELECT cron.schedule('copy-out', '1 second', $$set statement_timeout = 0; copy (select generate_series(1, 100000000000)) to stdout$$);`);
 await check('a job streaming COPY TO STDOUT is refused with "COPY not supported", as pg_cron records it, and its backend ends',async()=>{
  const refused=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='copy-out') AND status = 'failed' AND return_message = 'COPY not supported';`)!=='0',15000);
  works(`SELECT cron.unschedule('copy-out');`);
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE query LIKE '%copy (select generate_series(1, 100000000000))%' AND usename = '${a}_cron';`)==='0',15000);
  return refused&&gone||`refused ${refused}, gone ${gone}`;
 });
 works(`SELECT cron.schedule('huge', '2 seconds', $$select repeat('x', 20000000) from generate_series(1, 50)$$);`);
 await check('a job returning 1 GB of rows never holds it: the process peaks below the result\'s size and is back within 128 MiB once collected, and "50 rows" is recorded',async()=>{
  Bun.gc(true);
  const base=process.memoryUsage().rss;let peak=base;
  const sampler=setInterval(()=>{peak=Math.max(peak,process.memoryUsage().rss);},50);
  const done=await until(()=>Number(superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='huge') AND status = 'succeeded';`))>=1,40000);
  clearInterval(sampler);
  const row=superuser(a,`SELECT return_message FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='huge') AND status = 'succeeded' LIMIT 1;`);
  works(`SELECT cron.unschedule('huge');`);
  const grew=Math.round((peak-base)/1048576);
  // Measured once no run of the job streams any more.
  works(`UPDATE cron.job SET active = false WHERE jobname = 'huge';`);
  await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname='huge') AND status IN ('starting','running');`)==='0',30000);
  Bun.gc(true);await Bun.sleep(500);Bun.gc(true);
  const kept=Math.round((process.memoryUsage().rss-base)/1048576);
  return done&&row==='50 rows'&&grew<1000&&kept<128||`done ${done}, ${row}, peak growth ${grew} MiB, kept ${kept} MiB`;
 });

 // Outgoing requests.
 works(`SELECT net.http_get('http://127.0.0.1:${port}/');
SELECT net.http_get('http://169.254.169.254/latest/meta-data/');
SELECT net.http_get('http://${databaseAddress}:5432/');
SELECT net.http_post('http://[::ffff:10.0.0.1]/', '{}');
SELECT net.http_get('http://localhost:80/');
SELECT net.http_get('ftp://example.com/');
SELECT net.http_get('https://example.com/');`);
 await check('requests to loopback, metadata, private, the database\'s own address and other schemes are refused, and a public one is answered',async()=>{
  await until(()=>superuser(a,`SELECT count(*) FROM net._http_response WHERE id > 0;`)==='7',30000);
  const rows=superuser(a,`SELECT id||'|'||coalesce(status_code::text,'')||'|'||coalesce(error_msg,'') FROM net._http_response WHERE id > 0 ORDER BY id;`).split('\n');
  const refusedRows=rows.slice(0,6).filter(row=>/Refused by the server's outgoing request policy/.test(row));
  const answered=rows[6]??'';
  return refusedRows.length===6&&/\|200\|$/.test(answered)||rows.join(' ; ');
 });
 // A job that runs RESET ROLE may rewrite the scheduler login's password with any SCRAM iteration count (the security
 // review reproduced it from a job). Set here directly, at 20 million, where Bun's driver would hold the event loop
 // about 1.8 s per sign-in; then the bookkeeping session is ended, so the runner signs in again and again.
 await check('a scheduler login password rewritten with 20 million SCRAM iterations does not stall the scheduler process',async()=>{
  await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE status IN ('starting','running');`)==='0',30000);
  Bun.gc(true);
  superuser('postgres',`SET scram_iterations = 20000000; ALTER ROLE ${a}_cron PASSWORD 'tenant-chosen';`);
  let worst=0,last=performance.now();
  const sampler=setInterval(()=>{const now=performance.now();worst=Math.max(worst,now-last-20);last=now;},20);
  for(let i=0;i<3;i++){superuser('postgres',`SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '${a}_cron';`);await Bun.sleep(6000);}
  clearInterval(sampler);
  superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
  superuser('postgres',sqlFor('open_sql',a));
  const reset=superuser('postgres',`SELECT rolpassword LIKE 'SCRAM-SHA-256$4096:%' FROM pg_authid WHERE rolname = '${a}_cron';`);
  return worst<100&&reset==='t'||`worst event loop delay ${Math.round(worst)} ms, reset ${reset}`;
 });
 await one.stop();

 // A request body and headers queued by net.http_post reach the server as sent, read back as bytea through
 // Bun's client, and the answer is recorded. The runner gets the policy's own check with loopback admitted
 // for this one test server; the policy itself refuses loopback (above).
 const echo=createServer((incoming,outgoing)=>{const chunks:Buffer[]=[];incoming.on('data',chunk=>chunks.push(chunk));
  incoming.on('end',()=>{outgoing.writeHead(201,{'Content-Type':'application/json','X-Seen':String(incoming.headers['x-token']??'')});
   outgoing.end(JSON.stringify({method:incoming.method,host:incoming.headers.host,type:incoming.headers['content-type'],body:Buffer.concat(chunks).toString()}));});});
 await new Promise<void>(done=>echo.listen(0,'127.0.0.1',done));
 const echoPort=(echo.address() as {port:number}).port;
 const admitted=(address:string,rules:Policy,exact?:boolean)=>address==='127.0.0.1'?null:refusal(address,rules,exact);
 const local=new EnvironmentRunner(a,openRun(a),()=>policy({ports:[echoPort]}),async host=>host==='echo.test'?['127.0.0.1']:[],()=>{},Date.now,600_000,
  (request,rules,resolve)=>perform(request,rules,resolve,admitted)).start();
 try {
  const id=developer(`SELECT net.http_post('http://echo.test:${echoPort}/hook', body := '{"rows": [1, 2], "name": "é"}'::jsonb, params := '{"q": "a b"}', headers := '{"Content-Type": "application/json", "X-Token": "t-1"}');`).stdout.trim();
  await check('a queued POST reaches the server with its body, headers and parameters, and the answer is recorded by request id',async()=>{
   const answered=await until(()=>superuser(a,`SELECT count(*) FROM net._http_response WHERE id = ${Number(id)};`)==='1',15000);
   const row=superuser(a,`SELECT status_code||'|'||content_type||'|'||(headers->>'X-Seen')||'|'||content FROM net._http_response WHERE id = ${Number(id)};`);
   const [status,type,seen,...rest]=row.split('|');
   const body=JSON.parse(rest.join('|')||'{}');
   return answered&&status==='201'&&type==='application/json'&&seen==='t-1'&&body.method==='POST'&&body.host===`echo.test:${echoPort}`
    &&body.type==='application/json'&&JSON.stringify(JSON.parse(body.body))===JSON.stringify({name:'é',rows:[1,2]})||`${answered} ${row}`;
  });
 } finally {await local.stop();echo.close();}

 // A job acting as the scheduler login (RESET ROLE) changes that login's own password in a transaction it keeps
 // open, which holds the login's role row; turning off, turning on and a start must not wait for it.
 const holder=runner(a);
 works(`SELECT cron.schedule('hold-role', '1 second', $$reset role; set statement_timeout = 0; alter role ${a}_cron password 'held'; select pg_sleep(120)$$);`);
 await check('a job holding the scheduler login\'s role row does not hold up turning off, turning on or a start',async()=>{
  const running=await until(()=>superuser(a,`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(120)%' AND state = 'active';`)==='1',15000);
  const off=timed(()=>{superuser('postgres',sqlFor('close_sql',a));superuser(a,sqlFor('revoke_sql',a));});
  const on=timed(()=>{superuser('postgres',sqlFor('close_sql',a));superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
   superuser(a,sqlFor('shim_sql',a));superuser('postgres',sqlFor('open_sql',a));superuser(a,sqlFor('grants_sql',a));});
  const left=superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(120)%';`);
  works(`SELECT cron.unschedule('hold-role');`);
  return running&&off<5000&&on<5000&&left==='0'||`running ${running}, off ${off} ms, on ${on} ms, left ${left}`;
 });
 await holder.stop();
 // A job that changes the scheduler login's password (after RESET ROLE) and then runs on: the deadline kill
 // cannot sign in, so the bookkeeping session, signed in already, ends the backend.
 const locked=runner(a,4000);
 works(`SELECT cron.schedule('lock-out', '1 second', $$reset role; alter role ${a}_cron password 'tenant-chosen'; commit; set statement_timeout = 0; select pg_sleep(120)$$);`);
 await check('a job that locks the scheduler login out of new sign-ins is still ended at its deadline',async()=>{
  const sleeping=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(120)%' AND state = 'active';`)==='1',15000);
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(120)%';`)==='0',20000);
  return sleeping&&gone||`sleeping ${sleeping}, gone ${gone}`;
 });
 works(`SELECT cron.unschedule('lock-out');`);
 superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
 superuser('postgres',sqlFor('open_sql',a));
 works(`SELECT cron.schedule('escape', '1 second', $$reset role; alter role ${a}_cron password 'tenant-chosen';
  select count(pg_terminate_backend(pid)) from pg_stat_activity where usename = current_user and pid <> pg_backend_pid(); commit;
  set statement_timeout = 0; select pg_sleep(121)$$);`);
 await check('a job that locks the scheduler login out and ends its other sessions is still ended at its deadline, as the superuser',async()=>{
  const sleeping=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(121)%' AND state = 'active';`)==='1',20000);
  const gone=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%pg_sleep(121)%';`)==='0',30000);
  return sleeping&&gone||`sleeping ${sleeping}, gone ${gone}`;
 });
 works(`SELECT cron.unschedule('escape');`);
 await locked.stop();
 superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
 superuser('postgres',sqlFor('open_sql',a));

 await check('without a developer role every run says so and nothing else happens',async()=>{
  superuser(b,`SELECT cron.schedule('x', '1 second', 'select 1');`);
  const other=runner(b);
  const said=await until(()=>superuser(b,`SELECT count(*) FROM cron.job_run_details WHERE return_message LIKE 'The developer role does not exist yet%';`)!=='0',8000);
  await other.stop();
  return said||superuser(b,'SELECT status||return_message FROM cron.job_run_details;');
 });

 // The scheduler process: endpoints.json, a write pause and SIGTERM.
 const state=mkdtempSync(join(tmpdir(),'sbarbase-scheduler-')),secrets=join(state,'private');
 mkdirSync(secrets);mkdirSync(join(state,'write-pauses'));
 writeFileSync(join(state,'database.json'),JSON.stringify({host,port}));
 writeFileSync(join(secrets,'runtime.json'),JSON.stringify({environments:{[a]:{cron:cronPasswords[a]}}}));
 writeFileSync(join(state,'endpoints.json'),JSON.stringify({[a]:{cron:{user:`${a}_cron`}}}));
 works(`DELETE FROM public.seen; SELECT cron.schedule('process', '1 second', $$insert into public.seen (who) values ('process')$$);`);
 const child=spawn('bun',['lab/scheduler.ts'],{env:{...process.env,SBARBASE_SCHEDULER_STATE:state,SBARBASE_SCHEDULER_PRIVATE:secrets},stdio:['ignore','ignore','pipe']});
 let stderr='';child.stderr.on('data',chunk=>{stderr+=String(chunk);});
 try {
  await check('the scheduler process runs the jobs of an environment endpoints.json lists',async()=>
   await until(()=>superuser(a,`SELECT count(*) FROM public.seen WHERE who='process';`)!=='0',15000)||'no run; '+stderr.slice(0,300));
  await check('a write pause recorded for the environment stops its runs',async()=>{
   writeFileSync(join(state,'write-pauses',a+'.json'),'{}');
   await Bun.sleep(4000);
   const before=superuser(a,`SELECT count(*) FROM public.seen WHERE who='process';`);
   await Bun.sleep(3000);
   const after=superuser(a,`SELECT count(*) FROM public.seen WHERE who='process';`);
   rmSync(join(state,'write-pauses',a+'.json'));
   return before===after||`${before} then ${after}`;
  });
  works(`SELECT cron.unschedule('process'); SELECT cron.schedule('long', '1 second', 'select pg_sleep(30)');`);
  await check('on SIGTERM the scheduler ends the run in flight and records it',async()=>{
   const running=await until(()=>superuser(a,`SELECT count(*) FROM cron.job_run_details WHERE status='running' AND jobid=(SELECT jobid FROM cron.job WHERE jobname='long');`)!=='0',15000);
   child.kill('SIGTERM');
   const exited=await new Promise<boolean>(done=>{const timer=setTimeout(()=>done(false),20000);child.once('exit',()=>{clearTimeout(timer);done(true);});});
   const row=superuser(a,`SELECT status||'|'||return_message FROM cron.job_run_details WHERE jobid=(SELECT jobid FROM cron.job WHERE jobname='long') ORDER BY runid DESC LIMIT 1;`);
   return running&&exited&&row==='failed|stopped for maintenance'||`${running} ${exited} ${row}`;
  });
 } finally {
  child.kill('SIGKILL');
  rmSync(state,{recursive:true,force:true});
 }

 await check('turning off closes the login, takes back its grants and keeps the jobs; a dump then names no such role',()=>{
  superuser('postgres',sqlFor('close_sql',a));
  superuser(a,sqlFor('revoke_sql',a));
  const login=run(`${a}_cron`,a,'SELECT 1;',cronPasswords[a]);
  const jobs=superuser(a,'SELECT count(*) FROM cron.job;');
  const dump=spawnSync('docker',['exec',name,'pg_dump','-U','supabase_admin','-d',a,'--schema-only'],{encoding:'utf8'});
  return login.status!==0&&/not permitted to log in/.test(login.stderr)&&jobs==='1'&&dump.status===0&&!dump.stdout.includes(`${a}_cron`)
   ||`${login.status} ${login.stderr.slice(0,120)} jobs ${jobs} dump ${dump.status} names ${dump.stdout.includes(`${a}_cron`)}`;
 });
 await check('turning on again resets the role settings a job wrote for the login and opens it',()=>{
  superuser('postgres',`ALTER ROLE ${a}_cron SET search_path = public; ALTER ROLE ${a}_cron IN DATABASE ${a} SET statement_timeout = 1;`);
  superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
  superuser('postgres',sqlFor('open_sql',a));
  superuser(a,sqlFor('grants_sql',a));
  const settings=superuser('postgres',`SELECT count(*) FROM pg_db_role_setting WHERE setrole = '${a}_cron'::regrole;`);
  const login=run(`${a}_cron`,a,'SELECT count(*) FROM cron.job;',cronPasswords[a]);
  return settings==='0'&&login.status===0&&login.stdout.trim()==='1'||`${settings} ${login.status} ${login.stderr.slice(0,120)}`;
 });
 await check('an archive of the environment, planned as an in-place restore plans it, restores into a stage database of another name with its jobs',()=>{
  const dump=spawnSync('docker',['exec',name,'pg_dump','-U','supabase_admin','-Fc','-d',a],{maxBuffer:64*1024*1024});
  if(dump.status)return 'pg_dump failed: '+String(dump.stderr).slice(0,200);
  const sql=spawnSync('docker',['exec','-i',name,'pg_restore','--create','--exit-on-error','--file=-'],{input:dump.stdout,maxBuffer:64*1024*1024,encoding:'utf8'});
  if(sql.status||sql.stderr)return 'pg_restore planning emitted: '+sql.stderr.slice(0,200);
  const stage=a+'_stage_check';
  const planned=spawnSync('/usr/bin/python3',['-c',`import sys,io,json;sys.path.insert(0,'lab');import restore_sql
out=io.StringIO();plan=restore_sql.plan_archive(io.StringIO(sys.stdin.read()),out,${JSON.stringify(a)},${JSON.stringify(stage)})
print(json.dumps({'create':plan.create,'metadata':list(plan.metadata),'roles':list(plan.roles),'replay':out.getvalue()}))`],{input:sql.stdout,encoding:'utf8',maxBuffer:64*1024*1024});
  if(planned.status)return 'planner refused: '+planned.stderr.slice(-300);
  const plan=JSON.parse(planned.stdout) as {create:string;metadata:string[];roles:string[];replay:string};
  const missing=plan.roles.filter(role=>superuser('postgres',`SELECT count(*) FROM pg_roles WHERE rolname = '${role.replaceAll("'","''")}';`)!=='1');
  superuser('postgres',plan.create);
  try {
   const replayed=run('supabase_admin',stage,plan.replay+'\n'+plan.metadata.join('\n'));
   if(replayed.status)return 'replay failed: '+replayed.stderr.slice(0,300);
   const jobs=superuser(stage,'SELECT count(*) FROM cron.job;'),original=superuser(a,'SELECT count(*) FROM cron.job;');
   const valid=superuser(stage,`SELECT cron.schedule_valid('*/5 * * * *') AND NOT cron.schedule_valid('nope');`);
   return missing.length===0&&jobs===original&&Number(jobs)>0&&valid==='t'||`missing roles ${missing.join(',')}, jobs ${jobs} of ${original}, valid ${valid}`;
  } finally {superuser('postgres',`DROP DATABASE IF EXISTS ${stage};`);}
 });
 await check('a developer session holding the database row (a no-op GRANT left open) does not hold up the connection limit a turn writes beyond its bound',async()=>{
  const holder=spawn('docker',['exec','-i','-e','PGPASSWORD='+developerPassword,name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],{stdio:['pipe','ignore','ignore']});
  holder.stdin.write(`BEGIN; GRANT CONNECT ON DATABASE ${a} TO PUBLIC; SELECT pg_sleep(60);\n`);
  try {
   const held=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_developer' AND query LIKE '%pg_sleep(60)%';`)==='1',10000);
   const begun=Date.now();
   const out=run('supabase_admin','postgres',sqlFor('limit_sql',a,',22'));
   const took=Date.now()-begun;
   return held&&out.status!==0&&/lock timeout/.test(out.stderr)&&took<8000||`held ${held}, status ${out.status}, took ${took} ms, ${out.stderr.slice(0,120)}`;
  } finally {holder.kill('SIGKILL');superuser('postgres',`SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '${a}_developer';`);}
 });
 await check('a job holding the developer role row does not hold up a write pause, which ends the scheduler login first',async()=>{
  const holder=runner(a);
  works(`SELECT cron.schedule('hold-developer', '1 second', $$set statement_timeout = 0; begin; alter role ${a}_developer password 'held-by-a-job'; select pg_sleep(120)$$);`);
  try {
   const holding=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%held-by-a-job%' AND state = 'active';`)==='1',20000);
   const begun=Date.now();
   superuser('postgres',sqlFor('close_sql',a));
   superuser('postgres',`SET statement_timeout = '20s'; DO $$ BEGIN ALTER ROLE ${a}_developer NOLOGIN; END $$;`);
   const took=Date.now()-begun;
   return holding&&took<8000||`holding ${holding}, took ${took} ms`;
  } finally {
   await holder.stop();
   superuser('postgres',`ALTER ROLE ${a}_developer LOGIN PASSWORD '${developerPassword}';`);
   works(`SELECT cron.unschedule('hold-developer');`);
   superuser('postgres',sqlFor('login_sql',a,`,${JSON.stringify(cronPasswords[a])}`));
   superuser('postgres',sqlFor('open_sql',a));
  }
 });
 await check('a job holding the database row or the realtime schema row does not hold up turning Realtime on beyond its bound',async()=>{
  // The runtime's own Realtime scripts (durable_runtime.realtime_database), captured without running them.
  const captured=spawnSync('/usr/bin/python3',['-c',`import sys,json;sys.path.insert(0,'lab')
from types import SimpleNamespace
from unittest.mock import patch
import durable_runtime as d
r=object.__new__(d.Runtime);out=[]
r.sql=lambda q,database='postgres',check=True:out.append([q,database]) or SimpleNamespace(stdout='',returncode=0)
with patch.object(d,'current_database_limit',return_value=40):r.realtime_database(${JSON.stringify(a)},{'realtime':'${'r'.repeat(64)}'})
r.realtime_grants(${JSON.stringify(a)})
print(json.dumps(out))`],{encoding:'utf8'});
  if(captured.status)return 'capture failed: '+captured.stderr.slice(-300);
  const scripts=JSON.parse(captured.stdout) as [string,string][];
  const holder=runner(a);
  works(`SELECT cron.schedule('hold-database', '1 second', $$reset role; set statement_timeout = 0; begin; grant connect on database ${a} to public; select pg_sleep(120)$$);`);
  try {
   const holding=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%grant connect on database%' AND state = 'active';`)==='1',20000);
   const begun=Date.now();
   const out=run('supabase_admin',scripts[0]![1]==='postgres'?'postgres':a,scripts[0]![0]);
   const took=Date.now()-begun;
   if(!(holding&&out.status!==0&&/lock timeout/.test(out.stderr)&&took<8000))return `holding ${holding}, status ${out.status}, took ${took} ms, ${out.stderr.slice(0,160)}`;
   works(`SELECT cron.unschedule('hold-database');`);
   superuser('postgres',`SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%grant connect on database%';`);
   await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%grant connect on database%';`)==='0',20000);
   // Realtime's grants after its migrations: its schema as the migrations leave it (the superuser's, with usage
   // for authenticated, which the developer inherits), held by a job with a no-op GRANT.
   for(const [script,database] of scripts.slice(0,2))superuser(database==='postgres'?'postgres':a,script);
   superuser(a,`CREATE SCHEMA IF NOT EXISTS _realtime; CREATE TABLE IF NOT EXISTS realtime.messages (id int);
GRANT USAGE ON SCHEMA realtime TO authenticated; GRANT SELECT ON realtime.messages TO authenticated;`);
   works(`SELECT cron.schedule('hold-schema', '1 second', $$set statement_timeout = 0; begin; grant usage on schema realtime to public; select pg_sleep(120)$$);`);
   const schemaHeld=await until(()=>superuser('postgres',`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_cron' AND query LIKE '%grant usage on schema realtime%' AND state = 'active';`)==='1',20000);
   const grantsBegun=Date.now();
   const grants=run('supabase_admin',a,scripts[scripts.length-1]![0]);
   const grantsTook=Date.now()-grantsBegun;
   works(`SELECT cron.unschedule('hold-schema');`);
   return schemaHeld&&grants.status!==0&&/lock timeout/.test(grants.stderr)&&grantsTook<8000||`schema held ${schemaHeld}, status ${grants.status}, took ${grantsTook} ms, ${grants.stderr.slice(0,160)}`;
  } finally {
   run(`${a}_developer`,a,`SELECT cron.unschedule('hold-database');`,developerPassword);
   await holder.stop();
   superuser('postgres',`SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '${a}_cron';`);
   // The Realtime role, schemas and publication stay: the container is disposable, and later checks only add.
  }
 });
 await check('a second run of the schema SQL changes nothing and keeps the jobs',()=>{
  superuser(a,sqlFor('shim_sql',a));
  return superuser(a,'SELECT count(*) FROM cron.job;')==='1';
 });
 await check('a developer transaction holding every lock on the schemas\' tables does not hold up the schema SQL and grants run again',async()=>{
  const holder=spawn('docker',['exec','-i','-e','PGPASSWORD='+developerPassword,name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],{stdio:['pipe','ignore','ignore']});
  holder.stdin.write(`BEGIN; LOCK TABLE cron.job, cron.job_run_details, net.http_request_queue, net._http_response IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(60);\n`);
  try {
   const held=await until(()=>superuser(a,`SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE c.relname = 'job' AND l.mode = 'AccessExclusiveLock' AND l.granted;`)==='1',10000);
   const begun=Date.now();
   superuser(a,sqlFor('shim_sql',a));
   superuser(a,sqlFor('grants_sql',a));
   const took=Date.now()-begun;
   return held&&took<4000||`held ${held}, took ${took} ms`;
  } finally {holder.kill('SIGKILL');superuser('postgres',`SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '${a}_developer';`);}
 });
} catch(error) {
 results.push({check:'setup',result:'fail',detail:(error as Error).message.slice(0,500)});
} finally {
 spawnSync('docker',['rm','-f',name],{encoding:'utf8'});
}
const failed=results.filter(item=>item.result==='fail').length;
const report={total:results.length,failed,skipped:0,checks:results};
if(evidence)writeFileSync(evidence,JSON.stringify({scope:'One disposable Supabase Postgres container from the pinned image, two environment databases, the scheduler runner and process on this host; not a running installation.',
 image,at:new Date().toISOString(),seconds:Math.round((Date.now()-started)/1000),...report},null,1)+'\n');
console.log(JSON.stringify(report));
process.exit(failed?1:0);
