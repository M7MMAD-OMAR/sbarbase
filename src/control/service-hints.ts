/** Hints, never actions (docs/engineering/OPTIONAL-SERVICES.md, S2).
 *
 * A service that has been on for a whole number of days with no use gets a hint on the Services page and, at most once
 * every `NOTIFY_EVERY_MS`, one operator notification. Nothing is changed by a hint: turning the service off is the
 * person's own choice. The number of days is an installation setting with a floor of one day (`UNUSED_FLOOR_DAYS`). */
import type {Catalog} from './catalog';
import type {ServiceActivity} from '../gateway/activity';
import type {Stats} from './services';
export const UNUSED_DEFAULT_DAYS=7,UNUSED_FLOOR_DAYS=1,UNUSED_CEILING_DAYS=365;
export const NOTIFY_EVERY_MS=14*86_400_000;
const DAY_MS=86_400_000;

export type HintInput={state:'off'|'starting'|'stopping'|'on'|'failed';mode?:'off'|'on'|'on_demand';inUse:number|null;
 /** Last use, or null. */
 lastUsedAt:number|null;
 /** When the service last changed state: for a service that is on, when it was turned on. */
 onSince:number|null;now:number;days:number};
export type Hint={days:number;neverUsed:boolean};

export function unusedDays(value:unknown):number {
 const days=typeof value==='string'&&/^\d{1,4}$/.test(value)?Number(value):typeof value==='number'?value:NaN;
 return Number.isSafeInteger(days)?Math.min(UNUSED_CEILING_DAYS,Math.max(UNUSED_FLOOR_DAYS,days)):UNUSED_DEFAULT_DAYS;
}

/** The hint for one service, or null. Only a service that is plainly on counts: not one starting, stopping or failed, not one with
 * something open or running now, and not one in On demand mode (it sleeps by itself). The clock starts at the later of the last use
 * and the time it was turned on, so a service switched on today is never called unused for a week of earlier silence. */
export function unusedHint(input:HintInput):Hint|null {
 if(input.state!=='on'||(input.inUse??0)>0||input.mode==='on_demand')return null;
 const since=Math.max(input.lastUsedAt??0,input.onSince??0);
 if(!since)return null;
 const days=Math.floor((input.now-since)/DAY_MS);
 return days>=Math.max(UNUSED_FLOOR_DAYS,input.days)?{days,neverUsed:input.lastUsedAt===null||input.lastUsedAt<(input.onSince??0)}:null;
}

/** The once an hour look over every ready runtime: for each Realtime or Edge Functions that is on with no use for the installation's
 * number of days, one operator notice (at most one every 14 days per service, kept in the catalog). It never changes a service.
 * Returns the notices written, for tests. */
export function checkUnusedServices(catalog:Catalog,activity:ServiceActivity,now:number,stats?:Stats):{runtime:string;service:'realtime'|'functions';days:number}[] {
 const days=catalog.installationServices().unusedDays,written:{runtime:string;service:'realtime'|'functions';days:number}[]=[];
 for(const runtime of catalog.readyRuntimes()){
  let state;try{state=catalog.servicesOf(runtime);}catch{continue;}
  for(const service of ['realtime','functions'] as const){
   const item=state[service];
   const hint=unusedHint({state:item.state==='pending'?(item.desired==='on'?'starting':'stopping'):item.state==='on'?'on':item.state==='failed'?'failed':'off',mode:state.modes[service].mode,
    inUse:service==='realtime'?activity.openSockets(runtime):activity.inFlightCalls(runtime),
    lastUsedAt:activity.lastUsed(runtime,service)??state.activity[service]??null,onSince:item.updatedAt,now,days});
   if(!hint)continue;
   const memory=stats?.containers[`${runtime}-${service}`]?.memoryBytes??0;
   try{if(catalog.noticeUnusedService(runtime,service,hint.days,Math.round(memory/1048576),now))written.push({runtime,service,days:hint.days});}catch{}
  }
 }
 return written;
}
