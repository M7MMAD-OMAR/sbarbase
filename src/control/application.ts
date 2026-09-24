import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementIdentity} from './auth';
import {controlHandler} from './handler';
import {managedGateway,routeWithPlacement} from '../gateway/managed';
import {createGateway,type EnvironmentRoute} from '../gateway/handler';
import {observedGateway,RequestLog} from '../gateway/observe';
import type {ContainerReader} from './observe';

type ManagementRealm={auth:string;anonymousToken:string;publishableKey:string};

/** Compose a fixed management identity realm and scoped application routes.
 * The installer supplies all upstream addresses. No privileged Auth admin route
 * or signup is exposed. Intended for a loopback server until edge controls exist.
 */
export function application(catalog:Catalog,keys:KeyStore,realm:ManagementRealm,
 resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch,studioKey?:()=>Buffer,
 requests=new RequestLog(),containers?:ContainerReader) {
 const auth=new URL(realm.auth);
 if(!['http:','https:'].includes(auth.protocol)||auth.username||auth.password||auth.search||auth.hash||auth.pathname!=='/')
  throw new Error('Invalid management Auth endpoint');
 const identityTransport=(async(input,init)=>{
  if(String(input)!=='http://management.internal/auth/v1/user')throw new Error('Unexpected identity request');
  return transport(new URL('/user',auth),init);
 }) as typeof fetch;
 const identity=managementIdentity('http://management.internal',realm.publishableKey,identityTransport);
 const control=controlHandler(catalog,keys,identity,runtime=>{
  const routing=catalog.runtimeRouting(runtime);
  if(routing.maintenance)throw new Error('Runtime under maintenance');
  const route=routeWithPlacement(resolve(runtime),routing);
  if(!route||!route.enabled)throw new Error('Runtime routing unavailable');
  return [...(route.storage?['auth','rest','storage'] as const:['auth','rest'] as const),...(route.realtime?['realtime'] as const:[]),...(route.functions?['functions'] as const:[])];
 },studioKey,requests,containers);
 // Each environment's answers are counted for its logs and metrics (src/gateway/observe.ts).
 const gateway=observedGateway(managedGateway(catalog,keys,resolve,transport),requests,runtime=>{
  try{return !!resolve(runtime);}catch{return false;}
 });
 const login=createGateway(new Map([['management',{
  auth:realm.auth,rest:realm.auth,keys:[realm.publishableKey],anonymousToken:realm.anonymousToken,enabled:true
 }]]),transport);
 const methods:Record<string,readonly string[]>={token:['POST'],user:['GET'],logout:['POST'],settings:['GET']};
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  if(path.startsWith('/management/auth/')) {
   const route=path.match(/^\/management\/auth\/v1\/([a-z]+)$/)?.[1];
   if(!route||!methods[route])return Response.json({message:'Unknown authentication route'},{status:404});
   if(!methods[route].includes(request.method))return Response.json({message:'Method not allowed'},{status:405});
   const response=await login(request);
   const headers=new Headers(response.headers);headers.set('cache-control','no-store');
   // The operator login is for the console on this origin only; no other page may call it.
   for(const name of [...headers.keys()])if(name.startsWith('access-control-'))headers.delete(name);
   headers.set('x-content-type-options','nosniff');
   return new Response(response.body,{status:response.status,headers});
  }
  if(path.startsWith('/management/'))return control(request);
  return gateway(request);
 };
}
