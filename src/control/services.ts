import {readFileSync,statSync} from 'node:fs';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {Catalog,type ServiceRow,type ServicesState} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import type {ServiceActivity} from '../gateway/activity';
import {unusedHint,type Hint} from './service-hints';
import {IDLE_FLOOR_MINUTES,HOLD_MS,WAKES_PER_HOUR,type OnDemandService} from './on-demand-policy';
import {requireOperatorPublication,managementPublication,bindManagementPublication} from './management-publication';
import {refreshCurrentManagement} from './management-context';

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
export type ServiceState='off'|'starting'|'stopping'|'on'|'failed'|'sleeping'|'waking';
export type ServiceView={id:ServiceId;state:ServiceState;failure:string|null;updatedAt:number|null;
 /** Resident memory and its ceiling, from the last sample; null when the service has no container of its own or none was sampled. */
 memoryBytes:number|null;memoryLimitBytes:number|null;
 /** What turning it on adds, in bytes of memory; null when it adds no container. */
 costBytes:number|null;
 /** Last use in milliseconds. null: never seen, or not recorded for this service (`tracked` false). */
 lastUsedAt:number|null;tracked:boolean;
 /** Open Realtime sockets, or Edge Functions calls in flight, in this process; null for the other services. */
 inUse:number|null;
 /** Set when the service has been on with no use for the installation's number of days: a hint, never an action. */
 unused:Hint|null;
 /** Realtime and Edge Functions only: the saved mode, the quiet period in minutes, and whether On demand can be chosen for it at all. */
 mode?:'off'|'on'|'on_demand';idleMinutes?:number;onDemandAvailable?:boolean};
export type ServicesAnswer={runtime:string;services:ServiceView[];canChange:boolean;
 /** The kill switch is on: every On demand service is kept On. The wake limits are shown read only. */
 killSwitch:boolean;limits:{wakesPerHour:number;holdSeconds:number;idleFloorMinutes:number};
 stats:{capturedAt:number|null;stale:boolean};
 capacity:{availableBytes:number|null;totalBytes:number|null}};
export type Stats={capturedAt:number;containers:Record<string,{memoryBytes:number;limitBytes:number}>;costs:{realtime:number|null;functions:number|null};
 /** What the supervisor enforces (lab/service_stats.py thresholds): memory and disk reserves, and where a disk is a warning or critical. */
 thresholds?:{memoryReserveBytes:number;newEnvironmentMemoryBytes:number;diskReserveBytes:number;newEnvironmentDiskBytes:number;diskWarningBytes:number;diskWarningShare:number;diskCriticalBytes:number;diskCriticalShare:number};
 buildCache?:{totalBytes:number;reclaimableBytes:number;at:number};
 /** Free and total bytes of the filesystems Sbarbase writes to (lab/service_stats.py filesystems): the checkout and, when it is another one, Docker's data root. */
 filesystems?:Partial<Record<'checkout'|'docker',{availableBytes:number;totalBytes:number}>>};

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
   if(!/^(db|storage|management-auth|e_[a-f0-9]{24}-[a-z]{3,9})$/.test(name)||!entry||!count(entry.memoryBytes)||!count(entry.limitBytes))continue;
   containers[name]={memoryBytes:entry.memoryBytes,limitBytes:entry.limitBytes};
  }
  const costs=(value.costs??{}) as {realtime?:unknown;functions?:unknown};
  const extra=value as {thresholds?:Record<string,unknown>;buildCache?:Record<string,unknown>};
  const keys=['memoryReserveBytes','newEnvironmentMemoryBytes','diskReserveBytes','newEnvironmentDiskBytes','diskWarningBytes','diskCriticalBytes'] as const;
  const shares=['diskWarningShare','diskCriticalShare'] as const;
  const t=extra.thresholds;
  const thresholds=t&&keys.every(key=>count(t[key]))&&shares.every(key=>typeof t[key]==='number'&&(t[key] as number)>=0&&(t[key] as number)<=1)?t as unknown as NonNullable<Stats['thresholds']>:undefined;
  const c=extra.buildCache;
  const buildCache=c&&count(c.totalBytes)&&count(c.reclaimableBytes)&&count(c.at)?{totalBytes:c.totalBytes,reclaimableBytes:c.reclaimableBytes,at:c.at}:undefined;
  const f=(extra as {filesystems?:Record<string,{availableBytes?:unknown;totalBytes?:unknown}>}).filesystems;
  const filesystems:NonNullable<Stats['filesystems']>={};
  for(const key of ['checkout','docker'] as const){const item=f?.[key];if(item&&count(item.availableBytes)&&count(item.totalBytes)&&item.totalBytes>0&&item.availableBytes<=item.totalBytes)filesystems[key]={availableBytes:item.availableBytes,totalBytes:item.totalBytes};}
  return {capturedAt:value.capturedAt,containers,costs:{realtime:count(costs.realtime)?costs.realtime:null,functions:count(costs.functions)?costs.functions:null},
   ...(thresholds?{thresholds:Object.fromEntries([...keys,...shares].map(key=>[key,t![key]])) as NonNullable<Stats['thresholds']>}:{}),...(buildCache?{buildCache}:{}),...(Object.keys(filesystems).length?{filesystems}:{})};
 }catch{return undefined;}
}

const SERVICE_STATE:Record<ServiceRow['state'],ServiceState>={off:'off',on:'on',failed:'failed',pending:'starting'};
function stateOf(row:ServiceRow):ServiceState {
 // A pending change is "starting" or "stopping" by what was asked for.
 return row.state==='pending'?row.desired==='on'?'starting':'stopping':SERVICE_STATE[row.state];
}

export type ServicesInput={state:Omit<ServicesState,'actorCanChange'|'modes'|'killSwitch'|'onDemandAvailable'>&{actorCanChange?:boolean;modes?:ServicesState['modes'];killSwitch?:boolean;onDemandAvailable?:ServicesState['onDemandAvailable']};stats?:Stats;now:number;activity?:ServiceActivity;unusedDays?:number;
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
  memoryBytes:null,memoryLimitBytes:null,costBytes:null,lastUsedAt:state.activity[id]??null,tracked:false,inUse:null,unused:null,...extra});
 const withMode=(view:ServiceView,item:ServiceRow,service:OnDemandService):ServiceView=>{
  const saved=state.modes?.[service];
  if(!saved)return view;
  // An On demand service that is off is asleep, one that is starting is waking: said plainly, never as a failure or as Off.
  const shown:ServiceState=saved.mode==='on_demand'?(item.state==='off'?'sleeping':item.state==='pending'&&item.desired==='on'?'waking':view.state):view.state;
  return {...view,state:shown,mode:saved.mode,idleMinutes:saved.idleMinutes,onDemandAvailable:state.onDemandAvailable?.[service]??false};
 };
 const withHint=(view:ServiceView,item:ServiceRow,service:OnDemandService):ServiceView=>{
  const shaped=withMode(view,item,service);
  return {...shaped,unused:input.unusedDays===undefined?null:unusedHint({state:shaped.state==='sleeping'||shaped.state==='waking'?'off':shaped.state,mode:shaped.mode,inUse:shaped.inUse,lastUsedAt:shaped.lastUsedAt,onSince:item.updatedAt,now,days:input.unusedDays})};
 };
 return {runtime:state.runtime,canChange:state.actorCanChange??false,killSwitch:state.killSwitch??false,
  limits:{wakesPerHour:WAKES_PER_HOUR,holdSeconds:HOLD_MS/1000,idleFloorMinutes:IDLE_FLOOR_MINUTES},
  services:[
   withHint(row('realtime',state.realtime,{...memory('realtime'),costBytes:stats?.costs.realtime??null,tracked:true,
    lastUsedAt:activity?.lastUsed(state.runtime,'realtime')??state.activity.realtime??null,inUse:activity?.openSockets(state.runtime)??null}),state.realtime,'realtime'),
   withHint(row('functions',state.functions,{...memory('functions'),costBytes:stats?.costs.functions??null,tracked:true,
    lastUsedAt:activity?.lastUsed(state.runtime,'functions')??state.activity.functions??null,inUse:activity?.inFlightCalls(state.runtime)??null}),state.functions,'functions'),
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
   const data=composeServices({state,stats:(options.stats??readStats)(),now:(options.now??Date.now)(),activity:options.activity,memory:options.memory?.(),unusedDays:catalog.installationServices().unusedDays});
   return bindReadyEnvironmentPublication(reply(200,{data}),catalog,actor,match[1]!,state.runtime,false);
  }catch(error){
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}

const INSTALLATION_HELP='Send any of {"unusedDays": 1 to 365, "killSwitch": true or false, "defaultMode": "off" or "on_demand", "realtimeOnDemand": true or false, "idleMinutes": {"realtime": 5 or more, "functions": 5 or more}}';
/** GET and PUT `/management/v1/installation/services`: the installation wide settings of the optional services: the days before an unused service is
 * reported, the kill switch (every On demand service behaves as On), the mode and quiet periods a new environment starts with, and, read only, the
 * wake limits. Installation operators only, for both. */
export function installationServicesHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  if(new URL(request.url).pathname!=='/management/v1/installation/services')return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try{
   if(!catalog.installationOperator(actor))return reply(403,{message:'Forbidden'});
   if(request.method==='GET')return managementPublication(catalog,()=>{requireOperatorPublication(catalog,actor);return reply(200,{data:catalog.installationServices()});});
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   const body=input as Record<string,unknown>|null;
   const keys=body&&typeof body==='object'&&!Array.isArray(body)?Object.keys(body):[];
   const idle=body?.idleMinutes as Record<string,unknown>|undefined;
   const shapeOk=keys.length>0&&keys.every(key=>['unusedDays','killSwitch','defaultMode','idleMinutes','realtimeOnDemand'].includes(key))
    &&(body!.unusedDays===undefined||typeof body!.unusedDays==='number')&&(body!.killSwitch===undefined||typeof body!.killSwitch==='boolean')
    &&(body!.defaultMode===undefined||typeof body!.defaultMode==='string')&&(body!.realtimeOnDemand===undefined||typeof body!.realtimeOnDemand==='boolean')
    &&(idle===undefined||(!!idle&&typeof idle==='object'&&!Array.isArray(idle)&&Object.keys(idle).every(key=>(key==='realtime'||key==='functions')&&typeof idle[key]==='number')));
   if(!shapeOk)return reply(400,{message:INSTALLATION_HELP});
   const data=catalog.setInstallationServices(actor,{unusedDays:body!.unusedDays as number|undefined,killSwitch:body!.killSwitch as boolean|undefined,
    defaultMode:body!.defaultMode as string|undefined,realtimeOnDemand:body!.realtimeOnDemand as boolean|undefined,idleMinutes:idle as {realtime?:number;functions?:number}|undefined});
   return bindManagementPublication(reply(200,{data}),catalog,()=>{requireOperatorPublication(catalog,actor);return reply(200,{data:catalog.installationServices()});});
  }catch(error){
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Invalid services setting')return reply(400,{message:INSTALLATION_HELP});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
