import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {managementIdentity} from '../src/control/auth';

test('management identity uses fixed Auth endpoint, ignores identity headers and rejects anonymous users',async()=>{
 const seen:string[]=[];
 const transport=(async(input,init)=>{
  seen.push(String(input));const token=new Headers(init?.headers).get('authorization');
  if(token==='Bearer owner') return Response.json({id:'alice',is_anonymous:false});
  if(token==='Bearer anonymous') return Response.json({id:'alice',is_anonymous:true});
  return Response.json({msg:'invalid token'},{status:401});
 }) as typeof fetch;
 const identify=managementIdentity('https://management.example','management-key',transport);
 const request=(token:string)=>new Request('https://app.example',{headers:{authorization:`Bearer ${token}`,'x-user-id':'alice'}});
 expect(await identify(request('owner'))).toBe('alice');
 expect(await identify(request('application-token'))).toBeNull();
 expect(await identify(request('anonymous'))).toBeNull();
 expect(seen.every(url=>url==='https://management.example/auth/v1/user')).toBe(true);
});

test('HTTP boundary derives actor from authentication, rejects body spoofing, and applies current memberships',async()=>{
 const c=new Catalog(':memory:');
 try {
  const org=c.createOrganization('alice','A');c.setMember('alice',org,'viewer','viewer');
  const handler=managementHandler(c,async r=>({owner:'alice',reader:'viewer',outsider:'mallory'}[r.headers.get('authorization')??'']??null));
  const req=(token:string,method='GET',payload?:unknown)=>new Request(`http://localhost/management/v1/organizations/${org}/projects`,{
   method,headers:{authorization:token,'content-type':'application/json','x-user-id':'alice'},
   ...(payload===undefined?{}:{body:JSON.stringify(payload)})});
  expect((await handler(req(''))).status).toBe(401);
  expect((await handler(req('outsider'))).status).toBe(403);
  expect((await handler(req('reader','POST',{name:'P'}))).status).toBe(403);
  expect((await handler(req('owner','POST',{name:'P',actor:'other'}))).status).toBe(400);
  expect((await handler(req('owner','POST',{name:'P'}))).status).toBe(201);
  expect((await handler(req('reader'))).status).toBe(200);
  c.setMember('alice',org,'viewer',null);
  expect((await handler(req('reader'))).status).toBe(403);
  expect((await handler(req('owner','POST',{name:'x'.repeat(5000)}))).status).toBe(400);
  const unavailable=managementHandler(c,async()=>{throw new Error('secret diagnostic');});
  const response=await unavailable(req('owner'));
  expect(response.status).toBe(503);expect(await response.text()).not.toContain('secret');
 } finally {c.close();}
});
