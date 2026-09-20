import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {keyHandler} from '../src/control/key-http';
import {managedGateway} from '../src/gateway/managed';

test('only ready environments can issue keys; actor scope, one-time disclosure and revocation bind gateway',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try {
  const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('bob','B');
  catalog.setMember('alice',a,'viewer','viewer');
  const p=catalog.createProject('alice',a,'P'),q=catalog.createProject('bob',b,'Q');
  const e=catalog.createEnvironment('alice',p,'production'),f=catalog.createEnvironment('bob',q,'production');
  const handler=keyHandler(catalog,keys,async r=>r.headers.get('authorization'));
  const request=(actor:string,env=e,tail='keys',method='GET')=>handler(new Request(`http://localhost/management/v1/environments/${env}/${tail}`,{method,headers:{authorization:actor}}));
  expect((await request('alice',e,'keys','POST')).status).toBe(409);
  let job;while((job=catalog.claimProvision())) catalog.finishProvision(job.environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',e).runtime;
  expect((await request('viewer',e,'keys','POST')).status).toBe(403);
  expect((await request('bob',e,'keys','POST')).status).toBe(403);
  expect((await request('viewer',e,'connection')).status).toBe(200);
  const issued=await request('alice',e,'keys','POST');expect(issued.status).toBe(201);
  const {id,token}=await issued.json();
  expect(await (await request('alice')).text()).not.toContain(token);
  expect((await request('bob',f,`keys/${id}`,'DELETE')).status).toBe(404);
  const gateway=managedGateway(catalog,keys,()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',keys:[],anonymousToken:'internal',enabled:true}),
   (async()=>Response.json({ok:true})) as typeof fetch);
  const call=()=>gateway(new Request(`http://localhost/${runtime}/rest/v1/`,{headers:{apikey:token}}));
  expect((await call()).status).toBe(200);
  expect((await request('alice',e,`keys/${id}`,'DELETE')).status).toBe(200);
  expect((await call()).status).toBe(401);
  catalog.close();expect((await call()).status).toBe(503);
 } finally {catalog.close();keys.close();}
});
