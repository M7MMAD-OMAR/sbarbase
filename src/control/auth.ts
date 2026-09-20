import {createClient} from '@supabase/supabase-js';

export type ManagementIdentity = (request:Request)=>Promise<string|null>;

/** A fixed, dedicated management Supabase endpoint, never an application route.
 * Its signing keys and user database must be separate from hosted environments.
 */
export function managementIdentity(url:string,key:string,transport:typeof fetch=fetch):ManagementIdentity {
  const endpoint=new URL(url);
  if(!['https:','http:'].includes(endpoint.protocol)||endpoint.username||endpoint.password)
    throw new Error('Invalid management endpoint');
  const client=createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
    global:{fetch:(input,init)=>transport(input,{...init,redirect:'error',
      signal:AbortSignal.any([...(init?.signal?[init.signal]:[]),AbortSignal.timeout(5000)])})}});
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
