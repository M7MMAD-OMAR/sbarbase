import {randomBytes} from 'node:crypto';
import {catchUp,due,minuteStart,parseSchedule,type Schedule} from './cron';
import {perform,type HttpRequest,type HttpResult,type Policy,type NameResolver} from './egress';
import type {Row,WireSession} from './wire';

/** One environment's scheduled jobs and outgoing requests (docs/engineering/SCHEDULED-JOBS.md).
 *
 * Every connection is `<e>_cron`'s, to that environment's database alone, through the scheduler's own client
 * (src/scheduler/wire.ts), never Bun's driver: that one signs in again on its own after a backend is ended and
 * derives the SCRAM key on this thread at any iteration count a job could set. One bookkeeping session reads
 * `cron.job`, writes `cron.job_run_details`, takes requests from `net.http_request_queue` and writes
 * `net._http_response`, one statement at a time; its search path is pinned and every lock wait and statement
 * is bounded, so tenant code holding a lock delays only this runner. Values go in as dollar quoted literals
 * under a random tag, checked absent from the value. Each run gets a connection of its own, which keeps no
 * rows, takes the developer role and is closed after the run; at its deadline a run's backend is terminated
 * from a connection opened for that alone, so a bookkeeping session that tenant code keeps failing cannot stop
 * the kill. Nothing here waits for another environment. */

export const MAX_RUNNING=2,RUN_MS=10*60_000,MAX_REQUESTS=8,MAX_URL=8*1024,MAX_HEADERS=100;
export const HISTORY='7 days',RESPONSES='6 hours',MESSAGE=2000;
const SESSION="SET search_path TO pg_catalog, pg_temp; SET ROLE NONE; SET lock_timeout = '5s'; SET statement_timeout = '15s'; "+
 "SET idle_in_transaction_session_timeout = '60s'; SET default_transaction_read_only = off; SET application_name = 'sbarbase scheduler'";

export type OpenRun=(application:string)=>Promise<WireSession>;

type Job={jobid:number;schedule:string};
type JobState={parsed:Schedule|null;text:string;pending:boolean;running:boolean;lastStart:number};
type Run={jobid:number;runid:number|null;pid:number|null;session:WireSession|null;timer:ReturnType<typeof setTimeout>|null;ended:boolean};

function text(error:unknown){
 const value=error as {message?:unknown};
 return (typeof value?.message==='string'&&value.message?value.message:'The job failed').slice(0,MESSAGE);
}

/** The status line pg_cron records for a command that succeeded (pg_cron.c, PollForTask): "N rows" when the last
 * statement returned rows or was empty, its command tag otherwise. */
export function tag(command:string,rows:number,tuples:boolean):string {
 if(tuples)return `${rows} ${rows===1?'row':'rows'}`;
 return command;
}

/** A literal no setting of the session can change the reading of: dollar quoted with a random tag that the
 * value does not contain. NUL, which text cannot hold, is refused. */
export function literal(value:string):string {
 if(value.includes('\u0000'))throw new Error('A value holds a NUL character');
 for(;;){const quote='$q'+randomBytes(8).toString('hex')+'$';if(!value.includes(quote))return quote+value+quote;}
}
const nullable=(value:string|null)=>value===null?'NULL':literal(value);
/** An integer for SQL text, or a refusal. */
export function integer(value:unknown):string {
 if(typeof value==='string'?!/^-?[0-9]{1,15}$/.test(value):typeof value!=='number'&&typeof value!=='bigint')throw new Error('Not an integer');
 const number=Number(value);
 if(!Number.isSafeInteger(number))throw new Error('Not an integer');
 return String(number);
}

export class EnvironmentRunner {
 readonly runtime:string;
 private book:WireSession|null=null;
 private queue:Promise<unknown>=Promise.resolve();
 private jobs=new Map<number,JobState>();
 private runs=new Set<Run>();
 private requests=0;
 private last:number|null=null;
 private stopping=false;
 private nextPrune=0;
 private retryAt=0;
 private loop:Promise<void>|null=null;
 private wake:(()=>void)|null=null;

 constructor(runtime:string,private readonly openRun:OpenRun,private readonly policy:()=>Policy|null,
  private readonly resolve:NameResolver,private readonly log:(line:string)=>void=()=>{},private readonly now:()=>number=Date.now,
  private readonly runMs=RUN_MS,private readonly send:typeof perform=perform,
  private readonly lastResort:(pid:number)=>Promise<boolean>=async()=>false) {
  if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid environment');
  this.runtime=runtime;
 }

 start(){this.loop??=this.run();return this;}

 /** Start nothing new, end the runs in flight (recorded as failed), close every connection. */
 async stop(reason='stopped for maintenance'){
  this.stopping=true;
  this.wake?.();
  await Promise.all([...this.runs].map(run=>this.end(run,reason)));
  await this.loop?.catch(()=>{});
  this.book?.close();
  this.book=null;
 }

 private async run(){
  while(!this.stopping){
   if(this.now()<this.retryAt){await this.pause();continue;}
   try{await this.tick();}
   catch(error){
    // The database closed, the login was closed (a pause, a fence, a restore) or a statement timed out: begin
    // again a little later, without recording anything; the runs in flight record their own outcome. The
    // minutes missed meanwhile are not run later: the runner starts again at the next whole minute.
    this.log(`${this.runtime}: ${text(error)}`);
    this.book?.close();
    this.book=null;
    this.last=null;
    this.retryAt=this.now()+5000;
   }
   await this.pause();
  }
 }

 private pause(){return new Promise<void>(done=>{const timer=setTimeout(done,1000);this.wake=()=>{clearTimeout(timer);done();};});}

 private opening:Promise<WireSession>|null=null;
 private session():Promise<WireSession> {
  if(this.book)return Promise.resolve(this.book);
  if(this.stopping&&!this.loop)return Promise.reject(new Error('stopped'));
  this.opening??=this.open().finally(()=>{this.opening=null;});
  return this.opening;
 }

 /** One bookkeeping statement, after the ones before it: the session carries one at a time. A failure closes
  * the session, which the next use opens again. */
 private sql(text:string):Promise<Row[]> {
  const next=this.queue.then(async()=>{
   const book=await this.session();
   const outcome=await book.rows(text);
   if(!outcome.ok){
    if(book.ended&&this.book===book)this.book=null;
    throw new Error(outcome.message);
   }
   return outcome.rows;
  });
  this.queue=next.catch(()=>{});
  return next;
 }

 private async open():Promise<WireSession> {
  const book=await this.openRun('sbarbase scheduler');
  const set=await book.query(SESSION);
  if(!set.ok){book.close();throw new Error(set.message);}
  this.book=book;
  if(this.runs.size===0){
   // Runs a stopped or crashed scheduler left, or a restored database carried: never finished here, so ended
   // (a backend a killed scheduler left running is terminated) and failed, as pg_cron marks them at start.
   // Only with no run of this runner in flight.
   const settled=await book.query(`SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(a.pid)) FROM pg_catalog.pg_stat_activity a
    WHERE a.usename OPERATOR(pg_catalog.=) ${literal(this.runtime+'_cron')} AND a.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()
     AND a.pid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT job_pid FROM cron.job_run_details WHERE status OPERATOR(pg_catalog.=) ANY (ARRAY['starting','running'])));
    UPDATE cron.job_run_details SET status = 'failed', return_message = 'server restarted', end_time = pg_catalog.now()
    WHERE status OPERATOR(pg_catalog.=) ANY (ARRAY['starting','running'])`);
   if(!settled.ok){book.close();this.book=null;throw new Error(settled.message);}
  }
  return book;
 }

 private async tick(){
  // Only what decides when a job is due, every second; a run reads its command when it starts.
  const rows=await this.sql(`SELECT jobid, schedule FROM cron.job
   WHERE active AND pg_catalog.octet_length(schedule) OPERATOR(pg_catalog.<) 1000
   ORDER BY jobid LIMIT 100`);
  const now=this.now();
  const seen=new Set<number>();
  for(const row of rows){
   const jobid=Number(row.jobid),schedule=row.schedule??'';seen.add(jobid);
   const state=this.jobs.get(jobid);
   // A changed schedule changes the job's state in place: a run in flight clears `running` on this object.
   if(!state)this.jobs.set(jobid,{parsed:parseSchedule(schedule),text:schedule,pending:false,running:false,lastStart:0});
   else if(state.text!==schedule){state.parsed=parseSchedule(schedule);state.text=schedule;state.pending=false;}
  }
  for(const jobid of [...this.jobs.keys()])if(!seen.has(jobid)&&!this.jobs.get(jobid)!.running)this.jobs.delete(jobid);
  // Interval jobs: due once the interval passed since the last start, one waiting run at most.
  for(const state of this.jobs.values())
   if(state.parsed?.kind==='interval'&&!state.pending&&now-state.lastStart>=state.parsed.seconds*1000)state.pending=true;
  // Cron jobs: each minute the clock passed, as pg_cron's StartPendingRuns.
  if(this.last===null)this.last=minuteStart(now);
  const moved=catchUp(this.last,now);
  this.last=moved.last;
  for(const minute of moved.minutes)for(const state of this.jobs.values())
   if(state.parsed?.kind==='cron'&&due(state.parsed,new Date(minute.at),minute.wild,minute.fixed))state.pending=true;
  for(const row of rows){
   if(this.stopping||this.runs.size>=MAX_RUNNING)break;
   const jobid=Number(row.jobid);
   const state=this.jobs.get(jobid);
   if(state&&state.pending&&!state.running){state.pending=false;state.running=true;state.lastStart=now;void this.execute(jobid,state);}
  }
  await this.drain();
  if(now>=this.nextPrune){
   this.nextPrune=now+60_000;
   await this.sql(`DELETE FROM cron.job_run_details WHERE end_time OPERATOR(pg_catalog.<) (pg_catalog.now() OPERATOR(pg_catalog.-) interval '${HISTORY}')`);
   await this.sql(`DELETE FROM net._http_response WHERE created OPERATOR(pg_catalog.<) (pg_catalog.now() OPERATOR(pg_catalog.-) interval '${RESPONSES}')`);
  }
 }

 private async execute(jobid:number,state:JobState){
  const run:Run={jobid,runid:null,pid:null,session:null,timer:null,ended:false};
  this.runs.add(run);
  try {
   const developer=this.runtime+'_developer';
   const [found]=await this.sql(`SELECT j.command, j.username, (pg_catalog.to_regrole(${literal(developer)}) IS NOT NULL)::text AS developer,
     pg_catalog.array_to_json(ARRAY(SELECT pg_catalog.unnest(s.setconfig) FROM pg_catalog.pg_db_role_setting s
      WHERE s.setrole OPERATOR(pg_catalog.=) pg_catalog.to_regrole(${literal(developer)})
       AND s.setdatabase OPERATOR(pg_catalog.=) ANY (ARRAY[0::oid, (SELECT oid FROM pg_catalog.pg_database WHERE datname OPERATOR(pg_catalog.=) pg_catalog.current_database())])
      ORDER BY s.setdatabase))::text AS settings
    FROM cron.job j WHERE j.jobid OPERATOR(pg_catalog.=) ${integer(jobid)} AND j.active
     AND pg_catalog.octet_length(j.command) OPERATOR(pg_catalog.<=) 65536 AND pg_catalog.octet_length(j.username) OPERATOR(pg_catalog.<=) 128`);
   // Unscheduled or turned off since the tick: nothing ran, nothing to record.
   if(!found)return;
   const command=found.command??'',username=found.username??'';
   const [row]=await this.sql(`INSERT INTO cron.job_run_details (jobid, database, username, command, status, start_time)
    VALUES (${integer(jobid)}, pg_catalog.current_database(), ${literal(username)}, ${literal(command)}, 'starting', pg_catalog.now()) RETURNING runid`);
   run.runid=Number(row!.runid);
   if(this.stopping)throw new Error('stopped for maintenance');
   if(found.developer!=='true')throw new Error('The developer role does not exist yet; turn database access on once');
   const session=run.session=await this.openRun('sbarbase scheduled job');
   run.pid=session.pid;
   run.timer=setTimeout(()=>void this.end(run,`job exceeded the ${Math.round(this.runMs/1000)} second limit`),this.runMs);
   if(run.ended)throw new Error('stopped');
   await this.sql(`UPDATE cron.job_run_details SET status = 'running', job_pid = ${integer(run.pid)} WHERE runid OPERATOR(pg_catalog.=) ${integer(run.runid)}`);
   const setup=await session.query(`SET statement_timeout = '${Math.ceil(this.runMs/1000)}s'; SET ROLE ${developer}`);
   if(!setup.ok)throw new Error(setup.message);
   // What a session of the developer login gets when it signs in: its own role settings (the platform's search
   // path among them), the database-specific ones last, as PostgreSQL applies them. Never a role switch. A
   // setting PostgreSQL would only warn about at sign-in does not fail the run either.
   let settings:unknown=[];
   try{settings=JSON.parse(found.settings??'[]');}catch{}
   for(const item of Array.isArray(settings)?settings:[]){
    if(typeof item!=='string')continue;
    const at=item.indexOf('=');
    if(at<1)continue;
    const name=item.slice(0,at).toLowerCase();
    if(name==='role'||name==='session_authorization'||name==='statement_timeout')continue;
    await session.query(`SELECT pg_catalog.set_config(${literal(name)}, ${literal(item.slice(at+1))}, false)`);
   }
   const result=await session.query(command);
   if(!result.ok)throw new Error(result.message);
   await this.finish(run,'succeeded',tag(result.tag,result.rows,result.tuples));
  } catch(error) {
   await this.finish(run,'failed',run.ended?null:text(error));
  } finally {
   if(run.timer)clearTimeout(run.timer);
   run.session?.close();
   this.runs.delete(run);
   state.running=false;
   this.wake?.();
  }
 }

 /** End a run from outside: terminate its backend, so a job that ignores its own timeout stops. The kill goes
  * through a connection of its own (the login's fourth), tried three times, whatever the bookkeeping session
  * is doing. */
 private async end(run:Run,reason:string){
  if(run.ended)return;
  run.ended=true;
  if(run.pid!==null){
   let attempts=0;
   for(;attempts<3;attempts++){
    let killer:WireSession|null=null;
    try {
     killer=await this.openRun('sbarbase scheduler stop');
     // As the login itself: a job may have given it a role setting (`ALTER ROLE ... SET role`) that would
     // otherwise make this session someone allowed to signal nothing of the login's.
     const outcome=await killer.query(`SET ROLE NONE; SELECT pg_catalog.pg_terminate_backend(${integer(run.pid)})`);
     if(!outcome.ok)throw new Error(outcome.message);
     break;
    } catch(error) {
     this.log(`${this.runtime}: a run could not be ended yet (${text(error)})`);
     await new Promise(done=>setTimeout(done,2000));
    } finally {killer?.close();}
   }
   if(attempts>=3){
    // The login may refuse new sign-ins (a job that runs RESET ROLE can change its password): the bookkeeping
    // session, signed in already, ends the backend instead; and when the job ended that session too, the
    // platform ends it as the superuser, which nothing the job controls can refuse.
    const ended=await this.sql(`SET ROLE NONE; SELECT pg_catalog.pg_terminate_backend(${integer(run.pid)})`).then(()=>true,()=>false)
     ||await this.lastResort(run.pid).catch(()=>false);
    if(!ended)this.log(`${this.runtime}: a run could not be ended; the next turn off, pause or start ends it`);
   }
  }
  run.session?.close();
  await this.finish(run,'failed',reason);
 }

 private async finish(run:Run,status:'succeeded'|'failed',message:string|null){
  if(run.runid===null||message===null)return;
  const runid=run.runid;
  run.runid=null;
  try {
   await this.sql(`UPDATE cron.job_run_details SET status = ${literal(status)}, return_message = ${literal(message.slice(0,MESSAGE).replaceAll('\u0000',''))},
    end_time = pg_catalog.now() WHERE runid OPERATOR(pg_catalog.=) ${integer(runid)}`);
  } catch {
   // Left as running: the next session this runner opens with no run in flight marks it failed.
  }
 }

 /** Take as many queued requests as may run now (at most MAX_REQUESTS in flight for this environment) and
  * perform each under the egress policy. */
 private async drain(){
  const rules=this.policy();
  if(!rules||this.stopping)return;
  const room=MAX_REQUESTS-this.requests;
  if(room<=0)return;
  const rows=await this.sql(`DELETE FROM net.http_request_queue q USING (
     SELECT id FROM net.http_request_queue ORDER BY id LIMIT ${integer(room)}) r
    WHERE q.id OPERATOR(pg_catalog.=) r.id
    RETURNING q.id, q.method::text AS method, pg_catalog.octet_length(q.url) AS url_size,
     CASE WHEN pg_catalog.octet_length(q.url) OPERATOR(pg_catalog.<=) ${MAX_URL} THEN q.url END AS url,
     q.timeout_milliseconds, pg_catalog.octet_length(q.body) AS body_size,
     CASE WHEN pg_catalog.octet_length(q.body) OPERATOR(pg_catalog.<=) 1048576 THEN pg_catalog.encode(q.body, 'base64') END AS body,
     pg_catalog.octet_length(q.headers::text) AS headers_size,
     CASE WHEN pg_catalog.octet_length(q.headers::text) OPERATOR(pg_catalog.<=) 65536 THEN q.headers::text END AS headers`);
  for(const row of rows){
   this.requests++;
   void this.request(row).finally(()=>{this.requests--;});
  }
 }

 private async request(row:Row){
  let result:HttpResult;
  const prepared=prepare(row);
  if(typeof prepared==='string')result={status:null,timedOut:false,error:prepared};
  else {
   const rules=this.policy();
   result=rules?await this.send(prepared,rules,this.resolve):{status:null,timedOut:false,error:'The outgoing request policy is not ready'};
  }
  if(this.stopping&&!this.book)return;
  try {
   const id=integer(row.id);
   if(result.status===null)
    await this.sql(`INSERT INTO net._http_response (id, timed_out, error_msg) VALUES (${id}, ${result.timedOut}, ${literal(result.error)})`);
   else
    await this.sql(`INSERT INTO net._http_response (id, status_code, content, headers, content_type, timed_out, error_msg)
     VALUES (${id}, ${integer(result.status)}, ${nullable(result.body)}, ${literal(JSON.stringify(result.headers))}::jsonb,
      ${nullable(result.contentType===null?null:result.contentType.replaceAll('\u0000',''))}, false, ${nullable(result.error)})`);
  } catch(error) {
   this.log(`${this.runtime}: a response could not be recorded (${text(error)})`);
  }
 }
}

/** A queued row as a request, or why it is refused before any connection. */
export function prepare(row:Record<string,unknown>):HttpRequest|string {
 // Rows come from the scheduler's own client as text: a size is a number written out, a body base64.
 if(row.url===null||row.url===undefined)return `URL longer than ${MAX_URL} bytes`;
 if(row.body_size!==null&&row.body_size!==undefined&&row.body===null)return 'Request body larger than 1 MiB';
 if(row.headers_size!==null&&row.headers_size!==undefined&&row.headers===null)return 'Request headers larger than 64 KiB';
 const method=String(row.method).toUpperCase();
 if(method!=='GET'&&method!=='POST'&&method!=='DELETE')return 'Unsupported method';
 const headers:Record<string,string>={};
 if(row.headers!==null&&row.headers!==undefined){
  let parsed:unknown;
  try{parsed=JSON.parse(String(row.headers));}catch{return 'Invalid request headers';}
  if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return 'Request headers must be a JSON object';
  const entries=Object.entries(parsed as Record<string,unknown>);
  if(entries.length>MAX_HEADERS)return `More than ${MAX_HEADERS} request headers`;
  for(const [key,value] of entries){
   // As pg_net's jsonb_each_text: a JSON string as itself, anything else as its JSON text.
   const textValue=typeof value==='string'?value:value===null?'':JSON.stringify(value);
   if(!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)||/[\r\n\0]/.test(textValue))return 'Invalid request header';
   headers[key]=textValue;
  }
 }
 const body=row.body===null||row.body===undefined?null:row.body instanceof Uint8Array?row.body:typeof row.body==='string'?new Uint8Array(Buffer.from(row.body,'base64')):null;
 const timeout=Number(row.timeout_milliseconds);
 return {method,url:String(row.url),headers,body,timeoutMs:Number.isFinite(timeout)?timeout:5000};
}

/** A resolver that runs at most `limit` lookups at once for one environment. */
export function bounded(resolve:NameResolver,limit:number):NameResolver {
 let active=0;const waiting:(()=>void)[]=[];
 return async host=>{
  if(active>=limit)await new Promise<void>(done=>waiting.push(done));
  active++;
  try{return await resolve(host);}
  finally{active--;waiting.shift()?.();}
 };
}

export {due,catchUp};
