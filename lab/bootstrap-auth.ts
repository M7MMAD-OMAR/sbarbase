import {createClient} from '@supabase/supabase-js';
import type {BootstrapAuth} from '../src/control/bootstrap';
import {internalToken} from './upstream-app';

export function bootstrapAuth(endpoint:string,secret:string):BootstrapAuth {
 const root=new URL(endpoint);
 if(!['http:','https:'].includes(root.protocol)||root.username||root.password||root.pathname!=='/'||root.search||root.hash)
  throw new Error('Invalid management endpoint');
 const transport=(async(input,init)=>{
  const url=new URL(String(input));
  if(url.origin!=='http://bootstrap.internal'||!url.pathname.startsWith('/auth/v1/'))throw new Error('Unexpected bootstrap request');
  return fetch(new URL(url.pathname.slice('/auth/v1'.length)+url.search,root),{
   ...init,redirect:'error',signal:AbortSignal.timeout(10000)});
 }) as typeof fetch;
 const options={auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:transport}};
 const admin=createClient('http://bootstrap.internal',internalToken(secret,'service_role'),options);
 return {
  async find(operation) {
   let match:{id:string;operation:string}|null=null;
   for(let page=1;page<=100;page++) {
    const {data,error}=await admin.auth.admin.listUsers({page,perPage:100});
    if(error)throw new Error('Management identity lookup failed');
    for(const user of data.users)if(user.app_metadata?.sbarbase_bootstrap===operation) {
     if(match)throw new Error('Ambiguous bootstrap identity');match={id:user.id,operation};
    }
    if(data.users.length<100)return match;
   }
   throw new Error('Management identity lookup exceeded limit');
  },
  async create(email,password,operation) {
   const {data,error}=await admin.auth.admin.createUser({email,password,email_confirm:true,app_metadata:{sbarbase_bootstrap:operation}});
   if(error||!data.user)throw new Error('Management identity creation failed');
   return {id:data.user.id,operation:data.user.app_metadata.sbarbase_bootstrap};
  },
  async verify(email,password) {
   const client=createClient('http://bootstrap.internal',internalToken(secret,'anon'),options);
   const {data,error}=await client.auth.signInWithPassword({email,password});
   if(error)return null;
   const id=data.user?.id??null;
   // The temporary verification session must not be the operator's login session.
   await client.auth.signOut({scope:'local'});return id;
  }
 };
}
