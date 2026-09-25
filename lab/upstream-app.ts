import {readFileSync} from 'node:fs';
import {createClient} from '@supabase/supabase-js';
import type {InvitationAccounts} from '../src/control/invitations';
import {AuthClient} from '@supabase/auth-js';
import {PressureMonitor} from '../src/gateway/pressure';
import {applicationConcurrency} from '../src/gateway/managed';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {readJsonCached} from '../src/http/cached-json';
import {studioKey,studioProxy,studioUpstream} from '../src/control/studio';
import {realtimeUpgrade} from '../src/gateway/realtime';
import {RequestLog} from '../src/gateway/observe';
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
/** Account operations for invitations through the management realm's admin API. The service
 * role token is made here, from the private runtime secret, and never leaves this process.
 * docs/engineering/INVITATIONS.md */
export function invitationAccounts(url:string,serviceRole:string,request:typeof fetch=fetch):InvitationAccounts {
 // `url` is the realm's own Auth service, which serves /admin/users and /user at its root, so
 // the Auth client is used directly: supabase-js would address it at /auth/v1, which only an
 // API gateway serves (every call answered 404 until the first live invitation run).
 const auth=new AuthClient({url:url.replace(/\/+$/,''),headers:{Authorization:`Bearer ${serviceRole}`,apikey:serviceRole},
  persistSession:false,autoRefreshToken:false,detectSessionInUrl:false,
  fetch:(input,init)=>request(input,{...init,redirect:'error',signal:AbortSignal.timeout(10_000)})});
 return {
  async create(email,password) {
   const {data,error}=await auth.admin.createUser({email,password,email_confirm:true,app_metadata:{sbarbase_invited:true}});
   if(error){
    // Only these codes mean the email is taken; other 422s are validation, never 'exists'.
    if(['email_exists','user_already_exists'].includes(error.code??''))return 'exists';
    if(error.code==='weak_password')return 'weak';
    throw new Error('Account creation failed');
   }
   return {id:data.user.id};
  },
  async session(token) {
   const {data,error}=await auth.getUser(token);
   return error||!data.user?.id||!data.user.email?null:{id:data.user.id,email:data.user.email};
  },
 };
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
 const requests=new RequestLog();
 const handler=application(catalog,keys,{auth:management.auth,publishableKey:managementPublishableKey,
  anonymousToken:internalToken(secrets.management.jwt,'anon')},resolve,fetch,studioSessionKey,requests,undefined,
   invitationAccounts(management.auth,internalToken(secrets.management.jwt,'service_role')));
 const socket=realtimeUpgrade({
  route:runtime=>{
   if(!catalog.runtimeReady(runtime))return undefined;
   const routing=catalog.runtimeRouting(runtime);
   return routing.maintenance?'maintenance':routeWithPlacement(resolve(runtime),routing);
  },
  verifyKey:(runtime,key)=>keys.resolve(runtime,key)==='publishable'});
 // Socket openings count in the environment's logs too: 101 when one reaches Realtime.
 const realtime:typeof socket=(path,headers)=>{
  const started=performance.now(),decision=socket(path,headers);
  const runtime=new URL(path,'http://local').pathname.split('/')[1]??'';
  try{if(resolve(runtime))requests.record(runtime,{method:'GET',service:'realtime',path:'/websocket',
   status:decision.ok?101:decision.status,ms:performance.now()-started});}catch{}
  return decision;
 };
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
  return {handler,studio,upstream,realtime,catalog,keys,close(){pressure.stop();catalog.close();keys.close();}};
}
