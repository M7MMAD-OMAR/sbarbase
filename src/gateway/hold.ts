import {statSync} from 'node:fs';
import {readJsonCached} from '../http/cached-json';
import {withCors} from './handler';

export const HOLD_MARKER='.lab/upgrades/hold';
export const UPGRADE_STATE='.lab/upgrades/state.json';
const PENDING=['applied','rolling_back'];

/** True while a new version waits for its post-start health checks (lab/upgrade.py). Until then
 * application traffic is held, so nothing an application writes lands on a version that is not
 * trusted yet and the automatic way back loses nothing.
 *
 * The marker counts only while the upgrade state says a confirmation is pending. A marker left
 * behind by a crash, or by an older release that never removes it, is ignored once the state
 * moved on, and so is an unreadable state: a stale marker must never wedge an installation, so
 * everything but a readable pending state fails open. The answer is kept for `ttlMs`, so the
 * check costs one stat a second and a removed marker takes effect without a restart. */
export function upgradeHold(marker=HOLD_MARKER,state=UPGRADE_STATE,{ttlMs=1000,now=Date.now}:{ttlMs?:number;now?:()=>number}={}) {
 let checked=-Infinity,held=false;
 return ():boolean=>{
  const at=now();
  if(at-checked<ttlMs)return held;
  checked=at;
  try{statSync(marker);held=PENDING.includes(String((readJsonCached(state) as {phase?:unknown}|null)?.phase));}
  catch{held=false;}
  return held;
 };
}

/** Application requests wait with 503 and Retry-After while `held()`; the management API (the
 * operator's console, its sign-in included) always passes, so the operator can see the status. */
export function holdApplication(handler:(request:Request)=>Promise<Response>,held:()=>boolean) {
 return async(request:Request):Promise<Response>=>{
  if(new URL(request.url).pathname.startsWith('/management/')||!held())return handler(request);
  return withCors(Response.json({message:'Sbarbase is confirming an upgrade; try again shortly'},
   {status:503,headers:{'retry-after':'5','cache-control':'no-store'}}),request);
 };
}
