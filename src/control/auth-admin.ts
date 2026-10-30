import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';

/** The two Authentication pages that read and write through the environment's own Auth admin API: Audit Logs (a read of what Auth
 * recorded) and Single sign-on (SAML identity providers). The control plane makes the call with a service role token for that
 * environment only, to that environment's own Auth address, and only to the three paths below: this is not a general admin proxy.
 * Neither page needs the environment's database access turned on. */

/** What Auth answered: its status and parsed body, or `unavailable` when it could not be asked. */
export type AuthAnswer={status:number;body:unknown;total:number|null}|'unavailable';
export type AuthAdmin=(runtime:string,method:'GET'|'POST'|'DELETE',path:string,body?:unknown)=>Promise<AuthAnswer>;

const ALLOWED=/^\/admin\/(?:audit|sso\/providers(?:\/[0-9a-f-]{36})?)$/;
const MAX_ANSWER=2*1024*1024;

/** The call to one environment's Auth. `route` says where it is and which service role token to present (undefined when
 * there is no runtime to ask); `transport` is fetch. Nothing here throws: a failure to ask is `unavailable`. */
export function authAdminThrough(route:(runtime:string)=>{url?:string;token?:string}|'maintenance'|undefined,transport:typeof fetch,timeoutMs=10_000):AuthAdmin {
 return async(runtime,method,path,body)=>{
  try {
   if(!path.startsWith('/')||path.startsWith('//'))return 'unavailable';
   const target=new URL(path,'http://placeholder');
   if(!ALLOWED.test(target.pathname))return 'unavailable';
   const where=route(runtime);
   if(where==='maintenance'||!where?.url||!where.token)return 'unavailable';
   const destination=new URL(target.pathname,where.url);
   destination.search=target.search;
   const response=await transport(destination,{method,redirect:'error',signal:AbortSignal.timeout(timeoutMs),
    headers:{authorization:'Bearer '+where.token,...(body===undefined?{}:{'content-type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});
   const declared=Number(response.headers.get('content-length'));
   if(Number.isFinite(declared)&&declared>MAX_ANSWER)return 'unavailable';
   const text=await response.text();
   if(text.length>MAX_ANSWER)return 'unavailable';
   let parsed:unknown=null;
   try{parsed=text?JSON.parse(text):null;}catch{parsed=null;}
   const total=Number(response.headers.get('x-total-count'));
   return {status:response.status,body:parsed,total:Number.isInteger(total)&&total>=0&&response.headers.has('x-total-count')?total:null};
  }catch{return 'unavailable';}
 };
}

const DOMAIN=/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
const MAX_METADATA=262144,MAX_MAPPING=8192,MAX_DOMAINS=20;
const text=(value:unknown,limit:number)=>typeof value==='string'?value.slice(0,limit):undefined;

export type AuditEntry={id:string;created_at:string;ip_address:string;actor:string;action:string;log_type:string};
/** Only the fields the page shows: who, what, from where and when. Auth's `traits` are not passed on. */
export function auditEntry(raw:unknown):AuditEntry|null {
 if(!raw||typeof raw!=='object')return null;
 const entry=raw as Record<string,unknown>,payload=(entry.payload&&typeof entry.payload==='object'?entry.payload:{}) as Record<string,unknown>;
 const id=text(entry.id,64);
 if(!id)return null;
 return {id,created_at:text(entry.created_at,40)??'',ip_address:text(entry.ip_address,64)??'',
  actor:text(payload.actor_username,320)??text(payload.actor_id,64)??'',action:text(payload.action,100)??'',log_type:text(payload.log_type,40)??''};
}

export type SsoProvider={id:string;entity_id:string;domains:string[];created_at:string;updated_at:string};
export function ssoProvider(raw:unknown):SsoProvider|null {
 if(!raw||typeof raw!=='object')return null;
 const item=raw as Record<string,unknown>,saml=(item.saml&&typeof item.saml==='object'?item.saml:{}) as Record<string,unknown>;
 const id=text(item.id,64);
 if(!id)return null;
 const domains=Array.isArray(item.domains)?item.domains.map(entry=>text((entry as Record<string,unknown>)?.domain,253)).filter((entry):entry is string=>!!entry):[];
 return {id,entity_id:text(saml.entity_id,2048)??'',domains,created_at:text(item.created_at,40)??'',updated_at:text(item.updated_at,40)??''};
}

class InputError extends Error {}

/** The provider Auth is asked to create: pasted metadata, a few domains and an optional attribute mapping. A metadata address is not
 * accepted: Auth would fetch it from the environment's network, which has no route to the internet unless an enabled provider asks for one. */
export function ssoInput(input:unknown):Record<string,unknown> {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new InputError('settings');
 const value=input as Record<string,unknown>;
 for(const key of Object.keys(value))if(!['metadata_xml','domains','attribute_mapping'].includes(key))throw new InputError('settings');
 const xml=value.metadata_xml;
 if(typeof xml!=='string'||xml.length<20||Buffer.byteLength(xml)>MAX_METADATA||!/<(?:\w+:)?EntityDescriptor[\s>]/.test(xml)||/<!DOCTYPE|<!ENTITY/i.test(xml))throw new InputError('metadata_xml');
 const domains=value.domains;
 if(!Array.isArray(domains)||domains.length<1||domains.length>MAX_DOMAINS||!domains.every(domain=>typeof domain==='string'&&domain.length<=253&&DOMAIN.test(domain)))throw new InputError('domains');
 const out:Record<string,unknown>={type:'saml',metadata_xml:xml,domains:[...new Set(domains as string[])]};
 const mapping=value.attribute_mapping;
 if(mapping!==undefined&&mapping!==null) {
  if(typeof mapping!=='object'||Array.isArray(mapping)||JSON.stringify(mapping).length>MAX_MAPPING)throw new InputError('attribute_mapping');
  const keys=(mapping as Record<string,unknown>).keys;
  if(!keys||typeof keys!=='object'||Array.isArray(keys)||Object.keys(mapping as object).length!==1||Object.keys(keys).length>50)throw new InputError('attribute_mapping');
  for(const entry of Object.values(keys as Record<string,unknown>)) {
   if(!entry||typeof entry!=='object'||Array.isArray(entry))throw new InputError('attribute_mapping');
   const {name,names,default:fallback,...rest}=entry as Record<string,unknown>;
   if(Object.keys(rest).length>0||(name!==undefined&&typeof name!=='string')||(names!==undefined&&(!Array.isArray(names)||!names.every(item=>typeof item==='string')))
    ||(fallback!==undefined&&!['string','number','boolean'].includes(typeof fallback)))throw new InputError('attribute_mapping');
  }
  out.attribute_mapping=mapping;
 }
 return out;
}

/** Auth's own message for a refused request, short, so the page can say why. */
const messageOf=(body:unknown,fallback:string)=>{
 const given=body&&typeof body==='object'?(body as Record<string,unknown>).msg??(body as Record<string,unknown>).message:undefined;
 return typeof given==='string'&&given?given.slice(0,200):fallback;
};

/** GET `/environments/{id}/auth-audit`, GET and POST `/environments/{id}/sso-providers`, DELETE `/environments/{id}/sso-providers/{provider}`,
 * for owners and admins of a ready environment. */
export function authAdminHandler(catalog:Catalog,identify:ManagementIdentity,admin:AuthAdmin|undefined) {
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const match=url.pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(auth-audit|sso-providers)(?:\/([0-9a-f-]{36}))?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const [,environment,page,providerId]=match;
  const allowed=page==='auth-audit'?['GET']:providerId?['DELETE']:['GET','POST'];
  if(!allowed.includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   let state=catalog.signIn(actor,environment!);
   const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment!,state.runtime,true);
   const unavailable=()=>reply(503,{message:"This environment's Auth is not reachable right now. Try again in a moment."});
   if(!admin)return unavailable();
   if(request.method!=='GET'){await refreshCurrentManagement();state=catalog.signIn(actor,environment!);}
   if(page==='auth-audit') {
    const number=(name:string,fallback:number,max:number)=>{
     const given=url.searchParams.get(name);
     if(given===null)return fallback;
     if(!/^\d{1,6}$/.test(given)||Number(given)<1||Number(given)>max)throw new InputError(name);
     return Number(given);
    };
    const pageNumber=number('page',1,100000),perPage=number('per_page',50,100);
    const got=await admin(state.runtime,'GET',`/admin/audit?page=${pageNumber}&per_page=${perPage}`);
    if(got==='unavailable')return unavailable();
    if(got.status!==200||!Array.isArray(got.body))return reply(502,{message:messageOf(got.body,'Auth could not list its audit log')});
    return answer(reply(200,{data:{page:pageNumber,per_page:perPage,total:got.total,entries:got.body.map(auditEntry).filter((entry):entry is AuditEntry=>entry!==null)}}));
   }
   if(request.method==='GET') {
    const got=await admin(state.runtime,'GET','/admin/sso/providers');
    if(got==='unavailable')return unavailable();
    const items=got.body&&typeof got.body==='object'?(got.body as {items?:unknown}).items:undefined;
    if(got.status!==200||!Array.isArray(items))return reply(502,{message:messageOf(got.body,'Auth could not list its identity providers')});
    return answer(reply(200,{data:{providers:items.map(ssoProvider).filter((entry):entry is SsoProvider=>entry!==null)}}));
   }
   if(request.method==='POST') {
    let input:unknown;
    try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
    const got=await admin(state.runtime,'POST','/admin/sso/providers',ssoInput(input));
    if(got==='unavailable')return unavailable();
    if(got.status!==201&&got.status!==200)return reply([400,409,422].includes(got.status)?422:502,{message:messageOf(got.body,'Auth did not accept this identity provider')});
    const created=ssoProvider(got.body);
    // The provider exists once Auth made it: a failure to note it in the activity log must not turn the answer into an error.
    if(created)try{catalog.recordSsoProvider(actor,environment!,'sso_provider.created',created.id);}catch{}
    return answer(reply(201,{data:{provider:created}}));
   }
   const got=await admin(state.runtime,'DELETE',`/admin/sso/providers/${providerId}`);
   if(got==='unavailable')return unavailable();
   if(got.status===404)return reply(404,{message:'No such identity provider'});
   if(got.status!==200)return reply(502,{message:messageOf(got.body,'Auth could not remove this identity provider')});
   try{catalog.recordSsoProvider(actor,environment!,'sso_provider.removed',providerId!);}catch{}
   return answer(reply(200,{data:{removed:true}}));
  } catch(error) {
   if(error instanceof InputError)return reply(400,{message:`Check the ${error.message.replace(/_/g,' ')} setting`});
   if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready')return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
