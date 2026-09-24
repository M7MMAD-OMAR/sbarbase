import {ConcurrencyGate} from './concurrency';
import {GATEWAY} from './shares';
import {Catalog} from '../control/catalog';
import type {RuntimeRouting} from '../control/placement';
import {KeyStore} from '../control/keys';
import {createGateway,withCors,type EnvironmentRoute} from './handler';

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
export function routeWithPlacement(configured:EnvironmentRoute|undefined,routing:RuntimeRouting) {
 return configured&&routing.placement?{...configured,...routing.placement,storage:routing.placement.storage}:configured;
}

/** Runtime configuration comes from the trusted installer, never HTTP input.
 * Resolve on every request so routing does not outlive its control-plane state.
 */
export function managedGateway(catalog:Catalog,keys:KeyStore,resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch, concurrency=applicationConcurrency) {
 // Its own refusals carry the browser headers too, so a page sees the status, not a network error.
 return async(request:Request):Promise<Response>=>withCors(await route(request),request);
 async function route(request:Request):Promise<Response> {
  const runtime=new URL(request.url).pathname.split('/')[1];
  try {
   if(!runtime||!catalog.runtimeReady(runtime))
    return Response.json({message:'Unknown environment'},{status:404});
   const routing=catalog.runtimeRouting(runtime);
   if(routing.maintenance)return Response.json({message:'Environment temporarily paused'},
    {status:503,headers:{'retry-after':'1','cache-control':'no-store'}});
   const route=routeWithPlacement(resolve(runtime),routing);
   if(!route) return Response.json({message:'Environment routing unavailable'},{status:503});
   return await createGateway(new Map([[runtime,route]]),transport,
    (environment,key)=>keys.resolve(environment,key)==='publishable',10_000,concurrency)(request);
  } catch {
   return Response.json({message:'Environment routing unavailable'},{status:503});
  }
 }
}
