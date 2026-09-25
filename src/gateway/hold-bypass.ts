import {readFileSync} from 'node:fs';
import {timingSafeEqual} from 'node:crypto';
import {holdApplication} from './hold';

export const PROBE_TOKEN='.lab/upgrades/probe-token';
export const PROBE_HEADER='x-sbarbase-upgrade-probe';
// Only these two: the Functions route hands the caller's own headers to user code, and Storage
// and Realtime are probed directly by the supervisor already.
const PROBED=/^\/[a-z][a-z0-9_]{1,30}\/(rest|auth)\/v1(\/|$)/;

/** The supervisor's confirmation probe (lab/upgrade_health.py). While an upgrade waits for its
 * health checks, one request per routed environment goes through the gateway exactly as an
 * application's would (routing, key resolution, the proxy), past the hold that stops every other
 * application request, so a version whose gateway or key store is broken is never confirmed.
 *
 * The probe carries a random per-start token, written 0600 by lab/upgrade.py before_start and
 * removed once the start is confirmed or goes back, in PROBE_HEADER and as its API key. It is
 * accepted only while the hold is on, so the token is worth nothing once the upgrade confirmed,
 * and only from a caller on this host: the TLS proxy (deploy/console-tls-proxy.ts) sets
 * x-forwarded-* on every request it passes to the loopback listener, and a request carrying any
 * of them is never the probe. The token stands in for a publishable key because the key store
 * keeps digests only; a publishable key grants what any application visitor already has, and
 * the stored keys are still looked up first (KeyStore.confirmationProbe).
 *
 * The token is reread at most once per `ttlMs`, as the hold itself is. */
export function confirmationProbe(held:()=>boolean,path=PROBE_TOKEN,{ttlMs=1000,now=Date.now}:{ttlMs?:number;now?:()=>number}={}) {
 let checked=-Infinity,token:string|undefined;
 const current=()=>{
  const at=now();
  if(at-checked>=ttlMs) {
   checked=at;
   try{const text=readFileSync(path,'utf8').trim();token=/^[A-Za-z0-9_-]{32,256}$/.test(text)?text:undefined;}
   catch{token=undefined;}
  }
  return token;
 };
 const same=(value:string|null)=>{
  const expected=current();
  if(!expected||!value)return false;
  const left=Buffer.from(value),right=Buffer.from(expected);
  return left.length===right.length&&timingSafeEqual(left,right);
 };
 return {
  /** True for the supervisor's own probe request. */
  matches(request:Request):boolean {
   if(!held())return false;
   for(const name of request.headers.keys())if(name.startsWith('x-forwarded-')||name==='forwarded')return false;
   if(!PROBED.test(new URL(request.url).pathname))return false;
   return same(request.headers.get(PROBE_HEADER));
  },
  /** The probe's token as an API key, while the hold is on: only the probe reaches the gateway then. */
  key(value:string):boolean {return held()&&same(value);},
 };
}

/** holdApplication, except that the supervisor's probe reaches the gateway. The management API
 * is never the probe's path, so whatever the hold decides for it stays as it is. */
export function holdExceptProbe(handler:(request:Request)=>Promise<Response>,held:()=>boolean,
 probe:{matches(request:Request):boolean}) {
 const holding=holdApplication(handler,held);
 return (request:Request):Promise<Response>=>probe.matches(request)?handler(request):holding(request);
}
