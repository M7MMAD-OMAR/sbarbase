import type {Catalog} from './catalog';
import type {ServiceActivity} from '../gateway/activity';
import {HOLD_MS,MAX_HELD_PER_ENVIRONMENT,RETRY_AFTER_SECONDS,WAKES_PER_HOUR,type OnDemandService} from './on-demand-policy';

/** On demand mode at run time (docs/guides/services.md, docs/engineering/OPTIONAL-SERVICES.md, S3).
 *
 * `ensure` is called by the gateway only after the request's API key was verified (or, for a Functions call without a key, after the
 * function was found to be one its owner made public): it starts a sleeping service and holds the request until it answers, or says why not.
 * `sweep` is the sleeper: once a minute it puts a quiet service to sleep, and with the kill switch on it wakes what sleeps, one at a time.
 *
 * Safety, in the order a request meets it:
 *  - nothing here runs for a service saved as Off or On, and a service that is up is never touched by `ensure`;
 *  - a wake is a request row in the catalog, applied by the supervisor with the same admission as a manual start (lab/realtime.py);
 *  - one wake at a time on the installation, at most `WAKES_PER_HOUR` per service and environment, then a 429;
 *  - a held request waits at most `HOLD_MS`, then gets 503 with retry-after: never a connection that hangs;
 *  - a service sleeps only after its whole quiet period with no open socket and no call in flight (ServiceActivity.quietFor), judged in one
 *    synchronous step with the request that puts it to sleep. */
export type Outcome={ok:true}|{ok:false;status:404|429|503;message:string;retryAfter?:number};
const up:Outcome={ok:true};
const retry=(message:string,seconds=RETRY_AFTER_SECONDS):Outcome=>({ok:false,status:503,message,retryAfter:seconds});

const RETRY_FAILED_MS=10*60_000;
export type OnDemandOptions={holdMs?:number;now?:()=>number;pause?:(ms:number)=>Promise<void>;pollMs?:number;maxHeld?:number;wakesPerHour?:number};

export class OnDemand {
 private waking=new Map<string,Promise<Outcome>>();
 private held=new Map<string,number>();
 private tail:Promise<unknown>=Promise.resolve();
 private lastSleepSweep=0;
 constructor(private catalog:Catalog,private activity:ServiceActivity,private running:(runtime:string,service:OnDemandService)=>boolean,private options:OnDemandOptions={}) {}

 private get now(){return this.options.now?.()??Date.now();}
 private get holdMs(){return this.options.holdMs??HOLD_MS;}
 private pause(ms:number){return (this.options.pause??(delay=>new Promise<void>(resolve=>setTimeout(resolve,delay))))(ms);}
 private key(runtime:string,service:OnDemandService){return runtime+'|'+service;}

private modes=new Map<string,{at:number;onDemand:boolean}>();
 /** Whether this service is saved as On demand: the gateway asks before it spends anything on a request. The answer is kept for two seconds, so a busy
  * environment does not read the catalog for every request; a change of mode takes at most that long to reach the gateway. */
 isOnDemand(runtime:string,service:OnDemandService):boolean {
  const key=this.key(runtime,service),now=this.now,known=this.modes.get(key);
  if(known&&now-known.at<2000)return known.onDemand;
  try{
   const onDemand=this.catalog.serviceMode(runtime,service).mode==='on_demand';
   if(this.modes.size>5000)this.modes.clear();
   this.modes.set(key,{at:now,onDemand});
   return onDemand;
  }catch{return false;}
 }

 /** Makes sure the service answers, waking it if it is asleep and saved as On demand (the kill switch also lets it wake: it means On).
  * Returns at once when it is up. A service saved as Off or On is not woken: the caller answers as it always has. */
 async ensure(runtime:string,service:OnDemandService):Promise<Outcome> {
  let mode;
  try{mode=this.catalog.serviceMode(runtime,service).mode;this.modes.set(this.key(runtime,service),{at:this.now,onDemand:mode==='on_demand'});}catch{return {ok:false,status:503,message:'Service state unavailable',retryAfter:RETRY_AFTER_SECONDS};}
  // A mode that changed within the two seconds the gateway kept its answer: On and running is served as it always was; only Off is refused.
  if(mode==='on'&&this.catalog.serviceRow(runtime,service).state==='on'&&this.running(runtime,service))return up;
  if(mode!=='on_demand')return {ok:false,status:404,message:'Service is off for this environment'};
  let row=this.catalog.serviceRow(runtime,service);
  // The sleeper asked for a stop that the supervisor has not applied yet: call it off instead of waiting for a stop and a start.
  // Edge Functions only: a stop of Realtime that the supervisor has begun cannot be called off without a request meeting a container that is gone.
  if(service==='functions'&&row.state==='pending'&&row.desired==='off'&&this.running(runtime,service)&&this.catalog.cancelSleep(runtime,service))row=this.catalog.serviceRow(runtime,service);
  if(row.state==='on'&&this.running(runtime,service)){this.activity.touch(runtime,service);return up;}
  const key=this.key(runtime,service);
  const count=(this.held.get(runtime)??0)+1;
  if(count>(this.options.maxHeld??MAX_HELD_PER_ENVIRONMENT))return retry('Too many requests are waiting for this service to start. Retry shortly.');
  this.held.set(runtime,count);
  try{
   let wake=this.waking.get(key);
   if(!wake){
    wake=this.wake(runtime,service).finally(()=>{this.waking.delete(key);});
    this.waking.set(key,wake);
   }
   const timeout=new Promise<Outcome>(resolve=>{const timer=setTimeout(()=>resolve(retry('The service is starting. Retry in a moment.')),this.holdMs);timer.unref?.();wake.then(()=>clearTimeout(timer),()=>clearTimeout(timer));});
   const outcome=await Promise.race([wake,timeout]);
   // A service that has just been woken counts as used from that moment, so it is never put to sleep before the request that woke it has been served.
   if(outcome.ok)this.activity.touch(runtime,service);
   return outcome;
  }finally{
   const remaining=(this.held.get(runtime)??1)-1;
   if(remaining>0)this.held.set(runtime,remaining);else this.held.delete(runtime);
  }
 }

 /** One wake at a time on the installation: the next waits its turn, and its turn counts against its own hold. */
 private wake(runtime:string,service:OnDemandService):Promise<Outcome> {
  const run=this.tail.then(()=>this.wakeNow(runtime,service),()=>this.wakeNow(runtime,service));
  this.tail=run.catch(()=>undefined);
  return run;
 }

 private async wakeNow(runtime:string,service:OnDemandService):Promise<Outcome> {
  const deadline=this.now+this.holdMs,poll=this.options.pollMs??200;
  let requested=false;
  while(this.now<deadline){
   let row;
   try{row=this.catalog.serviceRow(runtime,service);}catch{return retry('Service state unavailable');}
   if(row.state==='on'&&this.running(runtime,service)){this.activity.touch(runtime,service);return up;}
   if(row.state==='pending'){await this.pause(poll);continue;}
   if(requested&&row.state==='failed')return {ok:false,status:503,message:'Could not wake: '+(row.failure??'the service did not start')+' The environment keeps working without it.',retryAfter:30};
   if(requested&&row.state==='off'&&row.desired==='off')return retry('The service could not be started. Retry shortly.');
   if(!requested){
    let used=0;
    try{used=this.catalog.wakesInWindow(runtime,service,this.now);}catch{return retry('Service state unavailable');}
    if(used>=(this.options.wakesPerHour??WAKES_PER_HOUR))
     return {ok:false,status:429,message:'This service has been woken too often in the last hour. Keep it On, or retry later.',retryAfter:600};
    let result;
    try{result=this.catalog.requestServiceChange(runtime,service,true,'system:wake','wake',row.state==='on');}catch{return retry('Service state unavailable');}
    if(result==='requested')requested=true;
   }
   await this.pause(poll);
  }
  return retry('The service is starting. Retry in a moment.');
 }

 /** The sleeper and the kill switch, called every few seconds. Sleep decisions run at most once a minute. Returns the requests it wrote. */
 sweep(now=this.now):{slept:number;woke:number} {
  const out={slept:0,woke:0};
  let killed=false;
  try{killed=this.catalog.installationServices().killSwitch;}catch{return out;}
  const sleepDue=now-this.lastSleepSweep>=60_000;
  if(sleepDue&&!killed)this.lastSleepSweep=now;
  let pending=false;
  for(const target of this.catalog.onDemandTargets()){
   let row;
   try{row=this.catalog.serviceRow(target.runtime,target.service);}catch{continue;}
   if(row.state==='pending'){pending=true;continue;}
   // A service that came on less than its quiet period ago is not put to sleep, however old its last use is: a wake that finished after the hold
   // (the request got 503 and will retry) is served by it.
   // A start that failed (usually for lack of room) is tried again only after ten minutes, not every sweep: each try is an audit row and a supervisor run.
   if(killed&&row.state==='failed'&&now-(row.updatedAt??0)<RETRY_FAILED_MS)continue;
   const running=row.state==='on'&&this.running(target.runtime,target.service);
   try{
    if(killed){
     // The kill switch means On: what sleeps is woken, one at a time on the installation, and nothing is put to sleep.
     if(!running&&!pending&&this.catalog.requestServiceChange(target.runtime,target.service,true,'system:kill-switch','kill_switch')==='requested'){pending=true;out.woke++;}
    }else if(sleepDue&&running&&now-(row.updatedAt??0)>=target.idleMinutes*60_000&&this.activity.quietFor(target.runtime,target.service,target.idleMinutes*60_000)
      &&this.catalog.requestServiceChange(target.runtime,target.service,false,'system:idle','idle')==='requested')out.slept++;
   }catch{}
  }
  return out;
 }
}
