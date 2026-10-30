import {Catalog,type HostActionState} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindManagementPublication,managementPublication,requireOperatorPublication} from './management-publication';
import {refreshCurrentManagement} from './management-context';
import {readStats,type Stats} from './services';
import type {ServerHealthSnapshot} from './server-health';

/** `GET /management/v1/server/capacity` and `POST /management/v1/server/build-cache`: the Server page's room and cleanup, for installation operators.
 *
 * The thresholds are the ones the supervisor enforces, written by lab/service_stats.py next to the memory figures (they come from lab/resource_admission.py and
 * lab/host_watch.py, never copied here). `levels` says in one word what each number means against them: `ok`, `warning` (a new environment or service may be
 * refused soon, or the disk is below its warning line) or `critical`. Clearing the build cache asks the supervisor for one `docker builder prune`: images, containers and
 * volumes are never touched, and the answer says so (docs/guides/services.md, docs/engineering/RESOURCE-POLICY.md). */
export type Level='ok'|'warning'|'critical'|'unknown';
export type Thresholds=NonNullable<Stats['thresholds']>;
export type Capacity={thresholds:Thresholds|null;
 memory:{availableBytes:number|null;totalBytes:number|null;swapTotalBytes:number|null;level:Level;nextEnvironmentNeedsBytes:number|null};
 disk:{availableBytes:number|null;totalBytes:number|null;level:Level;reserveBytes:number|null;
  /** Each filesystem Sbarbase writes to, with its own level: the checkout, and Docker's data root when it is another one (the build cache lives there). */
  filesystems:{name:'checkout'|'docker';availableBytes:number;totalBytes:number;level:Level}[]};
 /** `beforeClear`: the size was measured before the last clear finished, so it is not shown as current until the next measurement. */
 buildCache:{totalBytes:number;reclaimableBytes:number;measuredAt:number;beforeClear:boolean}|null;
 action:HostActionState|null;measuredAt:number|null;stale:boolean};

const STALE_MS=15*60_000;

/** Memory, by what admission does with a new environment (lab/resource_admission.py refuses one unless the reserve plus its own memory is available): `critical`
 * when one would be refused, `warning` when there is room for only one more, `ok` otherwise. Realtime and Edge Functions pass their own check of the placement and the
 * database connections as well, which the page does not predict. */
export function memoryLevel(available:number|null,thresholds:Thresholds|null):Level {
 if(available===null||!thresholds)return 'unknown';
 const needed=thresholds.memoryReserveBytes+thresholds.newEnvironmentMemoryBytes;
 if(available<needed)return 'critical';
 return available<needed+thresholds.newEnvironmentMemoryBytes?'warning':'ok';
}
/** Disk, by the supervisor's own two lines: critical below the critical bytes or share, warning below the warning ones. */
export function diskLevel(available:number|null,total:number|null,thresholds:Thresholds|null):Level {
 if(available===null||total===null||total<=0||!thresholds)return 'unknown';
 const share=available/total;
 if(available<thresholds.diskCriticalBytes||share<thresholds.diskCriticalShare)return 'critical';
 return available<thresholds.diskWarningBytes||share<thresholds.diskWarningShare?'warning':'ok';
}

export function composeCapacity(input:{health:ServerHealthSnapshot;stats?:Stats;action:HostActionState|null;now:number}):Capacity {
 const {health,stats,action,now}=input,thresholds=stats?.thresholds??null;
 const stale=!stats||Math.abs(now-stats.capturedAt)>STALE_MS;
 const usable=stale?null:thresholds;
 // Every filesystem Sbarbase writes to has its level; the disk's own is the worst of them. Without the supervisor's list, only the console's own.
 const listed=Object.entries(stats?.filesystems??{}) as ['checkout'|'docker',{availableBytes:number;totalBytes:number}][];
 const filesystems=(stale?[]:listed).map(([name,item])=>({name,availableBytes:item.availableBytes,totalBytes:item.totalBytes,level:diskLevel(item.availableBytes,item.totalBytes,usable)}));
 const order:Level[]=['unknown','ok','warning','critical'];
 const worst=filesystems.reduce<Level>((level,item)=>order.indexOf(item.level)>order.indexOf(level)?item.level:level,filesystems.length?'ok':'unknown');
 const clearedAfter=!!action&&action.state==='done'&&action.finishedAt!==null&&!!stats?.buildCache&&action.finishedAt>stats.buildCache.at;
 return {thresholds:usable,
  memory:{availableBytes:health.memory.availableBytes,totalBytes:health.memory.totalBytes,swapTotalBytes:health.memory.swapTotalBytes,level:memoryLevel(health.memory.availableBytes,usable),
   nextEnvironmentNeedsBytes:usable?usable.memoryReserveBytes+usable.newEnvironmentMemoryBytes:null},
  disk:{availableBytes:health.disk.availableBytes,totalBytes:health.disk.totalBytes,level:filesystems.length?worst:diskLevel(health.disk.availableBytes,health.disk.totalBytes,usable),reserveBytes:usable?.diskReserveBytes??null,filesystems},
  buildCache:stats?.buildCache?{totalBytes:stats.buildCache.totalBytes,reclaimableBytes:stats.buildCache.reclaimableBytes,measuredAt:stats.buildCache.at,beforeClear:clearedAfter}:null,
  action,measuredAt:stats?.capturedAt??null,stale};
}

export function capacityHandler(catalog:Catalog,identify:ManagementIdentity,options:{health:()=>ServerHealthSnapshot;stats?:()=>Stats|undefined;now?:()=>number}) {
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  const isCapacity=path==='/management/v1/server/capacity',isCache=path==='/management/v1/server/build-cache';
  if(!isCapacity&&!isCache)return reply(404,{message:'Unknown route'});
  if(request.method!==(isCapacity?'GET':'POST'))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try{
   if(!catalog.installationOperator(actor))return reply(403,{message:'Forbidden'});
   const view=()=>composeCapacity({health:options.health(),stats:(options.stats??readStats)(),action:catalog.hostAction('build_cache_clear'),now:(options.now??Date.now)()});
   if(isCapacity){
    const response=reply(200,{data:view()});
    response.headers.set('Cache-Control','no-store');
    return managementPublication(catalog,()=>{requireOperatorPublication(catalog,actor);return response;});
   }
   await refreshCurrentManagement();
   catalog.requestHostAction(actor,'build_cache_clear');
   return bindManagementPublication(reply(202,{data:view()}),catalog,()=>{requireOperatorPublication(catalog,actor);return reply(202,{data:view()});});
  }catch(error){
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Host action in progress')return reply(409,{message:'The build cache is already being cleared.'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
