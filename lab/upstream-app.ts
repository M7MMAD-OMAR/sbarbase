import {readFileSync} from 'node:fs';
import {PressureMonitor} from '../src/gateway/pressure';
import {applicationConcurrency} from '../src/gateway/managed';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {readJsonCached} from '../src/http/cached-json';
import {studioKey,studioProxy,studioUpstream} from '../src/control/studio';
import {realtimeUpgrade} from '../src/gateway/realtime';
import {routeWithPlacement} from '../src/gateway/managed';
import type {EnvironmentRoute} from '../src/gateway/handler';

export const managementPublishableKey='sb_publishable_sbarbase_local_management';
export function internalToken(secret:string,role:string,claims:Record<string,unknown>={}) {
 const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role,iss:'sbarbase-internal',...claims});
 return message+'.'+createHmac('sha256',secret).update(message).digest('base64url');
}
/** Realtime refuses a token without an expiry. Like Supabase's own anon key it lasts ten years,
 * and it is minted once per process from the environment's JWT secret. */
const realtimeTokens=new Map<string,string>();
export function realtimeToken(secret:string) {
 let token=realtimeTokens.get(secret);
 if(!token){const now=Math.floor(Date.now()/1000);token=internalToken(secret,'anon',{iat:now,exp:now+10*365*24*3600});realtimeTokens.set(secret,token);}
 return token;
}
/** Studio sessions the runtime recorded (lab/studio.py): addresses of running Studios, and
 * the network gateway address the internal Studio route listens on. */
export function studioState():{upstream?:{host:string;port:number};sessions:Record<string,{url:string}>} {
 try{return readJsonCached('.lab/upstream/studio.json') as any;}catch{return {sessions:{}};}
}
export function openUpstreamApplication() {
 const load=(path:string)=>JSON.parse(readFileSync(path,'utf8'));
 let key:Buffer|undefined;
 const studioSessionKey=()=>key??=studioKey('.secrets/upstream/studio-session.key');
 const secrets=load('.secrets/upstream/runtime.json');
 const management=load('.lab/upstream/management.json');
 const catalog=new Catalog('.lab/upstream/control.sqlite');
 const keys=new KeyStore('.secrets/upstream/managed-keys.sqlite');
 const resolve=(runtime:string):EnvironmentRoute|undefined=>{
  // Refresh private configuration and endpoints after provisioning or restart: the cache
  // rereads either file as soon as it changes.
  const endpoints=readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>;
  const current=readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>};
  if(!endpoints[runtime]||!current.environments[runtime])return undefined;
  const secret=current.environments[runtime].jwt;
  return {...endpoints[runtime],keys:[],anonymousToken:internalToken(secret,'anon'),enabled:true,
   ...(endpoints[runtime].realtime?{realtimeToken:realtimeToken(secret)}:{})};
 };
 const handler=application(catalog,keys,{auth:management.auth,publishableKey:managementPublishableKey,
  anonymousToken:internalToken(secrets.management.jwt,'anon')},resolve,fetch,studioSessionKey);
 const realtime=realtimeUpgrade({
  route:runtime=>{
   if(!catalog.runtimeReady(runtime))return undefined;
   const routing=catalog.runtimeRouting(runtime);
   return routing.maintenance?'maintenance':routeWithPlacement(resolve(runtime),routing);
  },
  verifyKey:(runtime,key)=>keys.resolve(runtime,key)==='publishable'});
  const studio=studioProxy({key:studioSessionKey,allowed:(actor,runtime)=>catalog.studioAllowed(actor,runtime),
   upstream:runtime=>studioState().sessions?.[runtime]?.url});
  const upstream=studioUpstream({
   endpoints:runtime=>(readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>)[runtime],
   secret:runtime=>(readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>}).environments[runtime]?.jwt,
   active:runtime=>!!studioState().sessions?.[runtime]});
  // One monitor per process, over the one application gate: a busy environment's operator notice.
  const pressure=new PressureMonitor(applicationConcurrency,(runtime,saturation)=>catalog.environmentSaturated(runtime,saturation));
  pressure.start();
  return {handler,studio,upstream,realtime,catalog,keys,close(){pressure.stop();catalog.close();keys.close();}};
}
