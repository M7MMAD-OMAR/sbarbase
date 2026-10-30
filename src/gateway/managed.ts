import {ConcurrencyGate} from './concurrency';
import {GATEWAY} from './shares';
import {Catalog} from '../control/catalog';
import {resolveRuntimePlacement,placementServices,type RuntimeRouting,type ResolvedPlacement} from '../control/placement';
import {KeyStore} from '../control/keys';
import {createGateway,gatewayKeyAccess,withCors,type EnvironmentRoute} from './handler';
import type {ServiceActivity} from './activity';
import type {OnDemandService} from '../control/on-demand-policy';

/** What the gateway needs of On demand mode (src/control/on-demand.ts): whether a service is saved as On demand, and a way to wake it. */
export type OnDemandGate={
 isOnDemand(runtime:string,service:OnDemandService):boolean;
 ensure(runtime:string,service:OnDemandService):Promise<{ok:true}|{ok:false;status:number;message:string;retryAfter?:number}>;
 /** A Functions call with no API key may wake the runtime only for a function its owner made public (verify_jwt off). */
 publicFunction?(runtime:string,name:string):boolean;
};

/** A guaranteed share per environment (8 unless the catalog records another), borrowing up to 24
 * while the neighbours' shares stay free (docs/engineering/FAIR-SHARE-ADMISSION.md). */
export const applicationConcurrency=new ConcurrencyGate(GATEWAY.share,GATEWAY.total,30_000,30_000,
 {ceiling:GATEWAY.ceiling,headroom:GATEWAY.headroom,recentMs:GATEWAY.recentMs});

/** Trusted in-process operator hook. It does not fence upstream SQL or other processes. */
export function pauseManagedEnvironment(runtime:string) {
 return applicationConcurrency.pause(runtime);
}

/** A staged placement overrides the installer's endpoints, storage included, so a moved
 * runtime never keeps its old storage route. */
export function routeWithPlacement(configured:EnvironmentRoute|undefined,routing:RuntimeRouting,resolved?:ResolvedPlacement) {
 if(routing.placement&&'version' in routing.placement)throw new Error('Native dedicated placement is not admitted');
 const placement=resolved?placementServices(resolved):routing.placement;
 if(placement&&'version' in placement)throw new Error('Native dedicated placement is not admitted');
 return configured&&placement?{...configured,...placement,storage:placement.storage}:configured;
}

/** Runtime configuration comes from the trusted installer, never HTTP input.
 * Resolve on every request so routing does not outlive its control-plane state.
 */
export function managedGateway(catalog:Catalog,keys:KeyStore,resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch, concurrency=applicationConcurrency, activity?:ServiceActivity, onDemand?:OnDemandGate) {
 keys.useRuntimeEpoch(runtime=>catalog.runtimeEpoch(runtime));
 // Its own refusals carry the browser headers too, so a page sees the status, not a network error.
 return async(request:Request):Promise<Response>=>withCors(await route(request),request);
 async function route(request:Request):Promise<Response> {
  const runtime=new URL(request.url).pathname.split('/')[1];
  try {
   if(!runtime||!catalog.runtimeReady(runtime))
    // A deleted environment's keys are revoked, so its apps get the answer a revoked key gets.
    return runtime&&catalog.runtimeDeleted(runtime)?Response.json({message:'Invalid API key'},{status:401})
     :Response.json({message:'Unknown environment'},{status:404});
   const match=new URL(request.url).pathname.match(/^\/([a-z][a-z0-9_]{1,30})\/(auth|rest|storage|realtime|functions)\/v1(\/.*)?$/);
   if(!match||!match[2])return Response.json({message:'Unknown route'},{status:404});
   const epoch=catalog.runtimeEpoch(runtime);
   const allowed=()=>catalog.runtimeReady(runtime)&&catalog.runtimeEpoch(runtime)===epoch;
   const access=gatewayKeyAccess(request,match[2],match[3]||'/',key=>keys.resolve(runtime,key));
   if(access instanceof Response)return access;
   const routing=catalog.runtimeRouting(runtime);
   const resolved=resolveRuntimePlacement(runtime,routing);
   if(resolved.profile==='native-dedicated')return Response.json({message:access.apiKey===null?'Environment routing unavailable':'Native dedicated placement is not admitted'},
    {status:503,headers:{'cache-control':'no-store'}});
   if(routing.maintenance)return Response.json({message:'Environment temporarily paused'},
    {status:503,headers:{'retry-after':'1','cache-control':'no-store'}});
   let route=routeWithPlacement(resolve(runtime),routing,resolved);
   if(!route) return Response.json({message:'Environment routing unavailable'},{status:503});
   // Edge Functions being turned off: calls that have not started wait out the drain, so the ones running can end.
   if(activity&&match[2]==='functions'&&request.method!=='OPTIONS'&&activity.isDraining(runtime))
    return Response.json({message:'Edge Functions are being turned off. Retry shortly.'},{status:503,headers:{'retry-after':'2','cache-control':'no-store'}});
   // A service saved as On demand is started by the first request that carries a verified key, and held until it answers. A Functions call without a
   // key may do the same only for a function its owner made public. Nothing else ever wakes anything.
   const wakes:OnDemandService|undefined=match[2]==='functions'?'functions':match[2]==='realtime'?'realtime':undefined;
   if(onDemand&&wakes&&request.method!=='OPTIONS'&&onDemand.isOnDemand(runtime,wakes)){
    const name=(match[3]||'/').split('/')[1]??'';
    const may=access.apiKey!==null||(wakes==='functions'&&access.keylessFunction&&!!onDemand.publicFunction?.(runtime,name));
    if(may){
     const outcome=await onDemand.ensure(runtime,wakes);
     if(!outcome.ok)return Response.json({message:outcome.message},{status:outcome.status,
      headers:{'cache-control':'no-store',...(outcome.retryAfter?{'retry-after':String(outcome.retryAfter)}:{})}});
     if(!allowed())return Response.json({message:'Invalid API key'},{status:401});
     route=routeWithPlacement(resolve(runtime),catalog.runtimeRouting(runtime),resolved);
     if(!route) return Response.json({message:'Environment routing unavailable'},{status:503});
    }
   }
   // A request that carried a verified API key and reached a running service counts as use of it (src/gateway/activity.ts).
   // A preflight and a call without a key do not: anyone can send those.
   // Edge Functions: a call with a verified key, or without one to a function its owner made public. Realtime's broadcast API: a call with a verified key.
   // A preflight and anything else keyless do not count: anyone can send those.
   if(activity&&request.method!=='OPTIONS'){
    const name=(match[3]||'/').split('/')[1]??'';
    if(match[2]==='functions'&&route.functions&&(access.apiKey!==null||(access.keylessFunction&&!!onDemand?.publicFunction?.(runtime,name))))activity.touch(runtime,'functions');
    if(match[2]==='realtime'&&route.realtime&&access.apiKey!==null)activity.touch(runtime,'realtime');
   }
   const guardedTransport=(async(input,init)=>{
    if(!allowed())return Response.json({message:'Invalid API key'},{status:401});
    const response=await transport(input,init);
    if(!allowed()){void response.body?.cancel().catch(()=>{});return Response.json({message:'Invalid API key'},{status:401});}
    return new Response(response.body?epochBody(response.body,allowed):null,{status:response.status,headers:response.headers});
   }) as typeof fetch;
   return await createGateway(new Map([[runtime,route]]),guardedTransport,
    (environment,key)=>allowed()?keys.resolve(environment,key):null,10_000,concurrency)(request);
  } catch {
   return Response.json({message:'Environment routing unavailable'},{status:503});
  }
 }
}

/** Check the captured runtime generation whenever downstream asks for another chunk. */
function epochBody(body:ReadableStream<Uint8Array>,allowed:()=>boolean):ReadableStream<Uint8Array> {
 const reader=body.getReader();let finished=false;
 const close=(cancel:boolean)=>{if(finished)return;finished=true;if(cancel)void reader.cancel().catch(()=>{});reader.releaseLock();};
 return new ReadableStream<Uint8Array>({
  async pull(controller){
   try{
    if(!allowed())throw new Error('Runtime access revoked');
    const chunk=await reader.read();
    if(!allowed())throw new Error('Runtime access revoked');
    if(chunk.done){controller.close();close(false);}else controller.enqueue(chunk.value);
   }catch(error){controller.error(error);close(true);}
  },cancel(){close(true);}
 },{highWaterMark:0});
}
