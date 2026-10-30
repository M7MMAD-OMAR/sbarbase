/** The words of the Server page's room and cleanup (src/control/capacity.ts). Pure, so the wording is tested. */
import type {Capacity,Level,Thresholds} from '../src/control/capacity';
export type {Capacity,Level,Thresholds};
export type Action=NonNullable<Capacity['action']>;

const GIB=1024**3,MIB=1024**2;
/** Sizes as Docker prints them are decimal; the page shows what the host counts, in binary units, with one decimal above a GiB. */
export function size(value:number|null):string {
 if(value===null)return 'Unavailable';
 return value>=GIB?`${Math.round(value/GIB*10)/10} GiB`:`${Math.round(value/MIB)} MiB`;
}
export const LEVEL_LABEL:Record<Level,string>={ok:'Enough',warning:'Getting low',critical:'Too low',unknown:'Not known yet'};
export const LEVEL_CLASS:Record<Level,string>={ok:'applied',warning:'pending',critical:'failed',unknown:''};

/** What the memory level means for a person who wants another environment, in the supervisor's own numbers. Realtime and Edge Functions pass their own check of
 * the placement and the database connections too, so the page does not promise an answer for them. */
export function memoryNote(capacity:Capacity):string {
 const t=capacity.thresholds,m=capacity.memory;
 if(!t||m.availableBytes===null)return 'The limits are read from the supervisor once a minute, and have not arrived yet.';
 const reserve=size(t.memoryReserveBytes),need=size(t.memoryReserveBytes+t.newEnvironmentMemoryBytes);
 const tail=' Realtime and Edge Functions pass their own check of memory and database connections as well.';
 if(m.level==='critical')return `Less than ${need} is available (${reserve} kept in reserve plus ${size(t.newEnvironmentMemoryBytes)} for the environment), so a new environment is refused until memory is freed.${tail}`;
 if(m.level==='warning')return `There is room for only one more environment: each needs ${size(t.newEnvironmentMemoryBytes)} on top of the ${reserve} kept in reserve.${tail}`;
 return `The server keeps ${reserve} in reserve. A new environment needs about ${need} available.${tail}`;
}

const FILESYSTEM:Record<'checkout'|'docker',string>={checkout:'The disk Sbarbase is installed on',docker:'The disk Docker keeps its data on'};
export const filesystemLabel=(name:'checkout'|'docker')=>FILESYSTEM[name];
export function diskNote(capacity:Capacity):string {
 const t=capacity.thresholds,d=capacity.disk;
 if(!t||d.availableBytes===null)return 'The limits are read from the supervisor once a minute, and have not arrived yet.';
 const warn=`${size(t.diskWarningBytes)} or ${Math.round(t.diskWarningShare*100)}%`,critical=`${size(t.diskCriticalBytes)} or ${Math.round(t.diskCriticalShare*100)}%`;
 if(d.level==='critical')return `Below ${critical} is free. Backups and databases stop working when the disk is full: free space now.`;
 if(d.level==='warning')return `Below ${warn} is free. The operator is notified; free space before it is critical (below ${critical}).`;
 return `It is a warning below ${warn} free and critical below ${critical}. A new environment also needs ${size(t.newEnvironmentDiskBytes)} on top of a ${size(d.reserveBytes)} reserve.`;
}

/** The swap line: this is a fact about the host, said plainly, since memory ceilings are only as good as there being no swap to hide behind. */
export function swapNote(swapTotalBytes:number|null):string {
 if(swapTotalBytes===null)return 'Swap could not be read on this host.';
 if(swapTotalBytes===0)return 'This host has no swap: a container that passes its memory ceiling is stopped, not slowed down. Each service container has swap switched off either way.';
 return `This host has ${size(swapTotalBytes)} of swap. Sbarbase's own containers have swap switched off, so a container that passes its ceiling is stopped, but other things on the host may swap.`;
}

/** The size shown before the button, when it was measured, and whether a clear has happened since: the size is only read every ten minutes. */
export function cacheText(capacity:Capacity,now:number=Date.now(),ago:(then:number,now:number)=>string=()=>''):string {
 const cache=capacity.buildCache;
 if(!cache)return 'The size of Docker\'s build cache has not been measured yet. It is read every ten minutes.';
 if(cache.beforeClear)return 'The build cache was cleared after the last measurement. Its size is read again within ten minutes.';
 const when=ago(cache.measuredAt,now);
 return `Docker's build cache holds ${size(cache.totalBytes)}, of which up to ${size(cache.reclaimableBytes)} can be freed${when?` (measured ${when})`:''}.`;
}
export const CACHE_GUARANTEE='Clearing it removes only the build cache. Images, containers and volumes are never touched: the way back of an update needs the images, so they stay.';

export function actionText(action:Action|null,now:number,ago:(then:number,now:number)=>string):string|null {
 if(!action)return null;
 if(action.state==='pending'||action.state==='running')return 'Clearing the build cache…';
 if(action.state==='failed')return action.failure??'The build cache could not be cleared. Nothing else was touched.';
 const freed=action.reclaimedBytes===null?'':` and freed ${size(action.reclaimedBytes)}`;
 return `Cleared ${action.finishedAt===null?'':ago(action.finishedAt,now)}${freed}.`.replace('Cleared  ','Cleared ');
}
/** A request is in progress while it is pending or running and no older than half an hour: past that the catalog accepts a new one, so the button does too. */
export const busy=(action:Action|null,now:number=Date.now())=>(action?.state==='pending'||action?.state==='running')&&now-action.requestedAt<30*60_000;
