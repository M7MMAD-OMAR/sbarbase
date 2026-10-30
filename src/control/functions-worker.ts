import type {Bucket,Line} from '../../lab/functions/main/usage';
import {FUNCTION_RANGES,type FunctionRange} from '../gateway/observe';

/** The control plane's side of the runtime's event worker (docs/engineering/EDGE-FUNCTIONS.md, "Event worker"): ask the environment's Functions
 * container what the event worker reported about one function, and shape it for the console. The numbers are per worker lifetime: a worker reports
 * when it is dropped, so a function that is called all day on one worker reports nothing until that worker ends. */
export type UsageAnswer={state:'ok';known:boolean;since:number;buckets:Bucket[];lines:Line[]}|{state:'unavailable'};
export type UsageSource=(runtime:string,name:string)=>Promise<UsageAnswer>;

function bucketOf(value:unknown):Bucket|undefined {
 const row=value as Partial<Bucket>|null;
 const numbers:(keyof Bucket)[]=['minute','boots','bootMs','workers','cpuSum','cpuMax','memSum','memMax','heapSum','externalSum','errors'];
 if(!row||typeof row!=='object'||numbers.some(key=>typeof row[key]!=='number'||!Number.isFinite(row[key] as number)))return undefined;
 return row as Bucket;
}

/** The call to `GET /_sb_stats/<name>` on one environment's main service. Nothing here throws: a failure is `unavailable`. */
export function usageThrough(route:(runtime:string)=>{url?:string;token?:string}|'maintenance'|undefined,transport:typeof fetch,timeoutMs=5_000):UsageSource {
 return async(runtime,name)=>{
  try{
   const where=route(runtime);
   if(where==='maintenance'||!where?.url||!where.token)return {state:'unavailable'};
   const response=await transport(new URL(`/_sb_stats/${encodeURIComponent(name)}`,where.url),{headers:{authorization:'Bearer '+where.token},signal:AbortSignal.timeout(timeoutMs)});
   if(response.status!==200)return {state:'unavailable'};
   const body=await response.json() as {known?:unknown;since?:unknown;buckets?:unknown;lines?:unknown};
   const buckets=(Array.isArray(body.buckets)?body.buckets:[]).map(bucketOf).filter((row):row is Bucket=>row!==undefined).slice(-1440);
   const lines=(Array.isArray(body.lines)?body.lines:[]).filter((line):line is Line=>!!line&&typeof (line as Line).at==='number'&&typeof (line as Line).msg==='string'&&typeof (line as Line).level==='string').slice(-200);
   return {state:'ok',known:body.known===true,since:typeof body.since==='number'?body.since:Date.now(),buckets,lines};
  }catch{return {state:'unavailable'};}
 };
}

export type UsagePoint={at:number;workers:number;averageCpuMs:number|null;maxCpuMs:number|null;averageMemory:number|null;maxMemory:number|null};
export type UsageView={range:FunctionRange;since:number;points:UsagePoint[];
 totals:{workers:number;boots:number;averageBootMs:number|null;averageCpuMs:number|null;maxCpuMs:number|null;averageMemory:number|null;maxMemory:number|null;heapPercent:number|null;externalPercent:number|null;errors:number}};

/** The buckets of the chosen range as chart points and totals. A worker is counted in the minute it ended. */
export function usageView(buckets:Bucket[],range:FunctionRange,since:number,now=Date.now()):UsageView {
 const {minutes,step}=FUNCTION_RANGES[range],current=Math.floor(now/60_000),first=current-minutes+1;
 const points:UsagePoint[]=[];
 const total={workers:0,boots:0,bootMs:0,cpuSum:0,cpuMax:0,memSum:0,memMax:0,heapSum:0,externalSum:0,errors:0};
 for(let start=first;start<=current;start+=step){
  let workers=0,cpuSum=0,cpuMax=0,memSum=0,memMax=0;
  for(const row of buckets)if(row.minute>=start&&row.minute<Math.min(start+step,current+1)){
   workers+=row.workers;cpuSum+=row.cpuSum;cpuMax=Math.max(cpuMax,row.cpuMax);memSum+=row.memSum;memMax=Math.max(memMax,row.memMax);
   total.workers+=row.workers;total.boots+=row.boots;total.bootMs+=row.bootMs;total.cpuSum+=row.cpuSum;total.cpuMax=Math.max(total.cpuMax,row.cpuMax);
   total.memSum+=row.memSum;total.memMax=Math.max(total.memMax,row.memMax);total.heapSum+=row.heapSum;total.externalSum+=row.externalSum;total.errors+=row.errors;
  }
  points.push({at:start*60_000,workers,averageCpuMs:workers?Math.round(cpuSum/workers):null,maxCpuMs:workers?cpuMax:null,averageMemory:workers?Math.round(memSum/workers):null,maxMemory:workers?memMax:null});
 }
 const per=(sum:number,count:number)=>count?Math.round(sum/count):null;
 return {range,since,points,totals:{workers:total.workers,boots:total.boots,averageBootMs:per(total.bootMs,total.boots),averageCpuMs:per(total.cpuSum,total.workers),
  maxCpuMs:total.workers?total.cpuMax:null,averageMemory:per(total.memSum,total.workers),maxMemory:total.workers?total.memMax:null,
  heapPercent:total.memSum?Math.round(total.heapSum/total.memSum*1000)/10:null,externalPercent:total.memSum?Math.round(total.externalSum/total.memSum*1000)/10:null,errors:total.errors}};
}

/** Replaces each of the environment's own secret values (six characters or more; shorter ones would hide ordinary words and numbers), and its JSON escaped and base64 forms, wherever a line holds
 * it, so what a function echoes back cannot show a secret that the console never displays. Longest first, so a value that contains another is
 * replaced whole. */
export function maskValues(text:string,values:string[]):string {
 let result=text;
 const forms=[...new Set(values)].filter(item=>item.length>=6).flatMap(item=>[item,JSON.stringify(item).slice(1,-1),Buffer.from(item).toString('base64').replace(/=+$/,'')]);
 for(const value of [...new Set(forms)].filter(item=>item.length>=6).sort((a,b)=>b.length-a.length))result=result.split(value).join('[redacted]');
 return result;
}
