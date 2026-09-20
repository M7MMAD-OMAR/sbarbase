import {test,expect} from 'bun:test';
import {application} from '../src/control/application';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';

test('management login surface excludes signup and admin APIs before forwarding',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');let calls=0;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,
  (async()=>{calls++;return Response.json({ok:true});}) as typeof fetch);
 try {
  for(const path of ['signup','admin/users','invite','token/extra','%61dmin/users']) {
   expect((await handler(new Request(`http://local/management/auth/v1/${path}`,{method:'POST',headers:{apikey:'public'}}))).status).toBe(404);
  }
  expect((await handler(new Request('http://local/management/auth/v1/user',{method:'PUT'}))).status).toBe(405);
  expect(calls).toBe(0);
  const allowed=await handler(new Request('http://local/management/auth/v1/token?grant_type=password',{method:'POST',headers:{apikey:'public'}}));
  expect(allowed.status).toBe(200);expect(allowed.headers.get('cache-control')).toBe('no-store');expect(calls).toBe(1);
 }finally{catalog.close();keys.close();}
});

test('connection discovery reflects trusted Storage availability after membership checks',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try {
  const org=catalog.createOrganization('owner','O'),project=catalog.createProject('owner',org,'P');
  const environment=catalog.createEnvironment('owner',project,'E');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>({
   auth:'http://auth.invalid',rest:'http://rest.invalid',storage:{url:'http://storage.invalid',tenantHost:'tenant.storage.internal'},
   keys:[],anonymousToken:'anon',enabled:true}),
   (async(input)=>{expect(String(input)).toBe('http://realm.invalid/user');return Response.json({id:'owner'});}) as typeof fetch);
  const response=await handler(new Request(`http://local/management/v1/environments/${environment}/connection`,{headers:{authorization:'Bearer verified'}}));
  expect(response.status).toBe(200);expect((await response.json()).services).toEqual(['auth','rest','storage']);
 }finally{catalog.close();keys.close();}
});
