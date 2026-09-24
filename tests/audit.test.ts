import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';

function two() {
 const catalog=new Catalog(':memory:');
 const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('bob','B');
 catalog.setMember('alice',a,'carol','viewer');catalog.setMember('bob',b,'alice','owner');   // alice owns both, bob owns B
 const shop=catalog.createProject('alice',a,'Shop'),blog=catalog.createProject('bob',b,'Blog');
 catalog.createEnvironment('alice',shop,'production');catalog.createEnvironment('bob',blog,'production');
 const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.');
 const call=(organization:string,actor:string)=>handler(new Request(`http://local/management/v1/organizations/${organization}/audit`,{headers:{authorization:actor}}));
 return {catalog,a,b,shop,blog,call};
}

test('owners see their own organization, never another; viewers see none',async()=>{
 const {catalog,a,b,call}=two();
 const ofA=(await (await call(a,'alice')).json()).data as {action:string;subject:string}[];
 expect(ofA.map(e=>e.action)).toEqual(['environment.created','project.created','membership.changed','organization.created']);
 expect(ofA.some(e=>e.subject.includes('Blog'))).toBe(false);
 expect(ofA[0]).toMatchObject({kind:'environment',subject:'Shop / production'});
 const ofB=(await (await call(b,'bob')).json()).data as {subject:string}[];
 expect(ofB.some(e=>e.subject.includes('Shop'))).toBe(false);
 expect((await call(a,'carol')).status).toBe(403);expect((await call(a,'bob')).status).toBe(403);
 catalog.close();
});

test('a project moved to another client shows there only from its arrival, without the other client\'s ids',async()=>{
 const {catalog,a,b,shop,call}=two();
 catalog.transferProject('alice',shop,b);
 catalog.createEnvironment('alice',shop,'staging');
 const ofB=(await (await call(b,'bob')).json()).data as {action:string;subject:string;detail:Record<string,unknown>}[];
 const shopEvents=ofB.filter(e=>e.subject.startsWith('Shop'));
 expect(shopEvents.map(e=>e.action)).toEqual(['environment.created','provision.cancelled','project.ownership_changed']);   // not its creation in A
 expect(shopEvents[2]!.detail).toEqual({});
 expect(JSON.stringify(ofB)).not.toContain(a);
 expect(ofB.every(e=>['alice','bob','outside'].includes(e.actor)||e.actor.startsWith('system'))).toBe(true);
 const ofA=(await (await call(a,'alice')).json()).data as {subject:string}[];
 expect(ofA.some(e=>e.subject.startsWith('Shop'))).toBe(false);
 catalog.close();
});
