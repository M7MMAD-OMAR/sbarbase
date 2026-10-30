import {Catalog} from './catalog';
import {resolveRuntimePlacement,placementServices,PlacementUnavailable} from './placement';
import {KeyStore} from './keys';
import {managementPasswordIdentity,hasManagementAccess,reply,type PasswordIdentity} from './auth';
import {managementAuth,type ManagementRealm} from './management-auth';
import {withManagementAuthorization,refreshCurrentManagement} from './management-context';
import {NativeLifecycleAuthority} from './lifecycle-authority';
import {publishDatabaseResponse} from './database-publication';
import {publishReadyEnvironmentResponse} from './ready-publication';
import {publishManagementResponse} from './management-publication';
import type {DirectDatabase} from '../http/database-proxy';
import {controlHandler,type ServicesOptions} from './handler';
import type {InvitationAccounts} from './invitations';
import {managedGateway,routeWithPlacement} from '../gateway/managed';
import type {EnvironmentRoute} from '../gateway/handler';
import {observedGateway,RequestLog} from '../gateway/observe';
import {functionNames} from './functions-read';
import {bootThrough} from './functions-write';
import {usageThrough} from './functions-worker';
import type {ContainerReader} from './observe';
import {AccessTokenStore,tokenEpoch,type AccessToken} from './access-tokens';
import {accessTokenHandler} from './access-token-http';
import {supabaseApi,refOf,inScope} from './supabase-api';
import {developerSql,type SqlRunner} from './agent-sql';
import {mcpEndpoint} from './mcp';
import {publicUrl} from './sign-in';
import {authAdminThrough} from './auth-admin';

/** Access for agents and tools (docs/guides/agents.md): personal access tokens, the
 * Supabase-compatible API under /v1 and the MCP endpoint. Off when no token store is given. */
export type AgentOptions={tokens:AccessTokenStore;sql?:SqlRunner;loopback?:()=>string|undefined;publicUrl?:()=>string;functionsRoot?:string};

/** Compose a fixed management identity realm and scoped application routes.
 * The installer supplies all upstream addresses. No privileged Auth admin route
 * or signup is exposed. Intended for a loopback server until edge controls exist.
 */
export function application(catalog:Catalog,keys:KeyStore,realm:ManagementRealm,
 resolve:(runtime:string)=>EnvironmentRoute|undefined,transport:typeof fetch=fetch,studioKey?:()=>Buffer,
 requests=new RequestLog(),containers?:ContainerReader,accounts?:InvitationAccounts,direct?:DirectDatabase,agents?:AgentOptions,serviceOptions:ServicesOptions={}) {
 const auth=new URL(realm.auth);
 if(!['http:','https:'].includes(auth.protocol)||auth.username||auth.password||auth.search||auth.hash||auth.pathname!=='/')
  throw new Error('Invalid management Auth endpoint');
 const identityTransport=(async(input,init)=>{
  if(String(input)!=='http://management.internal/auth/v1/user')throw new Error('Unexpected identity request');
  // The realm's address is read on each use (PROTOCOL 1, docs/engineering/HANDOVER.md): a replaced management Auth moves it.
  return transport(new URL('/user',new URL(realm.auth)),init);
 }) as typeof fetch;
 const passwordIdentity=managementPasswordIdentity('http://management.internal',realm.publishableKey,identityTransport);
 const identityCache=new WeakMap<Request,Promise<PasswordIdentity|null>>();
 const identify=(request:Request)=>{
  let value=identityCache.get(request);if(!value){value=passwordIdentity(request);identityCache.set(request,value);}return value;
 };
 const identity=async(request:Request)=>{
  await refreshCurrentManagement();
  const actor=await identify(request);return actor&&hasManagementAccess(actor,catalog.managementSecurity)?actor.actor:null;
 };
// A deploy asks the environment's running Edge Functions to start the new version before it goes live (functions-write.ts).
 const functionsWhere=(runtime:string)=>{
  const routing=catalog.runtimeRouting(runtime);
  if(routing.maintenance)return 'maintenance' as const;
  const route=routeWithPlacement(resolve(runtime),routing,resolveRuntimePlacement(runtime,routing));
  return {url:route?.functions?.url,token:route?.serviceToken};
 };
 // The Authentication pages that use the environment's own Auth admin API (Audit Logs, Single sign-on) reach it the same way.
 const authWhere=(runtime:string)=>{
  const routing=catalog.runtimeRouting(runtime);
  if(routing.maintenance)return 'maintenance' as const;
  const route=routeWithPlacement(resolve(runtime),routing,resolveRuntimePlacement(runtime,routing));
  return {url:route?.auth,token:route?.serviceToken};
 };
 const authAdmin=authAdminThrough(authWhere,transport);
 const functionsBoot=bootThrough(functionsWhere,transport);
 const functionsUsage=usageThrough(functionsWhere,transport);
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
 },studioKey,requests,containers,accounts,direct,functionsBoot,functionsUsage,authAdmin,serviceOptions);
 // Each environment's answers are counted for its logs and metrics (src/gateway/observe.ts).
 const gateway=observedGateway(managedGateway(catalog,keys,resolve,transport,undefined,serviceOptions.activity),requests,runtime=>{
  try{
   const routing=catalog.runtimeRouting(runtime);
   if(routing.maintenance)return false;
   placementServices(resolveRuntimePlacement(runtime,routing));return !!resolve(runtime);
  }catch{return false;}
 },undefined,functionNames(agents?.functionsRoot??'.lab/upstream/functions'));
 const login=managementAuth(catalog,realm,identify,transport);
 // Agents: a token acts as its owner through the same console routes, with their checks, and is
 // asked again before anything it changed is published. A separate route table: no token can
 // reach the token routes themselves.
 const tokenRoutes=agents?accessTokenHandler(catalog,agents.tokens,identity):undefined;
 const management=(request:Request)=>tokenRoutes&&/^\/management\/v1\/access-tokens(\/|$)/.test(new URL(request.url).pathname)?tokenRoutes(request):control(request);
 let v1:((request:Request)=>Promise<Response>)|undefined,mcp:((request:Request)=>Promise<Response>)|undefined;
 if(agents){
  const {tokens}=agents,epoch=tokenEpoch(catalog.managementSecurity);
  const callers=new WeakMap<Request,AccessToken>();
  const tokenControl=controlHandler(catalog,keys,async request=>callers.get(request)?.actor??null,runtime=>{
   const routing=catalog.runtimeRouting(runtime);
   const route=routeWithPlacement(resolve(runtime),routing,resolveRuntimePlacement(runtime,routing));
   if(!route||!route.enabled||routing.maintenance)throw new PlacementUnavailable();
   return [...(route.storage?['auth','rest','storage'] as const:['auth','rest'] as const),...(route.realtime?['realtime'] as const:[]),...(route.functions?['functions'] as const:[])];
  },studioKey,requests,containers,accounts,direct);
  const dispatch=async(token:AccessToken,method:string,path:string,body?:unknown)=>{
   const internal=new Request('http://sbarbase.internal'+path,{method,
    ...(body===undefined?{}:{headers:{'content-type':'application/json'},body:JSON.stringify(body)})});
   callers.set(internal,token);
   const current=()=>tokens.alive(token.id,epoch);
   const response=await withManagementAuthorization(current,()=>tokenControl(internal),async()=>{if(!current())throw new Error('Forbidden');});
   return current()?withManagementAuthorization(current,()=>publishManagementResponse(catalog,publishReadyEnvironmentResponse(catalog,publishDatabaseResponse(catalog,response))))
    :reply(401,{message:'The access token is no longer valid'});
  };
  const base=agents.publicUrl??publicUrl;
  v1=supabaseApi({catalog,tokens,dispatch,sql:agents.sql??developerSql(),publicUrl:base,functionsRoot:agents.functionsRoot});
  const loopback=agents.loopback??(()=>undefined);
  mcp=mcpEndpoint({loopback,resolve:bearer=>tokens.resolve(bearer,epoch),
   admit:token=>token?catalog.managementSecurity.rate('agent:token:'+token.id,300,60_000):catalog.managementSecurity.rate('agent:invalid',600,60_000),
   projectUrl:async(token,ref)=>{
    const entry=catalog.agentDirectory(token.actor).find(item=>inScope(token,item)&&refOf(item.environment)===ref);
    if(!entry?.runtime)throw new Error('Project not found');
    return `${base().replace(/\/+$/,'')}/${entry.runtime}`;
   }});
 }
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  if(path.startsWith('/management/auth/')) {
   if(!path.startsWith('/management/auth/v1/'))return reply(404,{message:'Unknown authentication route'});
   return login(request);
  }
  if(path.startsWith('/management/')){
   try {
    if(!catalog.managementSecurity.rate('management:global',1200,60000))return reply(429,{message:'Too many management requests'});
    // Invitation redemption is public by design; every protected route below enforces the current authentication policy.
    if(path!=='/management/invitations/redeem'){
     const actor=await identify(request);if(!actor)return reply(401,{message:'Password authentication required'});
     if(!hasManagementAccess(actor,catalog.managementSecurity))return reply(403,{message:'MFA verification required',code:'mfa_required'});
     if(!catalog.managementSecurity.rate('management:actor:'+actor.actor,120,60000))return reply(429,{message:'Too many management requests'});
     const bearer=request.headers.get('authorization'),epoch=catalog.managementSecurity.epoch(actor.actor),policyEpoch=catalog.managementSecurity.policy().epoch;
     const passwordMode=actor.aal==='aal1';
     const grantedFactor=catalog.managementSecurity.grantedFactor(actor.actor,actor.session,actor.verifiedAt,
      actor.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp').map(factor=>factor.id));
     const grantedFactors=new Set(grantedFactor?[grantedFactor]:[]);
     let fresh:PasswordIdentity|null=actor;
     const current=()=>!!fresh&&request.headers.get('authorization')===bearer&&!request.signal.aborted&&
      catalog.managementSecurity.epoch(actor.actor)===epoch&&catalog.managementSecurity.policy().epoch===policyEpoch&&hasManagementAccess(fresh,catalog.managementSecurity)&&
      (passwordMode||
      catalog.managementSecurity.granted(actor.actor,actor.session,actor.verifiedAt,
       fresh.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp'&&grantedFactors.has(factor.id)).map(factor=>factor.id)));
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
     const invoke=()=>withManagementAuthorization(current,()=>management(request),refresh);
     const response=await (authority?authority.run(invoke):invoke());
     if(authority){await authority.refresh();authority.assertCurrent();}
     if(current())await refresh();
      return current()?withManagementAuthorization(current,()=>publishManagementResponse(catalog,publishReadyEnvironmentResponse(catalog,publishDatabaseResponse(catalog,response))))
      :reply(403,{message:'MFA verification required',code:'mfa_required'});
    }
    return management(request);
   }catch(error){return error instanceof Error&&error.message==='Forbidden'
    ?reply(403,{message:'MFA verification required',code:'mfa_required'}):reply(503,{message:'Authentication unavailable'});}
  }
  if(v1&&(path==='/v1'||path.startsWith('/v1/')))return v1(request);
  if(mcp&&path==='/mcp')return mcp(request);
  return gateway(request);
 };
}
