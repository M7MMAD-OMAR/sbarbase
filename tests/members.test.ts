import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';

function org() {
 const catalog=new Catalog(':memory:');
 const a=catalog.createOrganization('alice','A');
 catalog.setMember('alice',a,'bob','admin');catalog.setMember('alice',a,'carol','viewer');
 const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
 const call=(actor:string,target:string,method:string,body?:unknown)=>handler(new Request(`http://local/management/v1/organizations/${a}/members/${encodeURIComponent(target)}`,
  {method,headers:{authorization:actor,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
 return {catalog,a,call};
}

test('an owner changes a role and removes a member; the removed member loses access at once',async()=>{
 const {catalog,a,call}=org();
 const promoted=await call('alice','carol','PUT',{role:'admin'});
 expect(promoted.status).toBe(200);
 expect((await promoted.json()).data).toContainEqual({actor:'carol',role:'admin'});
 expect((await call('alice','carol','DELETE')).status).toBe(200);
 expect(()=>catalog.listProjects('carol',a)).toThrow('Forbidden');
 catalog.close();
});

test('admins and viewers cannot change members, and nobody raises themselves',async()=>{
 const {catalog,call}=org();
 expect((await call('bob','carol','PUT',{role:'admin'})).status).toBe(403);
 expect((await call('bob','bob','PUT',{role:'owner'})).status).toBe(403);
 expect((await call('carol','carol','PUT',{role:'owner'})).status).toBe(403);
 expect((await call('mallory','carol','DELETE')).status).toBe(403);
 catalog.close();
});

test('the last owner stays, an unknown member is not added here, and bad input is refused',async()=>{
 const {catalog,call}=org();
 const last=await call('alice','alice','PUT',{role:'viewer'});
 expect(last.status).toBe(409);expect((await last.json()).message).toBe('Last owner cannot be removed');
 expect((await call('alice','alice','DELETE')).status).toBe(409);
 expect((await call('alice','dave','PUT',{role:'viewer'})).status).toBe(404);    // adding people is for invitations
 for(const body of [{role:'root'},{role:'admin',extra:1},{}])expect((await call('alice','carol','PUT',body)).status).toBe(400);
 expect((await call('alice','carol','POST')).status).toBe(405);
 catalog.close();
});
