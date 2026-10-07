import {Catalog} from './catalog';
import {resolveRuntimePlacement,placementServices,PlacementUnavailable} from './placement';
import {KeyStore} from './keys';
import {managementPasswordIdentity,hasManagementMfa,reply,type PasswordIdentity} from './auth';
import {managementAuth,type ManagementRealm} from './management-auth';
import {withManagementAuthorization,refreshCurrentManagement} from './management-context';
import {NativeLifecycleAuthority} from './lifecycle-authority';
import {publishDatabaseResponse} from './database-publication';
import {publishReadyEnvironmentResponse} from './ready-publication';
import {publishManagementResponse} from './management-publication';
import type {DirectDatabase} from '../http/database-proxy';
import {controlHandler} from './handler';
import type {InvitationAccounts} from './invitations';
import {managedGateway,routeWithPlacement} from '../gateway/managed';
import type {EnvironmentRoute} from '../gateway/handler';
import {observedGateway,RequestLog} from '../gateway/observe';
import type {ContainerReader} from './observe';

/** Compose a fixed management identity realm and scoped application routes.
 * The installer supplies all upstream addresses. No privileged Auth admin route
 * or signup is exposed. Intended for a loopback server until edge controls exist.
 */
export function application(catalog:Catalog,keys:KeyStore,realm:ManagementRealm,
 resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch,studioKey?:()=>Buffer,
 requests=new RequestLog(),containers?:ContainerReader,accounts?:InvitationAccounts,direct?:DirectDatabase) {
 const auth=new URL(realm.auth);
 if(!['http:','https:'].includes(auth.protocol)||auth.username||auth.password||auth.search||auth.hash||auth.pathname!=='/')
  throw new Error('Invalid management Auth endpoint');
 const identityTransport=(async(input,init)=>{
  if(String(input)!=='http://management.internal/auth/v1/user')throw new Error('Unexpected identity request');
  return transport(new URL('/user',auth),init);
 }) as typeof fetch;
 const passwordIdentity=managementPasswordIdentity('http://management.internal',realm.publishableKey,identityTransport);
 const identityCache=new WeakMap<Request,Promise<PasswordIdentity|null>>();
 const identify=(request:Request)=>{
  let value=identityCache.get(request);if(!value){value=passwordIdentity(request);identityCache.set(request,value);}return value;
 };
 const identity=async(request:Request)=>{
  await refreshCurrentManagement();
  const actor=await identify(request);return actor&&hasManagementMfa(actor,catalog.managementSecurity)?actor.actor:null;
 };
 const control=controlHandler(catalog,keys,identity,runtime=>{
  try {
   const routing=catalog.runtimeRouting(runtime);
   const resolved=resolveRuntimePlacement(runtime,routing);
   placementServices(resolved);
   if(routing.maintenance)throw new Error('Runtime under maintenance');
   const route=routeWithPlacement(resolve(runtime),routing,resolved);
   if(!route||!route.enabled)throw new Error('Runtime routing unavailable');
   return [...(route.storage?['auth','rest','storage'] as const:['auth','rest'] as const),...(route.realtime?['realtime'] as const:[]),...(route.functions?['functions'] as const:[])];
  }catch(error){throw error instanceof PlacementUnavailable?error:new PlacementUnavailable();}
 },studioKey,requests,containers,accounts,direct);
 // Each environment's answers are counted for its logs and metrics (src/gateway/observe.ts).
 const gateway=observedGateway(managedGateway(catalog,keys,resolve,transport),requests,runtime=>{
  try{
   const routing=catalog.runtimeRouting(runtime);
   if(routing.maintenance)return false;
   placementServices(resolveRuntimePlacement(runtime,routing));return !!resolve(runtime);
  }catch{return false;}
 });
 const login=managementAuth(catalog,realm,identify,transport);
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  if(path.startsWith('/management/auth/')) {
   if(!path.startsWith('/management/auth/v1/'))return reply(404,{message:'Unknown authentication route'});
   return login(request);
  }
  if(path.startsWith('/management/')){
   try {
    if(!catalog.managementSecurity.rate('management:global',1200,60000))return reply(429,{message:'Too many management requests'});
    // Invitation redemption is public by design; every protected route below requires MFA.
    if(path!=='/management/invitations/redeem'){
     const actor=await identify(request);if(!actor)return reply(401,{message:'Password authentication required'});
     if(!hasManagementMfa(actor,catalog.managementSecurity))return reply(403,{message:'MFA verification required',code:'mfa_required'});
     if(!catalog.managementSecurity.rate('management:actor:'+actor.actor,120,60000))return reply(429,{message:'Too many management requests'});
     const bearer=request.headers.get('authorization'),epoch=catalog.managementSecurity.epoch(actor.actor);
     const grantedFactor=catalog.managementSecurity.grantedFactor(actor.actor,actor.session,actor.verifiedAt,
      actor.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp').map(factor=>factor.id));
     const grantedFactors=new Set(grantedFactor?[grantedFactor]:[]);
     let fresh:PasswordIdentity|null=actor;
     const current=()=>!!fresh&&request.headers.get('authorization')===bearer&&!request.signal.aborted&&
      catalog.managementSecurity.epoch(actor.actor)===epoch&&hasManagementMfa(fresh,catalog.managementSecurity)&&
      catalog.managementSecurity.granted(actor.actor,actor.session,actor.verifiedAt,
       fresh.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp'&&grantedFactors.has(factor.id)).map(factor=>factor.id));
     const refresh=async()=>{
      if(!current())throw new Error('Forbidden');
      // Invalidate the old native observation while the exact original bearer is looked up.
      fresh=null;
      const renewed=await passwordIdentity(new Request('http://management.internal',
       {headers:{authorization:bearer!},signal:request.signal}));
      if(!renewed||renewed.actor!==actor.actor||renewed.session!==actor.session||renewed.aal!==actor.aal||
       renewed.expiresAt!==actor.expiresAt||renewed.passwordAt!==actor.passwordAt||renewed.verifiedAt!==actor.verifiedAt)
       throw new Error('Forbidden');
      fresh=renewed;
      if(!current()){fresh=null;throw new Error('Forbidden');}
     };
     const lifecycleMutation=/^\/management\/v1\/environments\/[a-f0-9-]{36}(?:\/(?:restore|purge|resume))?$/.test(path)&&['POST','DELETE'].includes(request.method);
     const authority=lifecycleMutation&&catalog.lifecycleAuthorizationStore
      ?await NativeLifecycleAuthority.request(catalog,catalog.lifecycleAuthorizationStore,request,transport):null;
     if(authority&&authority.actor()!==actor.actor)return reply(403,{message:'Forbidden'});
     const invoke=()=>withManagementAuthorization(current,()=>control(request),refresh);
     const response=await (authority?authority.run(invoke):invoke());
     if(authority){await authority.refresh();authority.assertCurrent();}
     if(current())await refresh();
      return current()?withManagementAuthorization(current,()=>publishManagementResponse(catalog,publishReadyEnvironmentResponse(catalog,publishDatabaseResponse(catalog,response))))
      :reply(403,{message:'MFA verification required',code:'mfa_required'});
    }
    return control(request);
   }catch(error){return error instanceof Error&&error.message==='Forbidden'
    ?reply(403,{message:'MFA verification required',code:'mfa_required'}):reply(503,{message:'Authentication unavailable'});}
  }
  return gateway(request);
 };
}
