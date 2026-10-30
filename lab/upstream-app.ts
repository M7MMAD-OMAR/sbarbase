import {installedLifecycleAdmission} from '../src/control/lifecycle-recovery';
import {LifecycleAuthorizationStore} from '../src/control/lifecycle-authority';
import {readFileSync} from 'node:fs';
import {createClient} from '@supabase/supabase-js';
import type {InvitationAccounts} from '../src/control/invitations';
import {AuthClient} from '@supabase/auth-js';
import {PressureMonitor} from '../src/gateway/pressure';
import {applicationConcurrency} from '../src/gateway/managed';
import {ServiceActivity} from '../src/gateway/activity';
import {createServerHealthReader} from '../src/control/server-health';
import {checkUnusedServices} from '../src/control/service-hints';
import {OnDemand} from '../src/control/on-demand';
import {isPublicFunction} from '../src/control/functions';
import {readStats} from '../src/control/services';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {durableInventoryDeriver} from '../src/control/durable-lifecycle';
import {KeyStore} from '../src/control/keys';
import {AccessTokenStore} from '../src/control/access-tokens';
import {application} from '../src/control/application';
import {readJsonCached} from '../src/http/cached-json';
import {DirectDatabase} from '../src/http/database-proxy';
import {studioKey,studioProxy,studioUpstream} from '../src/control/studio';
import {mailTemplateRoute} from '../src/control/mail-templates';
import {catalogRealtimeRoute,realtimeUpgrade} from '../src/gateway/realtime';
import {RequestLog} from '../src/gateway/observe';
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
/** What a secret API key becomes at the gateway (src/gateway/handler.ts): a service_role JWT with
 * an expiry, as Supabase's gateway mints. It is made from the secret read for this request, so a
 * signing key rotation changes it at once, and reused until a quarter of its life is left: an
 * HTTP call gets at least 15 of its 60 minutes, a Realtime socket, which keeps its token for its
 * whole life, at least 6 of its 24 hours. The tokens go only to the environment's own services;
 * its Edge Functions see the token a caller's key was swapped for, as on Supabase. */
const serviceTokens=new Map<string,{token:string;renew:number}>();
export function serviceToken(secret:string,lifetime:number,now=Math.floor(Date.now()/1000)) {
 const key=lifetime+':'+secret,cached=serviceTokens.get(key);
 if(cached&&cached.renew>now)return cached.token;
 if(serviceTokens.size>4096)serviceTokens.clear();
 const token=internalToken(secret,'service_role',{iat:now,exp:now+lifetime});
 serviceTokens.set(key,{token,renew:now+Math.floor(lifetime*3/4)});
 return token;
}
/** Studio sessions the runtime recorded (lab/studio.py): addresses of running Studios, and
 * the network gateway address the internal Studio route listens on. */
export function studioState():{upstream?:{host:string;port:number};sessions:Record<string,{url:string}>} {
 try{return readJsonCached('.lab/upstream/studio.json') as any;}catch{return {sessions:{}};}
}
/** Account operations for invitations through the management realm's admin API. The service
 * role token is made here, from the private runtime secret, and never leaves this process.
 * docs/engineering/INVITATIONS.md */
export function invitationAccounts(address:string|(()=>string),serviceRole:string,request:typeof fetch=fetch):InvitationAccounts {
 // `address` is the realm's own Auth service, which serves /admin/users and /user at its root, so
 // the Auth client is used directly: supabase-js would address it at /auth/v1, which only an
 // API gateway serves (every call answered 404 until the first live invitation run). A function
 // is asked on each use, so a realm that moved (a replaced container, with a handover running the
 // previous version beside the new one) is reached at its new address.
 let client:{url:string;auth:InstanceType<typeof AuthClient>}|undefined;
 const current=()=>{
  const url=(typeof address==='string'?address:address()).replace(/\/+$/,'');
  if(client?.url!==url)client={url,auth:new AuthClient({url,headers:{Authorization:`Bearer ${serviceRole}`,apikey:serviceRole},
   persistSession:false,autoRefreshToken:false,detectSessionInUrl:false,
   fetch:((input,init)=>request(input,{...init,redirect:'error',signal:AbortSignal.timeout(10_000)})) as typeof fetch})};
  return client.auth;
 };
 return {
  async create(email,password) {
   const {data,error}=await current().admin.createUser({email,password,email_confirm:true,app_metadata:{sbarbase_invited:true}});
   if(error){
    // Only these codes mean the email is taken; other 422s are validation, never 'exists'.
    if(['email_exists','user_already_exists'].includes(error.code??''))return 'exists';
    if(error.code==='weak_password')return 'weak';
    throw new Error('Account creation failed');
   }
   return {id:data.user.id};
  },
  async session(token) {
   const {data,error}=await current().getUser(token);
   return error||!data.user?.id||!data.user.email?null:{id:data.user.id,email:data.user.email};
  },
 };
}
export function openUpstreamApplication() {
 const load=(path:string)=>JSON.parse(readFileSync(path,'utf8'));
 let key:Buffer|undefined;
 const studioSessionKey=()=>key??=studioKey('.secrets/upstream/studio-session.key');
 const secrets=load('.secrets/upstream/runtime.json');
 // The management realm's address is read on each use: the new version rewrites management.json while
 // the previous version's process still serves (lab/handover.py, PROTOCOL in src/control/handover-formats.json).
 const managementAuth=()=>(readJsonCached('.lab/upstream/management.json') as {auth:string}).auth;
 const receipt=process.env.SBARBASE_LIFECYCLE_RECOVERY_RECEIPT;
 const catalog=new Catalog('.lab/upstream/control.sqlite',{lifecycleAdmission:receipt?installedLifecycleAdmission(process.cwd(),receipt):undefined});
 catalog.lifecycleAuthorizationStore=LifecycleAuthorizationStore.installed(process.cwd());
 // An environment's exact inventory is derived from this host's durable state when it is deleted (src/control/durable-lifecycle.ts).
 catalog.durableInventory=durableInventoryDeriver(process.cwd());
 const keys=new KeyStore('.secrets/upstream/managed-keys.sqlite');
 const tokens=new AccessTokenStore('.secrets/upstream/access-tokens.sqlite');
 // The server's own loopback address, set once it listens: the MCP endpoint reaches the
 // Supabase-compatible API there (src/control/mcp.ts).
 let loopback:string|undefined;
 const resolve=(runtime:string):EnvironmentRoute|undefined=>{
  // Refresh private configuration and endpoints after provisioning or restart: the cache
  // rereads either file as soon as it changes.
  const endpoints=readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>;
  const current=readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>};
  if(!endpoints[runtime]||!current.environments[runtime])return undefined;
  const secret=current.environments[runtime].jwt;
  return {...endpoints[runtime],keys:[],anonymousToken:internalToken(secret,'anon'),serviceToken:serviceToken(secret,3600),enabled:true,
   ...(endpoints[runtime].realtime?{realtimeToken:realtimeToken(secret),realtimeServiceToken:serviceToken(secret,24*3600)}:{})};
 };
 const requests=new RequestLog();
 // When each optional service was last used and what is using it now: the Services page reads it, and an On demand service's
 // sleep depends on it (src/gateway/activity.ts). Written to the catalog at most once a minute.
 const activity=new ServiceActivity({load:runtime=>catalog.serviceActivity(runtime),save:rows=>catalog.saveServiceActivity(rows)},Date.now,
  runtime=>applicationConcurrency.inFlight(runtime,'functions'));
 activity.start();
 // Once an hour, and fifteen minutes after a start: an operator notice for a service that sits unused (a hint, never an action).
 const hints=()=>{try{checkUnusedServices(catalog,activity,Date.now(),readStats());}catch{console.error('The unused service check failed; it is tried again in an hour.');}};
 // The first look waits fifteen minutes, so clients of a restarted console have reconnected before anything is called unused.
 const hintTimer=setInterval(hints,3600_000);hintTimer.unref?.();setTimeout(hints,15*60_000).unref?.();
 const health=createServerHealthReader();
 // On demand services (src/control/on-demand.ts): started by the first request with a verified key, put to sleep after their quiet period.
 // The sweeper runs every ten seconds; it decides about sleep once a minute and, with the kill switch on, wakes what sleeps.
 const onDemand=new OnDemand(catalog,activity,(runtime,service)=>!!resolve(runtime)?.[service]);
 const sweeper=setInterval(()=>{try{onDemand.sweep();}catch{console.error('The On demand sweep failed; it is tried again shortly.');}},10_000);sweeper.unref?.();
 const gate={isOnDemand:(runtime:string,service:'realtime'|'functions')=>onDemand.isOnDemand(runtime,service),ensure:(runtime:string,service:'realtime'|'functions')=>onDemand.ensure(runtime,service),
  // A Functions call without a key may wake the runtime only for a function its owner made public.
  publicFunction:(runtime:string,name:string)=>isPublicFunction('.lab/upstream/functions',runtime,name)};
 const database=new DirectDatabase();
 const handler=application(catalog,keys,{get auth(){return managementAuth();},publishableKey:managementPublishableKey,
  anonymousToken:internalToken(secrets.management.jwt,'anon')},resolve,fetch,studioSessionKey,requests,undefined,
   invitationAccounts(managementAuth,internalToken(secrets.management.jwt,'service_role')),database,{tokens,loopback:()=>loopback},
   {activity,onDemand:gate,memory:()=>{const memory=health().memory;return {availableBytes:memory.availableBytes,totalBytes:memory.totalBytes};}});
 const socket=realtimeUpgrade({
  route:catalogRealtimeRoute(catalog,resolve),
  verifyKey:(runtime,key)=>keys.resolve(runtime,key),
  wake:{isOnDemand:runtime=>onDemand.isOnDemand(runtime,'realtime'),ensure:runtime=>onDemand.ensure(runtime,'realtime')}});
 // Socket openings count in the environment's logs too: 101 when one reaches Realtime.
 const realtime:typeof socket=(path,headers)=>{
  const started=performance.now(),decision=socket(path,headers);
  const runtime=new URL(path,'http://local').pathname.split('/')[1]??'';
  const finish=(done:Awaited<typeof decision>)=>{
   try{if(resolve(runtime))requests.record(runtime,{method:'GET',service:'realtime',path:'/websocket',
    status:done.ok?101:done.status,ms:performance.now()-started});}catch{}
   return done.ok?{...done,onOpen:()=>activity.socketOpened(runtime)}:done;
  };
  return decision instanceof Promise?decision.then(finish):finish(decision);
 };
  const studio=studioProxy({key:studioSessionKey,allowed:(actor,runtime)=>catalog.studioAllowed(actor,runtime),epoch:actor=>catalog.managementSecurity.epoch(actor),runtimeEpoch:runtime=>catalog.runtimeEpoch(runtime),
   navigation:(actor,runtime,organization,project)=>catalog.studioNavigation(actor,runtime,organization,project),
   selection:(actor,environment)=>catalog.studio(actor,environment),
   touch:runtime=>catalog.touchStudio(runtime),starting:runtime=>catalog.studioStarting(runtime),
   upstream:runtime=>studioState().sessions?.[runtime]?.url});
  const upstream=studioUpstream({
   endpoints:runtime=>(readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>)[runtime],
   secret:runtime=>(readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>}).environments[runtime]?.jwt,
   active:runtime=>catalog.runtimeReady(runtime)&&!!studioState().sessions?.[runtime]});
  // The gate reads each environment's share from the catalog, so a change applies at the next request.
  applicationConcurrency.useShares(runtime=>catalog.gatewayShare(runtime));
  // One monitor per process, over the one application gate: a busy environment's operator notice.
  const pressure=new PressureMonitor(applicationConcurrency,(runtime,saturation)=>catalog.environmentSaturated(runtime,saturation));
  pressure.start();
  // The internal route also serves each environment's email templates to its own Auth (src/control/mail-templates.ts).
  const templates=mailTemplateRoute('.secrets/upstream');
  return {handler,studio,upstream:(request:Request)=>templates(request)??upstream(request),realtime,catalog,keys,tokens,database,listening(port:number){loopback=`http://127.0.0.1:${port}`;},
   close(){clearInterval(hintTimer);clearInterval(sweeper);activity.stop();database.stop();pressure.stop();catalog.close();keys.close();tokens.close();}};
}
