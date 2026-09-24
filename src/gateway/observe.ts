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

function percentile(values:number[],fraction:number):number|null {
 if(!values.length)return null;
 const sorted=[...values].sort((a,b)=>a-b);
 // Whole milliseconds, as the console shows them.
 return Math.round(sorted[Math.min(sorted.length-1,Math.ceil(fraction*sorted.length)-1)]!);
}

export class RequestLog {
 private recentByRuntime=new Map<string,RequestEntry[]>();
 private minutesByRuntime=new Map<string,Minute[]>();
 private firstSeen=new Map<string,number>();
 constructor(private now:()=>number=Date.now) {}

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

const GATEWAY_PATH=/^\/([a-z][a-z0-9_]{1,30})\/(auth|rest|storage|realtime)\/v1(\/[^?]*)?$/;

/** Wraps the gateway so each answer is counted for its environment. `known` keeps names that
 * are not environments out of the log. */
export function observedGateway(gateway:(request:Request)=>Promise<Response>,log:RequestLog,known:(runtime:string)=>boolean,
 now:()=>number=performance.now.bind(performance)) {
 return async(request:Request):Promise<Response>=>{
  const started=now();
  const response=await gateway(request);
  const match=new URL(request.url).pathname.match(GATEWAY_PATH);
  if(match&&request.method!=='OPTIONS'){
   try{if(known(match[1]!))log.record(match[1]!,{method:request.method,service:match[2]!,path:match[3]||'/',status:response.status,
    ms:now()-started});}catch{}
  }
  return response;
 };
}
