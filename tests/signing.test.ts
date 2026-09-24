import {test,expect} from 'bun:test';
import {signingHandler} from '../src/control/signing';
import {controlHandler} from '../src/control/handler';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';

test('owners and admins rotate the signing key once at a time; viewers cannot; the secret is never answered',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');catalog.setMember('alice',org,'bob','admin');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=signingHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor:string,path='',method='GET')=>handler(new Request(`http://local/management/v1/environments/${environment}/signing-key${path}`,
   {method,headers:{authorization:actor}}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol')).status).toBe(403);
  expect((await call('carol','/rotate','POST')).status).toBe(403);
  expect(((await (await call('alice')).json()) as any).data).toMatchObject({state:'never',rotatedAt:null});
  expect((await call('alice','/rotate','GET')).status).toBe(405);
  expect((await call('alice','','POST')).status).toBe(405);
  const rotated=await call('bob','/rotate','POST');
  expect(rotated.status).toBe(202);
  const body=await rotated.text();
  expect(JSON.parse(body).data.state).toBe('pending');
  expect(body).not.toMatch(/jwt|secret/i);
  expect((await call('alice','/rotate','POST')).status).toBe(409);
  const events=(catalog as any).db.query("SELECT actor FROM audit_events WHERE action='signing.rotation_requested'").all();
  expect(events).toEqual([{actor:'bob'}]);
 } finally {catalog.close();}
});

test('the control handler routes the signing key',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const handler=controlHandler(catalog,new KeyStore(':memory:'),async()=>null);
  const response=await handler(new Request('http://local/management/v1/environments/00000000-0000-4000-8000-000000000000/signing-key'));
  expect(response.status).toBe(401);
 } finally {catalog.close();}
});
