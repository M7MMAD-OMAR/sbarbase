import {readFileSync} from 'node:fs';
import {PressureMonitor} from '../src/gateway/pressure';
import {applicationConcurrency} from '../src/gateway/managed';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {readJsonCached} from '../src/http/cached-json';
import {studioKey,studioProxy,studioUpstream} from '../src/control/studio';

export const managementPublishableKey='sb_publishable_sbarbase_local_management';
export function internalToken(secret:string,role:string) {
 const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role,iss:'sbarbase-internal'});
 return message+'.'+createHmac('sha256',secret).update(message).digest('base64url');
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
 const handler=application(catalog,keys,{auth:management.auth,publishableKey:managementPublishableKey,
  anonymousToken:internalToken(secrets.management.jwt,'anon')},runtime=>{
  // Refresh private configuration and endpoints after provisioning or restart: the cache
  // rereads either file as soon as it changes.
  const endpoints=readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>;
  const current=readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>};
  if(!endpoints[runtime]||!current.environments[runtime])return undefined;
  return {...endpoints[runtime],keys:[],anonymousToken:internalToken(current.environments[runtime].jwt,'anon'),enabled:true};
  },fetch,studioSessionKey);
  const studio=studioProxy({key:studioSessionKey,allowed:(actor,runtime)=>catalog.studioAllowed(actor,runtime),
   upstream:runtime=>studioState().sessions?.[runtime]?.url});
  const upstream=studioUpstream({
   endpoints:runtime=>(readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>)[runtime],
   secret:runtime=>(readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>}).environments[runtime]?.jwt,
   active:runtime=>!!studioState().sessions?.[runtime]});
  // The gate reads each environment's share from the catalog, so a change applies at the next request.
  applicationConcurrency.useShares(runtime=>catalog.gatewayShare(runtime));
  // One monitor per process, over the one application gate: a busy environment's operator notice.
  const pressure=new PressureMonitor(applicationConcurrency,(runtime,saturation)=>catalog.environmentSaturated(runtime,saturation));
  pressure.start();
  return {handler,studio,upstream,catalog,keys,close(){pressure.stop();catalog.close();keys.close();}};
}
