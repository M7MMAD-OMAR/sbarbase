import {statSync} from 'node:fs';
import {join} from 'node:path';
import {readJsonCached} from '../http/cached-json';
import {reply} from '../control/auth';
import {PENDING,UPDATES_DIRECTORY} from '../control/updates';
import {withCors} from './handler';

export const HOLD_MARKER=join(UPDATES_DIRECTORY,'hold');
export const UPGRADE_STATE=join(UPDATES_DIRECTORY,'state.json');

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

/** Application requests wait with 503 and Retry-After while `held()`. The management API (the
 * operator's console) answers reads as usual, and management changes get 409 with a sentence
 * saying they are paused (see passesHold). */
export function holdApplication(handler:(request:Request)=>Promise<Response>,held:()=>boolean) {
 return async(request:Request):Promise<Response>=>{
  if(!held())return handler(request);
  const path=new URL(request.url).pathname;
  if(path.startsWith('/management/'))
   return passesHold(request.method,path)?handler(request):reply(409,{message:PAUSED_CHANGES});
  return withCors(Response.json({message:'Sbarbase is confirming an upgrade; try again shortly'},
   {status:503,headers:{'retry-after':'5','cache-control':'no-store'}}),request);
 };
}
