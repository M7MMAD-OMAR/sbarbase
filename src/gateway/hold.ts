import {readFileSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {readJsonCached} from '../http/cached-json';
import {reply} from '../control/auth';
import {PENDING,UPDATES_DIRECTORY} from '../control/updates';
import {withCors} from './handler';

export const HOLD_MARKER=join(UPDATES_DIRECTORY,'hold');
export const UPGRADE_STATE=join(UPDATES_DIRECTORY,'state.json');

/** What a hold covers. `all`, today's hold: application traffic waits and management changes
 * pause. `management`: the same, except that the tenant data plane keeps flowing: each
 * environment's gateway routes (DATA_PLANE), its Realtime sockets, direct database access and
 * the Functions' Storage. Everything else stays held, the console, `/v1` and `/mcp` included,
 * since those write the control state (projects, keys, a token's last use) that the way back
 * restores. The list is of what flows, so a route added later is held until it is named here. */
export type HoldScope='all'|'management';

/** An environment's gateway route to Auth, REST, Storage, Realtime or Functions. Only the
 * runtime name form (`e_` and 24 hex digits) counts, so `/v1/...` and `/mcp` never match. */
export const DATA_PLANE=/^\/e_[a-f0-9]{24}\/(auth|rest|storage|realtime|functions)\/v1(\/|$)/;

/** The scope a hold marker names. The marker lab/upgrade.py writes before this field existed
 * (`{phase,since}`), an empty one and one that does not parse all mean `all`, and so does any
 * scope this version does not know: an unknown scope fails closed to the whole hold. */
export function holdScope(text:string):HoldScope {
 try{return (JSON.parse(text) as {scope?:unknown}|null)?.scope==='management'?'management':'all';}
 catch{return 'all';}
}

/** The hold in force, or undefined, while a new version waits for its post-start health checks
 * (lab/upgrade.py). Until then application traffic is held, so nothing an application writes
 * lands on a version that is not trusted yet and the automatic way back loses nothing; a marker
 * scoped to `management` lets the tenant data plane through (HoldScope).
 *
 * The marker counts only while the upgrade state says a confirmation is pending. A marker left
 * behind by a crash, or by an older release that never removes it, is ignored once the state
 * moved on, and so is an unreadable state: a stale marker must never wedge an installation, so
 * everything but a readable pending state fails open. Once the state is pending the marker's
 * scope fails closed (holdScope). The answer is kept for `ttlMs`, so the check costs one read a
 * second and a removed or rescoped marker takes effect without a restart. The cache runs on a
 * monotonic clock (performance.now): a wall clock stepped back must not keep a cached answer,
 * such as "no hold", past its time. */
export function upgradeHoldScope(marker=HOLD_MARKER,state=UPGRADE_STATE,{ttlMs=1000,now=()=>performance.now()}:{ttlMs?:number;now?:()=>number}={}) {
 let checked=-Infinity,scope:HoldScope|undefined;
 return ():HoldScope|undefined=>{
  const at=now();
  if(at-checked<ttlMs)return scope;
  checked=at;
  try{
   statSync(marker);
   if(!PENDING.includes(String((readJsonCached(state) as {phase?:unknown}|null)?.phase)))scope=undefined;
   // A marker that exists but cannot be read holds everything.
   else try{scope=holdScope(readFileSync(marker,'utf8'));}catch{scope='all';}
  }
  catch{scope=undefined;}
  return scope;
 };
}

/** True while any hold is in force (upgradeHoldScope), whatever its scope. */
export function upgradeHold(marker=HOLD_MARKER,state=UPGRADE_STATE,options:{ttlMs?:number;now?:()=>number}={}) {
 const scope=upgradeHoldScope(marker,state,options);
 return ():boolean=>scope()!==undefined;
}

export const PAUSED_CHANGES='Sbarbase is confirming an update, so changes are paused until it is confirmed. '+
 'If it returns to the previous version, a change made now would be lost. Try again in a few minutes.';
const READS=['GET','HEAD','OPTIONS'];
/** Management changes that write only .lab/upgrades, which the snapshot does not restore. */
const UPDATE_CHANGES=['POST /management/v1/updates/rollback','POST /management/v1/updates/check','PUT /management/v1/updates/settings'];

/** Whether a management request passes while traffic is held. Reads always do, so the operator
 * can watch the update; so do the operator's sign-in, token refresh and sign-out (the
 * management Auth realm, which the way back does not restore), "roll back", "check now" and the
 * update settings, which the way back does not restore either. Every other change would land in
 * the control state that the automatic way back restores from its snapshot, and would be lost
 * silently, so it waits. */
export function passesHold(method:string,path:string):boolean {
 return READS.includes(method)||path.startsWith('/management/auth/')||UPDATE_CHANGES.includes(method+' '+path);
}

/** Application requests wait with 503 and Retry-After while `held()` says `all` (or `true`, the
 * form callers used before scopes existed). The management API (the operator's console) answers
 * reads as usual, and management changes get 409 with a sentence saying they are paused (see
 * passesHold), under either scope. A `management` hold lets the tenant data plane through
 * (DATA_PLANE) and holds everything else as `all` does. */
export function holdApplication(handler:(request:Request)=>Promise<Response>,held:()=>boolean|HoldScope|undefined) {
 return async(request:Request):Promise<Response>=>{
  const scope=held();
  if(!scope)return handler(request);
  const path=new URL(request.url).pathname;
  if(scope==='management'&&DATA_PLANE.test(path))return handler(request);
  if(path.startsWith('/management/'))
   return passesHold(request.method,path)?handler(request):reply(409,{message:PAUSED_CHANGES});
  return withCors(Response.json({message:'Sbarbase is confirming an upgrade; try again shortly'},
   {status:503,headers:{'retry-after':'5','cache-control':'no-store'}}),request);
 };
}
