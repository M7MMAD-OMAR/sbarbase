import {expect,test} from 'bun:test';
import {createClient} from '@supabase/supabase-js';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managementToken,sessionId,factorId,challengeId,verifiedFactor} from './management-fixture';

test('public Auth SDK exposes management revocation retry code while grant remains denied',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const now=Math.floor(Date.now()/1000),password=managementToken('owner','aal1',sessionId,now);
 const realm={auth:'http://source-native.invalid/',anonymousToken:'source-anon',publishableKey:'source-public'};
 let nativeVerifyCalls=0,proxyStatus=0,proxyCode='';
 try{
  catalog.initializeInstallation('source-sdk-regression','owner','Source fixture');
  // Synthetic upstream is confined to this source test. It is not native runtime evidence.
  const transport=(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
   const url=new URL(String(input));
   if(url.origin!=='http://source-native.invalid')throw new Error('Unexpected source fixture origin');
   if(url.pathname==='/user')return Response.json({id:'owner',is_anonymous:false,factors:[verifiedFactor]});
   if(url.pathname===`/factors/${factorId}/verify`&&init?.method==='POST'){
    nativeVerifyCalls++;catalog.managementSecurity.revoke('owner');
    return Response.json({access_token:managementToken('owner','aal2',sessionId,now),refresh_token:'source-refresh',
     expires_in:3600,user:{id:'owner',factors:[verifiedFactor]}});
   }
   throw new Error('Unexpected source fixture path');
  }) as typeof fetch;
  const handler=application(catalog,keys,realm,()=>undefined,transport);
  const client=createClient('http://console.invalid/management',realm.publishableKey,{
   auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
   global:{fetch:(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
    const request=new Request(input,init),response=await handler(request);
    if(new URL(request.url).pathname===`/management/auth/v1/factors/${factorId}/verify`){
     proxyStatus=response.status;proxyCode=(await response.clone().json()).code;
    }
    return response;
   }) as typeof fetch},
  });
  expect((await client.auth.setSession({access_token:password,refresh_token:'source-refresh'})).error).toBeNull();
  const result=await client.auth.mfa.verify({factorId,challengeId,code:'123456'});
  expect(nativeVerifyCalls).toBe(1);expect(proxyStatus).toBe(409);expect(proxyCode).toBe('mfa_retry');
  expect(catalog.managementSecurity.epoch('owner')).toBe(1);
  expect(catalog.managementSecurity.granted('owner',sessionId,now,[factorId])).toBe(false);
  expect(result.error?.status).toBe(409);
  expect(result.error?.code).toBe('mfa_retry');
 }finally{catalog.close();keys.close();}
});
