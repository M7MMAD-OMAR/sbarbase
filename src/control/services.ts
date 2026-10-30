import {readFileSync,statSync} from 'node:fs';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {Catalog,type ServiceRow,type ServicesState} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import type {ServiceActivity} from '../gateway/activity';

/** GET `/environments/{id}/services`: the Services page of one environment (docs/guides/services.md).
 *
 * One answer for the five optional services (Realtime, Edge Functions, direct database access, scheduled jobs, Studio): what is asked
 * for, what the supervisor last did, the memory the container holds now, when the service was last used and what turning it on
 * costs. Every member of the organization reads it; the switches stay on the existing routes, for owners and admins.
 *
 * The memory comes from one small file the supervisor writes once a minute (lab/service_stats.py): the console process has no
 * Docker socket. The file is read with a size cap, and a sample older than `STALE_MS` is reported as stale, never as current.
 * The answer holds container names' numbers and times only: no environment variable, key, log or address. */
export type ServiceId='realtime'|'functions'|'database'|'cron'|'studio';
export type ServiceState='off'|'starting'|'stopping'|'on'|'failed';
export type ServiceView={id:ServiceId;state:ServiceState;failure:string|null;updatedAt:number|null;
 /** Resident memory and its ceiling, from the last sample; null when the service has no container of its own or none was sampled. */
 memoryBytes:number|null;memoryLimitBytes:number|null;
 /** What turning it on adds, in bytes of memory; null when it adds no container. */
 costBytes:number|null;
 /** Last use in milliseconds. null: never seen, or not recorded for this service (`tracked` false). */
 lastUsedAt:number|null;tracked:boolean;
 /** Open Realtime sockets, or Edge Functions calls in flight, in this process; null for the other services. */
 inUse:number|null};
export type ServicesAnswer={runtime:string;services:ServiceView[];canChange:boolean;
 stats:{capturedAt:number|null;stale:boolean};
 capacity:{availableBytes:number|null;totalBytes:number|null}};
export type Stats={capturedAt:number;containers:Record<string,{memoryBytes:number;limitBytes:number}>;costs:{realtime:number|null;functions:number|null}};

export const STATS_FILE='.lab/upstream/service-stats.json';
const MAX_STATS_BYTES=256*1024,STALE_MS=3*60_000;

const count=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;

/** The sampler's file, or undefined when it is missing, too large or not what the sampler writes. */
export function readStats(path:string=STATS_FILE):Stats|undefined {
 try{
  if(statSync(path).size>MAX_STATS_BYTES)return undefined;
  const value=JSON.parse(readFileSync(path,'utf8')) as {version?:unknown;capturedAt?:unknown;containers?:unknown;costs?:unknown};
  if(value?.version!==1||!count(value.capturedAt)||!value.containers||typeof value.containers!=='object'||Array.isArray(value.containers))return undefined;
  const containers:Stats['containers']={};
  for(const [name,entry] of Object.entries(value.containers as Record<string,{memoryBytes?:unknown;limitBytes?:unknown}>)){
   if(!/^(db|storage|management-auth|e_[a-f0-9]{24}-[a-z]{3,8})$/.test(name)||!entry||!count(entry.memoryBytes)||!count(entry.limitBytes))continue;
   containers[name]={memoryBytes:entry.memoryBytes,limitBytes:entry.limitBytes};
  }
  const costs=(value.costs??{}) as {realtime?:unknown;functions?:unknown};
  return {capturedAt:value.capturedAt,containers,costs:{realtime:count(costs.realtime)?costs.realtime:null,functions:count(costs.functions)?costs.functions:null}};
 }catch{return undefined;}
}

const SERVICE_STATE:Record<ServiceRow['state'],ServiceState>={off:'off',on:'on',failed:'failed',pending:'starting'};
function stateOf(row:ServiceRow):ServiceState {
 // A pending change is "starting" or "stopping" by what was asked for.
 return row.state==='pending'?row.desired==='on'?'starting':'stopping':SERVICE_STATE[row.state];
}

export type ServicesInput={state:ServicesState;stats?:Stats;now:number;activity?:ServiceActivity;
 memory?:{availableBytes:number|null;totalBytes:number|null}};

/** Pure: the answer from what the catalog, the sampler and the gateway know. */
export function composeServices(input:ServicesInput):ServicesAnswer {
 const {state,stats,now,activity}=input;
 const stale=!stats||Math.abs(now-stats.capturedAt)>STALE_MS;
 const memory=(container:string)=>{
  const entry=stats?.containers[`${state.runtime}-${container}`];
  return entry&&!stale?{memoryBytes:entry.memoryBytes,memoryLimitBytes:entry.limitBytes}:{memoryBytes:null,memoryLimitBytes:null};
 };
 const row=(id:ServiceId,item:ServiceRow,extra:Partial<ServiceView>):ServiceView=>({id,state:stateOf(item),failure:item.state==='failed'?item.failure:null,updatedAt:item.updatedAt,
  memoryBytes:null,memoryLimitBytes:null,costBytes:null,lastUsedAt:state.activity[id]??null,tracked:false,inUse:null,...extra});
 return {runtime:state.runtime,canChange:state.actorCanChange,
  services:[
   row('realtime',state.realtime,{...memory('realtime'),costBytes:stats?.costs.realtime??null,tracked:true,
    lastUsedAt:activity?.lastUsed(state.runtime,'realtime')??state.activity.realtime??null,inUse:activity?.openSockets(state.runtime)??null}),
   row('functions',state.functions,{...memory('functions'),costBytes:stats?.costs.functions??null,tracked:true,
    lastUsedAt:activity?.lastUsed(state.runtime,'functions')??state.activity.functions??null,inUse:activity?.inFlightCalls(state.runtime)??null}),
   row('database',state.database,{}),
   row('cron',state.cron,{}),
   row('studio',state.studio,{...memory('studio'),tracked:true}),
  ],
  stats:{capturedAt:stats?.capturedAt??null,stale},
  capacity:{availableBytes:input.memory?.availableBytes??null,totalBytes:input.memory?.totalBytes??null}};
}

export function servicesHandler(catalog:Catalog,identify:ManagementIdentity,options:{stats?:()=>Stats|undefined;activity?:ServiceActivity;
 memory?:()=>{availableBytes:number|null;totalBytes:number|null};now?:()=>number}={}) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/services$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try{
   const state=catalog.services(actor,match[1]!);
   const data=composeServices({state,stats:(options.stats??readStats)(),now:(options.now??Date.now)(),activity:options.activity,memory:options.memory?.()});
   return bindReadyEnvironmentPublication(reply(200,{data}),catalog,actor,match[1]!,state.runtime,false);
  }catch(error){
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
