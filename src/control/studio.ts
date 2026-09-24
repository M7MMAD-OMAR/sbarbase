import {createHmac,randomBytes,timingSafeEqual} from 'node:crypto';
import {chmodSync,existsSync,readFileSync,writeFileSync} from 'node:fs';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

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

type Claim={runtime:string;actor:string;expires:number;kind:'ticket'|'session'};

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
 if(claim.kind!==kind||claim.runtime!==runtime||typeof claim.actor!=='string'||!(claim.expires>now))return null;
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
   if(session) {
    const current=catalog.studio(actor,environment!);
    if(current.state!=='running')return reply(409,{message:'Studio is not running'});
    const ticket=signStudio(key(),{runtime:current.runtime,actor,expires:Date.now()+TICKET_MS,kind:'ticket'});
    return reply(201,{host:studioHost(current.runtime),path:'/__sbarbase/enter?ticket='+encodeURIComponent(ticket)});
   }
   if(request.method==='GET')return reply(200,{data:catalog.studio(actor,environment!)});
   const next=catalog.requestStudio(actor,environment!,request.method==='POST'?'running':'stopped');
   return reply(202,{data:next});
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

/** The browser side: `<id>.studio.localhost` requests, gated by the session cookie. */
export function studioProxy(options:{key:()=>Buffer;allowed:(actor:string,runtime:string)=>boolean;
 upstream:(runtime:string)=>string|undefined;transport?:typeof fetch}) {
 const transport=options.transport??fetch;
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const runtime=studioRuntime(url.hostname);
  if(!runtime)return page(404,'Unknown Studio address.');
  if(url.pathname==='/__sbarbase/enter') {
   const claim=verifyStudio(options.key(),url.searchParams.get('ticket'),runtime,'ticket');
   if(!claim||!options.allowed(claim.actor,runtime))return page(401,'This Studio link has expired. Open Studio again from the Sbarbase console.');
   const session=signStudio(options.key(),{runtime,actor:claim.actor,expires:Date.now()+SESSION_MS,kind:'session'});
   return new Response(null,{status:302,headers:{location:'/project/default','cache-control':'no-store',
    'set-cookie':`${STUDIO_COOKIE}=${session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_MS/1000}`}});
  }
  const claim=verifyStudio(options.key(),cookieValue(request.headers.get('cookie'),STUDIO_COOKIE),runtime,'session');
  if(!claim||!options.allowed(claim.actor,runtime))return page(401,'Open Studio from the Sbarbase console to sign in.');
  const target=options.upstream(runtime);
  if(!target)return page(503,'Studio is not running for this environment. Start it from the Sbarbase console.');
  const headers=new Headers();
  for(const [name,value] of request.headers)if(!HOP.has(name))headers.set(name,value);
  const cookies=withoutCookie(request.headers.get('cookie'),STUDIO_COOKIE);
  if(cookies)headers.set('cookie',cookies);else headers.delete('cookie');
  const response=await transport(new URL(url.pathname+url.search,target),{method:request.method,headers,redirect:'manual',
   ...(['GET','HEAD'].includes(request.method)?{}:{body:request.body,duplex:'half'})} as RequestInit);
  const out=new Headers();
  for(const [name,value] of response.headers)if(!HOP.has(name)&&name!=='set-cookie')out.set(name,value);
  for(const cookie of response.headers.getSetCookie())out.append('set-cookie',cookie);
  out.set('x-frame-options','SAMEORIGIN');
  return new Response(response.body,{status:response.status,headers:out});
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
  const response=await transport(new URL(rest+url.search,base),{method:request.method,headers,redirect:'manual',
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
