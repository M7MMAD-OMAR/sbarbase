import {createClient} from '@supabase/supabase-js';
import type {ManagementSecurity} from './management-security';

export type ManagementIdentity = (request:Request)=>Promise<string|null>;

/** The JSON answer every management route gives: never cached, never content sniffed. */
export function reply(status:number,data:unknown) {
  return Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
}

/** The caller's management actor, or the 503 or 401 answer to return instead. */
export async function authenticate(identify:ManagementIdentity,request:Request):Promise<string|Response> {
  let actor:string|null;
  try {actor=await identify(request);} catch {return reply(503,{message:'Authentication unavailable'});}
  return actor||reply(401,{message:'Authentication required'});
}

/** A fixed, dedicated management Supabase endpoint, never an application route.
 * Its signing keys and user database must be separate from hosted environments.
 */
export type PasswordIdentity={actor:string;session:string;aal:'aal1'|'aal2';expiresAt:number;passwordAt:number;verifiedAt:number;factors:{id:string;status:string;factor_type:string}[]};
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Parse only after Auth has validated this exact token. User metadata never grants access. */
function passwordClaims(token:string,actor:string,factors:PasswordIdentity['factors']):PasswordIdentity|null {
  try {
    const segments=token.split('.');if(segments.length!==3)return null;
    const claims=JSON.parse(Buffer.from(segments[1]!,'base64url').toString('utf8'));
    const now=Math.floor(Date.now()/1000);
    if(claims.sub!==actor||!UUID.test(claims.session_id)||!Number.isSafeInteger(claims.exp)||claims.exp<=now||
      !['aal1','aal2'].includes(claims.aal)||!Array.isArray(claims.amr))return null;
    const timestamp=(method:string)=>Math.max(0,...claims.amr.filter((entry:any)=>entry?.method===method&&
      Number.isSafeInteger(entry.timestamp)&&entry.timestamp>0&&entry.timestamp<=now).map((entry:any)=>entry.timestamp));
    const passwordAt=timestamp('password'),verifiedAt=timestamp('totp');
    if(!passwordAt||now-passwordAt>=43200)return null;
    return {actor,session:claims.session_id,aal:claims.aal,expiresAt:claims.exp,passwordAt,verifiedAt,factors};
  }catch{return null;}
}
/** Password sessions can enroll and challenge native MFA, but cannot use management APIs. */
export function managementPasswordIdentity(url:string,key:string,transport:typeof fetch=fetch):(request:Request)=>Promise<PasswordIdentity|null> {
  const endpoint=new URL(url);
  if(!['https:','http:'].includes(endpoint.protocol)||endpoint.username||endpoint.password)
    throw new Error('Invalid management endpoint');
  const guardedFetch=Object.assign(
    (input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>transport(input,{...init,redirect:'error',
      signal:AbortSignal.any([...(init?.signal?[init.signal]:[]),AbortSignal.timeout(5000)])}),
    {preconnect:(...args:Parameters<typeof fetch.preconnect>)=>transport.preconnect?.(...args)});
  const client=createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
    global:{fetch:guardedFetch}});
  return async request=>{
    const header=request.headers.get('authorization');
    if(!header||header.length>8192||!/^Bearer \S+$/i.test(header)) return null;
    const {data,error}=await client.auth.getUser(header.slice(7));
    if(error) {
      if(error.status===401||error.status===403||error.status===400) return null;
      throw new Error('Management authentication unavailable');
    }
    const user=data.user;
    if(!user||user.is_anonymous||!user.id||user.id.length>200) return null;
    return passwordClaims(header.slice(7),user.id,(user.factors??[]).map(factor=>({id:factor.id,status:factor.status,factor_type:factor.factor_type})));
  };
}

/** Management access needs a native aal2 grant and the currently verified matching factor. */
export function hasManagementMfa(identity:PasswordIdentity,security:ManagementSecurity):boolean {
  const now=Math.floor(Date.now()/1000);
  return Number.isSafeInteger(identity.expiresAt)&&identity.expiresAt>now&&
    Number.isSafeInteger(identity.passwordAt)&&identity.passwordAt>0&&identity.passwordAt<=now&&now-identity.passwordAt<43200&&
    identity.aal==='aal2'&&identity.verifiedAt>0&&security.granted(identity.actor,identity.session,
    identity.verifiedAt,identity.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp').map(factor=>factor.id));
}
export function managementIdentity(url:string,key:string,transport:typeof fetch=fetch,security?:ManagementSecurity):ManagementIdentity {
  const identify=managementPasswordIdentity(url,key,transport);
  return async request=>{
    const identity=await identify(request);
    return identity&&security&&hasManagementMfa(identity,security)?identity.actor:null;
  };
}
