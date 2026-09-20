import {ConcurrencyGate} from './concurrency';
import {Catalog} from '../control/catalog';
import {KeyStore} from '../control/keys';
import {createGateway,type EnvironmentRoute} from './handler';

const applicationConcurrency=new ConcurrencyGate();

/** Trusted in-process operator hook. It does not fence upstream SQL or other processes. */
export function pauseManagedEnvironment(runtime:string) {
 return applicationConcurrency.pause(runtime);
}

/** Runtime configuration comes from the trusted installer, never HTTP input.
 * Resolve on every request so routing does not outlive its control-plane state.
 */
export function managedGateway(catalog:Catalog,keys:KeyStore,resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch, concurrency=applicationConcurrency) {
 return async(request:Request):Promise<Response>=>{
  const runtime=new URL(request.url).pathname.split('/')[1];
  try {
   if(!runtime||!catalog.runtimeReady(runtime))
    return Response.json({message:'Unknown environment'},{status:404});
   const routing=catalog.runtimeRouting(runtime);
   if(routing.maintenance)return Response.json({message:'Environment temporarily paused'},
    {status:503,headers:{'retry-after':'1','cache-control':'no-store'}});
   const configured=resolve(runtime);
   const route=configured&&routing.placement?{...configured,...routing.placement,storage:routing.placement.storage}:configured;
   if(!route) return Response.json({message:'Environment routing unavailable'},{status:503});
   return await createGateway(new Map([[runtime,route]]),transport,
    (environment,key)=>keys.resolve(environment,key)==='publishable',10_000,concurrency)(request);
  } catch {
   return Response.json({message:'Environment routing unavailable'},{status:503});
  }
 };
}
