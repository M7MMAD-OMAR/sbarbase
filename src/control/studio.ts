import {bindReadyEnvironmentPublication} from './ready-publication';
import {createHmac,randomBytes,timingSafeEqual} from 'node:crypto';
import {chmodSync,existsSync,readFileSync,writeFileSync} from 'node:fs';
import {Catalog,type StudioNavigation,type StudioSession} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {studioWorkspaceAsset} from './studio-workspace';

/** Studio for one environment, reached in the browser at `<24 hex>.studio.localhost` on the
 * console's own port. Every environment is its own origin, so a Studio session cookie for one
 * environment is never sent to another. The console hands out a one-minute ticket; entering
 * with it sets an HttpOnly cookie for that origin only. Every request re-checks that the
 * actor is still an owner or admin of the environment, so a removed member loses Studio at
 * once. Studio's own server-side calls to Auth, REST and Storage use the internal route
 * (`studioUpstream`), which admits only that environment's service key. */

const TICKET_MS=60_000;
const SESSION_MS=8*60*60_000;
export const STUDIO_COOKIE='sbarbase_studio';
const HOST=/^([a-f0-9]{24})\.studio\.localhost$/;

export function studioKey(path:string):Buffer {
 if(!existsSync(path)){writeFileSync(path,randomBytes(32).toString('hex'),{mode:0o600});chmodSync(path,0o600);}
 const value=readFileSync(path,'utf8').trim();
 if(!/^[a-f0-9]{64}$/.test(value))throw new Error('Invalid Studio session key');
 return Buffer.from(value,'hex');
}

export function studioRuntime(hostname:string):string|null {
 const match=hostname.match(HOST);return match?'e_'+match[1]:null;
}

export function studioHost(runtime:string):string {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 return runtime.slice(2)+'.studio.localhost';
}

type Claim={runtime:string;actor:string;expires:number;kind:'ticket'|'session';epoch:number;runtimeEpoch?:number;console?:string};

function consoleOrigin(value:string|undefined):string|undefined {
 try{const url=new URL(value??'');return ['http:','https:'].includes(url.protocol)&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)&&!url.username&&!url.password&&!url.search&&!url.hash?url.origin:undefined;}catch{return;}
}

export function signStudio(key:Buffer,claim:Claim):string {
 const body=Buffer.from(JSON.stringify(claim)).toString('base64url');
 return body+'.'+createHmac('sha256',key).update(body).digest('base64url');
}

export function verifyStudio(key:Buffer,token:string|null|undefined,runtime:string,kind:Claim['kind'],now=Date.now()):Claim|null {
 if(!token||token.length>2048)return null;
 const [body,signature,extra]=token.split('.');
 if(!body||!signature||extra!==undefined)return null;
 const expected=createHmac('sha256',key).update(body).digest();
 const given=Buffer.from(signature,'base64url');
 if(given.length!==expected.length||!timingSafeEqual(given,expected))return null;
 let claim:Claim;
 try{claim=JSON.parse(Buffer.from(body,'base64url').toString('utf8'));}catch{return null;}
 if(!claim||claim.kind!==kind||claim.runtime!==runtime||typeof claim.actor!=='string'||!(claim.expires>now)||
  !Number.isSafeInteger(claim.epoch)||claim.epoch<0||claim.runtimeEpoch!==undefined&&(!Number.isSafeInteger(claim.runtimeEpoch)||claim.runtimeEpoch<0))return null;
 return claim;
}

/** Management routes: GET, POST (start) and DELETE (stop) `/environments/{id}/studio`, and
 * POST `/environments/{id}/studio/session` for the ticket the console opens Studio with. */
export function studioHandler(catalog:Catalog,identify:ManagementIdentity,key:()=>Buffer) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/studio(\/session)?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const [,environment,session]=match;
  const allowed=session?['POST']:['GET','POST','DELETE'];
  if(!allowed.includes(request.method))return reply(405,{message:'Method not allowed'});
  if(request.body)return reply(400,{message:'Invalid request'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   const answer=(response:Response,runtime:string)=>bindReadyEnvironmentPublication(response,catalog,actor,environment!,runtime,true);
   if(session) {
    const current=catalog.studio(actor,environment!);
    if(current.state!=='running')return reply(409,{message:'Studio is not running'});
    const ticket=signStudio(key(),{runtime:current.runtime,actor,expires:Date.now()+TICKET_MS,kind:'ticket',epoch:catalog.managementSecurity.epoch(actor),runtimeEpoch:catalog.runtimeEpoch(current.runtime),console:consoleOrigin(new URL(request.url).origin)});
    return answer(reply(201,{host:studioHost(current.runtime),path:'/__sbarbase/enter?ticket='+encodeURIComponent(ticket)}),current.runtime);
   }
   if(request.method==='GET'){const data=catalog.studio(actor,environment!);return answer(reply(200,{data}),data.runtime);}
   const next=catalog.requestStudio(actor,environment!,request.method==='POST'?'running':'stopped');
   return answer(reply(202,{data:next}),next.runtime);
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}

const HOP=new Set(['connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','content-length']);

function cookieValue(header:string|null,name:string):string|null {
 for(const part of (header??'').split(';')){const [k,...v]=part.trim().split('=');if(k===name)return v.join('=');}
 return null;
}

function withoutCookie(header:string|null,name:string):string {
 return (header??'').split(';').map(part=>part.trim()).filter(part=>part&&!part.startsWith(name+'=')).join('; ');
}

function page(status:number,text:string) {
 return new Response(`<!doctype html><meta charset="utf-8"><title>Studio</title><p style="font-family:system-ui;margin:3rem">${text}</p>`,
  {status,headers:{'content-type':'text/html; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
}

class StudioBodyError extends Error {
 constructor(readonly status:number){super('Studio request body refused');}
}

/** Finish bounded mutation input before authorizing its upstream effect. */
async function studioBody(request:Request,limit:number,timeoutMs:number):Promise<Uint8Array<ArrayBuffer>|undefined> {
 if(!request.body){if(request.signal.aborted)throw new StudioBodyError(400);return;}
 const reader=request.body.getReader(),deadline=performance.now()+timeoutMs;
 let bytes=new Uint8Array(Math.min(limit,64*1024)),size=0,complete=false;
 let refuse!:(error:StudioBodyError)=>void;
 const stopped=new Promise<never>((_,reject)=>{refuse=reject;});
 const abort=()=>refuse(new StudioBodyError(400));
 const timer=setTimeout(()=>refuse(new StudioBodyError(408)),timeoutMs);
 request.signal.addEventListener('abort',abort,{once:true});
 try{
  if(request.signal.aborted)throw new StudioBodyError(400);
  for(;;){
   if(performance.now()>=deadline)throw new StudioBodyError(408);
   const chunk=await Promise.race([reader.read(),stopped]);
   if(performance.now()>=deadline)throw new StudioBodyError(408);
   if(chunk.done){complete=true;break;}
   if(!(chunk.value instanceof Uint8Array))throw new StudioBodyError(400);
   if(chunk.value.byteLength===0)continue;
   const offset=size;
   size+=chunk.value.byteLength;
   if(size>limit)throw new StudioBodyError(413);
   if(size>bytes.byteLength){const next=new Uint8Array(Math.min(limit,Math.max(size,bytes.byteLength*2)));next.set(bytes);bytes=next;}
   bytes.set(chunk.value,offset);
  }
  return bytes.slice(0,size);
 }catch(error){throw error instanceof StudioBodyError?error:new StudioBodyError(400);}
 finally{
  clearTimeout(timer);request.signal.removeEventListener('abort',abort);
  // Client-controlled cancellation may never settle, so do not await it.
  if(!complete)void reader.cancel().catch(()=>{});
  reader.releaseLock();
 }
}

/** Check access when downstream asks for bytes, including after an upstream wait. */
function studioResponseBody(body:ReadableStream<Uint8Array>,allowed:()=>boolean,signal:AbortSignal):ReadableStream<Uint8Array> {
 const reader=body.getReader();let finished=false,abort=()=>{};
 const finish=(cancel:boolean,reason?:unknown)=>{
  if(finished)return;finished=true;signal.removeEventListener('abort',abort);
  if(cancel)void reader.cancel(reason).catch(()=>{});
  reader.releaseLock();
 };
 return new ReadableStream<Uint8Array>({
  start(controller){
   abort=()=>{if(!finished){controller.error(new Error('Studio request aborted'));finish(true);}};
   if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});
  },
  async pull(controller){
   try{
    if(!allowed())throw new Error('Studio access revoked');
    const chunk=await reader.read();
    if(finished)return;
    if(!allowed()||signal.aborted)throw new Error('Studio access revoked');
    if(chunk.done){controller.close();finish(false);}else controller.enqueue(chunk.value);
   }catch(error){if(!finished){controller.error(error);finish(true,error);}}
  },
  cancel(reason){finish(true,reason);},
 },{highWaterMark:0});
}

/** The browser side: `<id>.studio.localhost` requests, gated by the session cookie. */
export function studioProxy(options:{key:()=>Buffer;allowed:(actor:string,runtime:string)=>boolean;epoch:(actor:string)=>number;
 runtimeEpoch?:(runtime:string)=>number;upstream:(runtime:string)=>string|undefined;transport?:typeof fetch;
 requestBodyLimit?:number;requestBodyTimeoutMs?:number;
 navigation?:(actor:string,runtime:string,organization?:string,project?:string)=>StudioNavigation;
 selection?:(actor:string,environment:string)=>StudioSession}) {
 const transport=options.transport??fetch;
 const bodyLimit=options.requestBodyLimit??16*1024*1024,bodyTimeout=options.requestBodyTimeoutMs??30_000;
 if(!Number.isSafeInteger(bodyLimit)||bodyLimit<1||bodyLimit>64*1024*1024||!Number.isSafeInteger(bodyTimeout)||bodyTimeout<1||bodyTimeout>120_000)
  throw new Error('Invalid Studio request body limits');
 const current=(claim:Claim,runtime:string)=>{
  try{return claim.expires>Date.now()&&claim.epoch===options.epoch(claim.actor)&&(claim.runtimeEpoch??0)===(options.runtimeEpoch?.(runtime)??0)&&options.allowed(claim.actor,runtime);}catch{return false;}
 };
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const runtime=studioRuntime(url.hostname);
  if(!runtime)return page(404,'Unknown Studio address.');
  if(url.pathname==='/__sbarbase/enter') {
   const claim=verifyStudio(options.key(),url.searchParams.get('ticket'),runtime,'ticket');
   if(!claim||!current(claim,runtime))return page(401,'This Studio link has expired. Open Studio again from the Sbarbase console.');
   const session=signStudio(options.key(),{runtime,actor:claim.actor,expires:Date.now()+SESSION_MS,kind:'session',epoch:claim.epoch,runtimeEpoch:claim.runtimeEpoch??0,console:consoleOrigin(claim.console)});
   return new Response(null,{status:302,headers:{location:options.navigation?'/__sbarbase/workspace':'/project/default','cache-control':'no-store',
    'set-cookie':`${STUDIO_COOKIE}=${session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_MS/1000}`}});
  }
  const claim=verifyStudio(options.key(),cookieValue(request.headers.get('cookie'),STUDIO_COOKIE),runtime,'session');
  if(!claim||!current(claim,runtime))return ['/__sbarbase/navigation','/__sbarbase/switch'].includes(url.pathname)
   ?reply(401,{message:'Open Studio from the Sbarbase console to sign in.'}):page(401,'Open Studio from the Sbarbase console to sign in.');
  const allowed=()=>current(claim,runtime);
  const guarded=(response:Response,permission=allowed)=>new Response(response.body?studioResponseBody(response.body,permission,request.signal):null,
   {status:response.status,headers:response.headers});
  if(url.pathname.startsWith('/__sbarbase/')) {
   if(!options.navigation)return reply(404,{message:'Workspace navigation is unavailable'});
   if(url.pathname==='/__sbarbase/switch') {
    if(request.method!=='POST')return reply(405,{message:'Method not allowed'});
    if(request.headers.get('origin')!==url.origin)return reply(403,{message:'Forbidden'});
    if(request.headers.get('content-type')?.split(';')[0]?.trim()!=='application/json')return reply(415,{message:'JSON is required'});
    let bytes:Uint8Array<ArrayBuffer>|undefined;
    try{bytes=await studioBody(request,Math.min(bodyLimit,512),bodyTimeout);}
    catch(error){const status=error instanceof StudioBodyError?error.status:400;
     return reply(status,{message:status===413?'Request is too large':status===408?'Request body timed out':'Invalid request'});}
    // Membership and session expiry may change during the body read.
    if(!allowed())return reply(403,{message:'Forbidden'});
    let environment:string;
    try{const value=JSON.parse(new TextDecoder().decode(bytes));
     if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==1||typeof value.environment!=='string'||!/^[a-f0-9-]{36}$/.test(value.environment))throw new Error();
     environment=value.environment;
    }catch{return reply(400,{message:'Invalid environment'});}
    try{
     if(!options.selection)return reply(404,{message:'Workspace switching is unavailable'});
     const selected=options.selection(claim.actor,environment);
     if(!allowed()||!current(claim,selected.runtime))return reply(403,{message:'Forbidden'});
     if(selected.desired!=='running'||selected.state!=='running'||!options.upstream(selected.runtime))return reply(409,{message:'Studio is not running here. Start it from the console, then Refresh.'});
     const destination=new URL(url.origin);destination.hostname=studioHost(selected.runtime);destination.pathname='/__sbarbase/enter';
     destination.searchParams.set('ticket',signStudio(options.key(),{runtime:selected.runtime,actor:claim.actor,expires:Date.now()+TICKET_MS,kind:'ticket',epoch:claim.epoch,runtimeEpoch:options.runtimeEpoch?.(selected.runtime)??0,console:consoleOrigin(claim.console)}));
     return guarded(reply(200,{href:destination.href}),()=>allowed()&&current(claim,selected.runtime));
    }catch(error){return reply(error instanceof Error&&error.message==='Forbidden'?403:409,{message:error instanceof Error&&error.message==='Forbidden'?'Forbidden':'Environment is unavailable'});}
   }
   if(!['GET','HEAD'].includes(request.method))return reply(405,{message:'Method not allowed'});
   if(url.pathname==='/__sbarbase/navigation') {
    try{const organization=url.searchParams.get('organization')??undefined,project=url.searchParams.get('project')??undefined;
     const data=options.navigation(claim.actor,runtime,organization,project),snapshot=JSON.stringify(data);
     const consoleUrl=new URL(url.origin);consoleUrl.hostname='127.0.0.1';consoleUrl.pathname='/';
     return guarded(reply(200,{...data,consoleUrl:consoleOrigin(claim.console)??consoleUrl.href}),()=>{
      if(!allowed())return false;
      try{return JSON.stringify(options.navigation!(claim.actor,runtime,organization,project))===snapshot;}catch{return false;}
     });
    }catch(error){return reply(error instanceof Error&&error.message==='Forbidden'?403:500,{message:error instanceof Error&&error.message==='Forbidden'?'Forbidden':'Unable to load workspace'});}
   }
   const asset=studioWorkspaceAsset(url.pathname);
   if(asset)return request.method==='HEAD'?new Response(null,{headers:asset.headers}):asset;
   return reply(404,{message:'Unknown workspace route'});
  }
  const target=options.upstream(runtime);
  if(!target)return page(503,'Studio is not running for this environment. Start it from the Sbarbase console.');
  const headers=new Headers();
  for(const [name,value] of request.headers)if(!HOP.has(name))headers.set(name,value);
  const cookies=withoutCookie(request.headers.get('cookie'),STUDIO_COOKIE);
  if(cookies)headers.set('cookie',cookies);else headers.delete('cookie');
  const destination=new URL(target);
  destination.pathname=url.pathname;destination.search=url.search;
  let body:Uint8Array<ArrayBuffer>|undefined;
  try{if(!['GET','HEAD'].includes(request.method))body=await studioBody(request,bodyLimit,bodyTimeout);}
  catch(error){const status=error instanceof StudioBodyError?error.status:400;
   return page(status,status===413?'Studio request is too large.':status===408?'Studio request body timed out.':'Invalid Studio request body.');}
  if(!allowed())return page(403,'Studio access has been revoked. Open Studio again from the Sbarbase console.');
  let response:Response;
  try{response=await transport(destination,{method:request.method,headers,redirect:'manual',signal:request.signal,
   ...(['GET','HEAD'].includes(request.method)?{}:{body})} as RequestInit);}
  catch{return page(request.signal.aborted?400:502,'Unable to reach Studio.');}
  if(!allowed()||request.signal.aborted){
   if(response.body)void response.body.cancel().catch(()=>{});
   return page(request.signal.aborted?400:403,'Studio access is no longer available.');
  }
  const out=new Headers();
  for(const [name,value] of response.headers)if(!HOP.has(name)&&name!=='set-cookie')out.set(name,value);
  for(const cookie of response.headers.getSetCookie())out.append('set-cookie',cookie);
  out.set('x-frame-options','SAMEORIGIN');
  if(options.navigation&&out.get('content-type')?.includes('text/html')){
   const policies=(out.get('content-security-policy')||'').split(',').map(policy=>{
    const directives=policy.split(';').map(value=>value.trim()).filter(value=>value&&!/^frame-ancestors(?:\s|$)/i.test(value));
    return [...directives,"frame-ancestors 'self'"].join('; ');
   });
   out.set('content-security-policy',policies.join(', '));
  }
  if(request.method==='HEAD'){
   if(response.body)void response.body.cancel().catch(()=>{});
   return new Response(null,{status:response.status,headers:out});
  }
  return new Response(response.body?studioResponseBody(response.body,allowed,request.signal):null,{status:response.status,headers:out});
 };
}

type Endpoints={auth:string;rest:string;storage?:{url:string;tenantHost:string}};

/** Studio's server-side calls (`SUPABASE_URL=http://<network gateway>:<port>/<runtime>`), the
 * Kong routes of a single-project stack: /auth/v1, /rest/v1 and /storage/v1 of that one
 * environment. Admits only a service_role key signed with that environment's own secret. */
export function studioUpstream(options:{endpoints:(runtime:string)=>Endpoints|undefined;secret:(runtime:string)=>string|undefined;
 active:(runtime:string)=>boolean;transport?:typeof fetch}) {
 const transport=options.transport??fetch;
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const match=url.pathname.match(/^\/(e_[a-f0-9]{24})\/(auth|rest|storage)\/v1(\/.*)?$/);
  if(!match)return Response.json({message:'Unknown route'},{status:404});
  const [,runtime,service,rest='/']=match;
  const secret=options.secret(runtime!),endpoints=options.endpoints(runtime!);
  if(!secret||!endpoints||!options.active(runtime!))return Response.json({message:'Unavailable'},{status:503});
  const bearer=(request.headers.get('authorization')??'').replace(/^Bearer\s+/i,'');
  if(!serviceKey(bearer,secret)&&!serviceKey(request.headers.get('apikey')??'',secret))
   return Response.json({message:'Invalid API key'},{status:401});
  let base:string;const headers=new Headers();
  for(const [name,value] of request.headers)if(!HOP.has(name))headers.set(name,value);
  if(service==='auth')base=endpoints.auth;
  else if(service==='rest')base=endpoints.rest;
  else {
   if(!endpoints.storage)return Response.json({message:'Unavailable'},{status:503});
   base=endpoints.storage.url;headers.set('x-forwarded-host',endpoints.storage.tenantHost);
  }
  const destination=new URL(base);
  destination.pathname=rest;destination.search=url.search;
  const response=await transport(destination,{method:request.method,headers,redirect:'manual',
   ...(['GET','HEAD'].includes(request.method)?{}:{body:request.body,duplex:'half'})} as RequestInit);
  const out=new Headers();
  for(const [name,value] of response.headers)if(!HOP.has(name))out.set(name,value);
  return new Response(response.body,{status:response.status,headers:out});
 };
}

/** A JWT with role service_role, signed HS256 with the environment's own secret. */
export function serviceKey(token:string,secret:string):boolean {
 const [header,body,signature,extra]=token.split('.');
 if(!header||!body||!signature||extra!==undefined)return false;
 const expected=createHmac('sha256',secret).update(header+'.'+body).digest();
 const given=Buffer.from(signature,'base64url');
 if(given.length!==expected.length||!timingSafeEqual(given,expected))return false;
 try {
  const head=JSON.parse(Buffer.from(header,'base64url').toString('utf8'));
  const claims=JSON.parse(Buffer.from(body,'base64url').toString('utf8'));
  return head.alg==='HS256'&&claims.role==='service_role'&&(claims.exp===undefined||claims.exp*1000>Date.now());
 } catch {return false;}
}
