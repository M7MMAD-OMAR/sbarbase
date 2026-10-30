/** What the gateway saw, per environment, kept in this process's memory only: the last
 * requests and per-minute totals for the last hour. A restart starts both empty. Nothing
 * here holds a key, a token, a header or a query string: only the method, the service, the
 * path without its query, the status and how long the answer took. */

export type RequestEntry={at:number;method:string;service:string;path:string;status:number;ms:number};
type Minute={minute:number;count:number;client:number;server:number;ms:number;slowest:number;services:Record<string,number>;
 durations:number[]};
export type Metrics={since:number|null;window:{minutes:number;requests:number;clientErrors:number;serverErrors:number;
 p50:number|null;p95:number|null;services:Record<string,number>};
 perMinute:{minute:number;requests:number;clientErrors:number;serverErrors:number;averageMs:number|null}[]};

const RECENT=500,MINUTES=60,MAX_PATH=200,MAX_ENVIRONMENTS=2000,DURATIONS_PER_MINUTE=2000;
// Per function counters: one bucket a minute for a day, 200 recent calls, and a ceiling on live buckets as a backstop.
const FUNCTION_MINUTES=1440,FUNCTION_RECENT=200,FUNCTION_BUCKET_CEILING=400_000,FUNCTION_RUNTIME_CEILING=40_000;
export const FUNCTION_RANGES={'15m':{minutes:15,step:1},'1h':{minutes:60,step:1},'3h':{minutes:180,step:5},'1d':{minutes:1440,step:15}} as const;
export type FunctionRange=keyof typeof FUNCTION_RANGES;
type FunctionBucket={minute:number;count:number;client:number;server:number;ms:number;slowest:number};
export type FunctionCall={at:number;method:string;path:string;status:number;ms:number};
type Cumulative={requests:number;client:number;server:number;since:number;complete:boolean};
type FunctionStats={buckets:FunctionBucket[];recent:FunctionCall[];total:Cumulative};
export type FunctionPoint={at:number;requests:number;clientErrors:number;serverErrors:number;averageMs:number|null;maxMs:number|null};
export type FunctionMetrics={range:FunctionRange;countingSince:number;points:FunctionPoint[];
 totals:{requests:number;clientErrors:number;serverErrors:number;averageMs:number|null;maxMs:number|null};
 sinceDeploy:{requests:number;clientErrors:number;serverErrors:number;since:number;complete:boolean}};

function percentile(values:number[],fraction:number):number|null {
 if(!values.length)return null;
 const sorted=[...values].sort((a,b)=>a-b);
 // Whole milliseconds, as the console shows them.
 return Math.round(sorted[Math.min(sorted.length-1,Math.ceil(fraction*sorted.length)-1)]!);
}

export class RequestLog {
 private functionsByRuntime=new Map<string,Map<string,FunctionStats>>();
 private functionBuckets=0;
 private runtimeBuckets=new Map<string,number>();
 private startedAt:number;
 private recentByRuntime=new Map<string,RequestEntry[]>();
 private minutesByRuntime=new Map<string,Minute[]>();
 private firstSeen=new Map<string,number>();
 constructor(private now:()=>number=Date.now) {this.startedAt=now();}

 /** Counts one call of a function that is deployed in this environment (the caller checks that).
  * The path is the part after the function's name, without its query. */
 recordFunction(runtime:string,name:string,call:{method:string;path:string;status:number;ms:number;at?:number}) {
  const at=call.at??this.now(),minute=Math.floor(at/60_000);
  let byName=this.functionsByRuntime.get(runtime);
  if(!byName){if(this.functionsByRuntime.size>=MAX_ENVIRONMENTS)return;byName=new Map();this.functionsByRuntime.set(runtime,byName);}
  let stats=byName.get(name);
  if(!stats){stats={buckets:[],recent:[],total:{requests:0,client:0,server:0,since:this.startedAt,complete:false}};byName.set(name,stats);}
  const path=call.path.length>MAX_PATH?call.path.slice(0,MAX_PATH)+'…':call.path;
  stats.recent.push({at,method:call.method,path,status:call.status,ms:Math.round(call.ms)});
  if(stats.recent.length>FUNCTION_RECENT)stats.recent.splice(0,stats.recent.length-FUNCTION_RECENT);
  let last=stats.buckets[stats.buckets.length-1];
  if(!last||last.minute<minute){
   const keep=minute-FUNCTION_MINUTES;
   let drop=0;while(drop<stats.buckets.length&&stats.buckets[drop]!.minute<=keep)drop++;
   if(drop){stats.buckets.splice(0,drop);this.functionBuckets-=drop;this.runtimeBuckets.set(runtime,(this.runtimeBuckets.get(runtime)??0)-drop);}
   // Past a ceiling the oldest minute of this environment makes room, so the recent numbers are the ones kept; an environment
   // never takes another's room. With nothing of its own to give up, a call still counts in the totals.
   if((this.functionBuckets>=FUNCTION_BUCKET_CEILING||(this.runtimeBuckets.get(runtime)??0)>=FUNCTION_RUNTIME_CEILING)&&!this.evictOldest(runtime,stats))return this.count(stats,call.status);
   last={minute,count:0,client:0,server:0,ms:0,slowest:0};stats.buckets.push(last);this.functionBuckets++;this.runtimeBuckets.set(runtime,(this.runtimeBuckets.get(runtime)??0)+1);
  }
  // A late call (its minute already passed) joins the bucket of its minute when that one is still held.
  this.count(stats,call.status);
  const bucket=last.minute===minute?last:stats.buckets.find(item=>item.minute===minute);
  if(!bucket)return;
  bucket.count++;bucket.ms+=call.ms;bucket.slowest=Math.max(bucket.slowest,call.ms);
  if(call.status>=500)bucket.server++;else if(call.status>=400)bucket.client++;
 }

 /** Drops the oldest bucket of the environment (not the one being filled) and returns whether there was one. */
 private evictOldest(runtime:string,filling:FunctionStats):boolean {
  let oldest:FunctionStats|undefined;
  for(const stats of this.functionsByRuntime.get(runtime)?.values()??[])
   if(stats.buckets.length&&(stats!==filling||stats.buckets.length>1)&&(!oldest||stats.buckets[0]!.minute<oldest.buckets[0]!.minute))oldest=stats;
  if(!oldest)return false;
  oldest.buckets.shift();this.functionBuckets--;this.runtimeBuckets.set(runtime,(this.runtimeBuckets.get(runtime)??0)-1);
  return true;
 }

 private count(stats:FunctionStats,status:number) {
  stats.total.requests++;
  if(status>=500)stats.total.server++;else if(status>=400)stats.total.client++;
 }

 /** A deploy starts "since the last deploy" again; the minute history stays. */
 deployedFunction(runtime:string,name:string,at:number) {
  const stats=this.functionsByRuntime.get(runtime)?.get(name);
  if(stats)stats.total={requests:0,client:0,server:0,since:at,complete:true};
  else{
   let byName=this.functionsByRuntime.get(runtime);
   if(!byName){if(this.functionsByRuntime.size>=MAX_ENVIRONMENTS)return;byName=new Map();this.functionsByRuntime.set(runtime,byName);}
   byName.set(name,{buckets:[],recent:[],total:{requests:0,client:0,server:0,since:at,complete:true}});
  }
 }

 /** A deleted function's numbers go with it, so a new function with the same name starts at zero. */
 forgetFunction(runtime:string,name:string) {
  const stats=this.functionsByRuntime.get(runtime)?.get(name);
  if(!stats)return;
  this.functionBuckets-=stats.buckets.length;
  this.runtimeBuckets.set(runtime,(this.runtimeBuckets.get(runtime)??0)-stats.buckets.length);
  this.functionsByRuntime.get(runtime)!.delete(name);
 }

 /** The newest first. The stored path is raw: a caller that shows it redacts it first, and filters on the redacted text. */
 functionCalls(runtime:string,name:string,filter:{errors?:boolean;since?:number;limit?:number}={}):FunctionCall[] {
  const rows=(this.functionsByRuntime.get(runtime)?.get(name)?.recent??[]).filter(row=>
   (!filter.errors||row.status>=400)&&(filter.since===undefined||row.at>=filter.since));
  return rows.slice(-(filter.limit??200)).reverse();
 }

 functionMetrics(runtime:string,name:string,range:FunctionRange):FunctionMetrics {
  const {minutes,step}=FUNCTION_RANGES[range],current=Math.floor(this.now()/60_000);
  const buckets=this.functionsByRuntime.get(runtime)?.get(name)?.buckets??[];
  const first=current-minutes+1;
  const points:FunctionPoint[]=[];
  const totals={requests:0,clientErrors:0,serverErrors:0,ms:0,slowest:0};
  for(let start=first;start<=current;start+=step){
   let requests=0,clientErrors=0,serverErrors=0,ms=0,slowest=0;
   for(const bucket of buckets)if(bucket.minute>=start&&bucket.minute<Math.min(start+step,current+1)){
    requests+=bucket.count;clientErrors+=bucket.client;serverErrors+=bucket.server;ms+=bucket.ms;slowest=Math.max(slowest,bucket.slowest);}
   points.push({at:start*60_000,requests,clientErrors,serverErrors,averageMs:requests?Math.round(ms/requests):null,maxMs:requests?Math.round(slowest):null});
   totals.requests+=requests;totals.clientErrors+=clientErrors;totals.serverErrors+=serverErrors;totals.ms+=ms;totals.slowest=Math.max(totals.slowest,slowest);
  }
  const total=this.functionsByRuntime.get(runtime)?.get(name)?.total;
  return {range,countingSince:this.startedAt,points,
   totals:{requests:totals.requests,clientErrors:totals.clientErrors,serverErrors:totals.serverErrors,
    averageMs:totals.requests?Math.round(totals.ms/totals.requests):null,maxMs:totals.requests?Math.round(totals.slowest):null},
   // `complete` is true only when this process saw the deploy; otherwise the numbers start at the last restart (`since`).
   sinceDeploy:{requests:total?.requests??0,clientErrors:total?.client??0,serverErrors:total?.server??0,since:total?.since??this.startedAt,complete:total?.complete??false}};
 }

 /** For tests and the memory check: how many buckets are held. */
 functionBucketCount(){return this.functionBuckets;}

 record(runtime:string,entry:Omit<RequestEntry,'at'>&{at?:number}) {
  if(!this.recentByRuntime.has(runtime)&&this.recentByRuntime.size>=MAX_ENVIRONMENTS)return;
  const at=entry.at??this.now();
  const path=entry.path.length>MAX_PATH?entry.path.slice(0,MAX_PATH)+'…':entry.path;
  const recent=this.recentByRuntime.get(runtime)??[];
  recent.push({at,method:entry.method,service:entry.service,path,status:entry.status,ms:Math.round(entry.ms)});
  if(recent.length>RECENT)recent.splice(0,recent.length-RECENT);
  this.recentByRuntime.set(runtime,recent);
  if(!this.firstSeen.has(runtime))this.firstSeen.set(runtime,at);
  const minute=Math.floor(at/60_000);
  const minutes=(this.minutesByRuntime.get(runtime)??[]).filter(row=>row.minute>minute-MINUTES);
  let row=minutes.find(item=>item.minute===minute);
  if(!row){row={minute,count:0,client:0,server:0,ms:0,slowest:0,services:{},durations:[]};minutes.push(row);}
  row.count++;row.ms+=entry.ms;row.slowest=Math.max(row.slowest,entry.ms);
  if(entry.status>=500)row.server++;else if(entry.status>=400)row.client++;
  row.services[entry.service]=(row.services[entry.service]??0)+1;
  if(row.durations.length<DURATIONS_PER_MINUTE)row.durations.push(entry.ms);
  this.minutesByRuntime.set(runtime,minutes);
 }

 /** The newest first, optionally one service's or only errors. */
 recent(runtime:string,filter:{service?:string;errors?:boolean;limit?:number}={}):RequestEntry[] {
  const rows=(this.recentByRuntime.get(runtime)??[]).filter(row=>
   (!filter.service||row.service===filter.service)&&(!filter.errors||row.status>=400));
  return rows.slice(-(filter.limit??200)).reverse();
 }

 metrics(runtime:string):Metrics {
  const current=Math.floor(this.now()/60_000);
  const minutes=(this.minutesByRuntime.get(runtime)??[]).filter(row=>row.minute>current-MINUTES);
  const services:Record<string,number>={};
  const durations:number[]=[];
  let requests=0,clientErrors=0,serverErrors=0;
  for(const row of minutes){
   requests+=row.count;clientErrors+=row.client;serverErrors+=row.server;durations.push(...row.durations);
   for(const [name,count] of Object.entries(row.services))services[name]=(services[name]??0)+count;
  }
  const perMinute=[];
  for(let minute=current-MINUTES+1;minute<=current;minute++){
   const row=minutes.find(item=>item.minute===minute);
   perMinute.push({minute:minute*60_000,requests:row?.count??0,clientErrors:row?.client??0,serverErrors:row?.server??0,
    averageMs:row?Math.round(row.ms/row.count):null});
  }
  return {since:this.firstSeen.get(runtime)??null,window:{minutes:MINUTES,requests,clientErrors,serverErrors,
   p50:percentile(durations,0.5),p95:percentile(durations,0.95),services},perMinute};
 }
}

const GATEWAY_PATH=/^\/([a-z][a-z0-9_]{1,30})\/(auth|rest|storage|realtime|functions)\/v1(\/[^?]*)?$/;

/** Wraps the gateway so each answer is counted for its environment, Function calls and the gateway's
 * own refusals (429, 503) included. `known` keeps names that are not environments out of the log.
 * `ms` is the time to the response headers, not to the end of the body: for a streamed Function
 * answer it is not the call's duration. The path is the pathname alone: no query, header or key. */
export function observedGateway(gateway:(request:Request)=>Promise<Response>,log:RequestLog,known:(runtime:string)=>boolean,
 now:()=>number=performance.now.bind(performance),knownFunction:(runtime:string,name:string)=>boolean=()=>false) {
 return async(request:Request):Promise<Response>=>{
  const started=now();
  const response=await gateway(request);
  const match=new URL(request.url).pathname.match(GATEWAY_PATH);
  if(match&&request.method!=='OPTIONS'){
   try{
    if(known(match[1]!)){
     const ms=now()-started;
     log.record(match[1]!,{method:request.method,service:match[2]!,path:match[3]||'/',status:response.status,ms});
     // Only a name this environment has deployed gets its own counters: a caller can send any name.
     const parts=(match[3]||'/').match(/^\/([^/]+)(\/.*)?$/);
     if(match[2]==='functions'&&parts&&knownFunction(match[1]!,parts[1]!))
      log.recordFunction(match[1]!,parts[1]!,{method:request.method,path:parts[2]||'/',status:response.status,ms});
    }
   }catch{}
  }
  return response;
 };
}
