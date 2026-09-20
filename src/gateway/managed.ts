import {Catalog} from '../control/catalog';
import {KeyStore} from '../control/keys';
import {createGateway,type EnvironmentRoute} from './handler';

/** Runtime configuration comes from the trusted installer, never HTTP input.
 * Resolve on every request so routing does not outlive its control-plane state.
 */
export function managedGateway(catalog:Catalog,keys:KeyStore,resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch) {
 return async(request:Request):Promise<Response>=>{
  const runtime=new URL(request.url).pathname.split('/')[1];
  try {
   if(!runtime||!catalog.runtimeReady(runtime))
    return Response.json({message:'Unknown environment'},{status:404});
   const route=resolve(runtime);
   if(!route) return Response.json({message:'Environment routing unavailable'},{status:503});
   return await createGateway(new Map([[runtime,route]]),transport,
    (environment,key)=>keys.resolve(environment,key)==='publishable')(request);
  } catch {
   return Response.json({message:'Environment routing unavailable'},{status:503});
  }
 };
}
