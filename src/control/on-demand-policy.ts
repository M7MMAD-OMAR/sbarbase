/** The numbers and words of On demand mode (docs/guides/services.md, docs/engineering/OPTIONAL-SERVICES.md, S3).
 *
 * Pure and dependency free, so the catalog, the gateway, the sleeper and the console all read the same values. A person changes the quiet
 * period of a service per environment and the defaults for the installation from the console; the limits below are shown read only. */
export type OnDemandService='realtime'|'functions';
export type Mode='off'|'on'|'on_demand';
export const MODES:readonly Mode[]=['off','on','on_demand'];
export const ON_DEMAND_SERVICES:readonly OnDemandService[]=['realtime','functions'];

/** Quiet period before a service sleeps, in minutes. No setting goes below the floor, so a wrong value cannot flap a service. */
export const IDLE_FLOOR_MINUTES=5,IDLE_CEILING_MINUTES=7*24*60;
export const DEFAULT_IDLE_MINUTES:Record<OnDemandService,number>={realtime:30,functions:15};
/** Wakes of one service of one environment in an hour; then a clear 429. Shown read only. */
export const WAKES_PER_HOUR=6;
export const WAKE_WINDOW_MS=3_600_000;
/** How long the gateway holds a first request while the service starts, then answers 503 with retry-after. Shown read only. */
export const HOLD_MS=20_000;
export const RETRY_AFTER_SECONDS=2;
/** Held requests one environment may have waiting for a wake; more are told to retry. */
export const MAX_HELD_PER_ENVIRONMENT=64;
/** Functions calls get this long to finish before a manual turn off ends them; new calls are refused meanwhile. */
export const DRAIN_MS=10_000;

export function idleMinutes(value:unknown,service:OnDemandService):number {
 const minutes=typeof value==='string'&&/^\d{1,6}$/.test(value)?Number(value):typeof value==='number'?value:NaN;
 return Number.isSafeInteger(minutes)&&minutes>=IDLE_FLOOR_MINUTES&&minutes<=IDLE_CEILING_MINUTES?minutes:DEFAULT_IDLE_MINUTES[service];
}
export const validIdleMinutes=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=IDLE_FLOOR_MINUTES&&value<=IDLE_CEILING_MINUTES;
export const isMode=(value:unknown):value is Mode=>typeof value==='string'&&(MODES as readonly string[]).includes(value);

/** What a new environment gets for a service that can be On demand. Measured on 2026-10-30 (docs/evidence/optional-services-2026-10-30/cold-start.json):
 * Edge Functions answer again 1.7 to 3.4 seconds after a wake, so On demand is the default for them. Realtime took 12.3 to 14.8 seconds, almost all of it the
 * upstream Realtime's own boot, so a first connection would wait past the 10 seconds supabase-js allows a channel to join: Realtime keeps Off for a new
 * environment and cannot be chosen as On demand unless an operator allows it (`realtimeOnDemand`, for hardware where it starts faster). */
export const DEFAULT_NEW_MODE:'off'|'on_demand'='on_demand';
/** Whether On demand can be chosen for a service without an operator's say: Edge Functions yes, Realtime no (see above). */
export const ON_DEMAND_BY_DEFAULT:Record<OnDemandService,boolean>={realtime:false,functions:true};
