import {createClient} from '@supabase/supabase-js';

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
export function managementIdentity(url:string,key:string,transport:typeof fetch=fetch):ManagementIdentity {
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
    return user.id;
  };
}
