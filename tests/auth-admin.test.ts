import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {authAdminHandler,authAdminThrough,auditEntry,ssoProvider,ssoInput,type AuthAdmin} from '../src/control/auth-admin';

const XML='<?xml version="1.0"?><EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com/meta"></EntityDescriptor>';
const ID='1f3c9449-eb9a-48fb-884e-41092efdbcdf';

test('an audit entry keeps who, what, where and when, and nothing else Auth recorded',()=>{
 expect(auditEntry({id:'a',created_at:'2026-10-30T07:00:00Z',ip_address:'203.0.113.4',payload:{actor_id:'u1',actor_username:'p@example.com',action:'login',log_type:'account',traits:{provider:'email',secret:'S'}}}))
  .toEqual({id:'a',created_at:'2026-10-30T07:00:00Z',ip_address:'203.0.113.4',actor:'p@example.com',action:'login',log_type:'account'});
 expect(auditEntry({id:'b',payload:{actor_id:'u2',action:'token_refreshed'}})!.actor).toBe('u2');
 expect(auditEntry(null)).toBeNull();expect(auditEntry({payload:{}})).toBeNull();
 expect(ssoProvider({id:ID,saml:{entity_id:'https://idp.example.com/meta',metadata_xml:XML,attribute_mapping:{keys:{}}},domains:[{domain:'example.org'}],created_at:'c',updated_at:'u'}))
  .toEqual({id:ID,entity_id:'https://idp.example.com/meta',domains:['example.org'],created_at:'c',updated_at:'u'});
});

test('the provider input takes pasted metadata and domains, refuses an address, entities and odd mappings',()=>{
 expect(ssoInput({metadata_xml:XML,domains:['example.org','example.org','a.example.com']})).toEqual({type:'saml',metadata_xml:XML,domains:['example.org','a.example.com']});
 const mapping={keys:{email:{name:'mail'},groups:{names:['g','h'],default:'none'}}};
 expect(ssoInput({metadata_xml:XML,domains:['example.org'],attribute_mapping:mapping}).attribute_mapping).toEqual(mapping);
 for(const bad of [{metadata_url:'https://idp.example.com/m',domains:['example.org']},{metadata_xml:XML,domains:[]},{metadata_xml:XML,domains:['Not A Domain']},{metadata_xml:XML,domains:['localhost']},
  {metadata_xml:'<a/>',domains:['example.org']},{metadata_xml:XML+'<!DOCTYPE x [<!ENTITY e "x">]>',domains:['example.org']},{metadata_xml:XML,domains:['example.org'],attribute_mapping:{keys:{email:{evil:1}}}},
  {metadata_xml:XML,domains:['example.org'],attribute_mapping:{keys:[]}},{metadata_xml:XML,domains:['example.org'],attribute_mapping:{other:{}}},{metadata_xml:XML,domains:Array.from({length:21},(_,i)=>`d${i}.example.com`)},[],null])
  expect(()=>ssoInput(bad)).toThrow();
});

test('the transport calls only the three admin paths, with the environment token and address, and never throws',async()=>{
 const calls:{url:string;auth:string|null;method:string}[]=[];
 const transport=(async(url:URL,init:RequestInit)=>{calls.push({url:String(url),auth:new Headers(init.headers).get('authorization'),method:init.method!});
  return new Response(JSON.stringify([{id:'x'}]),{status:200,headers:{'x-total-count':'7'}});}) as unknown as typeof fetch;
 const admin=authAdminThrough(runtime=>runtime==='e_'+'a'.repeat(24)?{url:'http://10.0.0.5:9999',token:'TOKEN-A'}:runtime==='maint'?'maintenance':undefined,transport);
 const a='e_'+'a'.repeat(24);
 const got=await admin(a,'GET','/admin/audit?page=2&per_page=10');
 expect(got).toEqual({status:200,body:[{id:'x'}],total:7});
 expect(calls[0]).toEqual({url:'http://10.0.0.5:9999/admin/audit?page=2&per_page=10',auth:'Bearer TOKEN-A',method:'GET'});
 for(const path of ['/admin/users','/admin/audit/../users','/admin/sso/providers/../users','/user','/admin/sso/providers/not-a-uuid','//evil.example.com/admin/audit'])expect(await admin(a,'GET',path)).toBe('unavailable');
 expect(await admin('maint','GET','/admin/audit')).toBe('unavailable');
 expect(await admin('e_'+'b'.repeat(24),'GET','/admin/audit')).toBe('unavailable');
 expect(calls.length).toBe(1);
 const failing=authAdminThrough(()=>({url:'http://10.0.0.5:9999',token:'T'}),(async()=>{throw new Error('down');}) as unknown as typeof fetch);
 expect(await failing(a,'GET','/admin/audit')).toBe('unavailable');
});

test('the routes are for owners and admins, each environment reaches only its own Auth, and provider changes are audited by id',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const project=catalog.createProject('alice',org,'P');
  const first=catalog.createEnvironment('alice',project,'production'),second=catalog.createEnvironment('alice',project,'staging');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const runtimes={first:catalog.getProvision('alice',first).runtime,second:catalog.getProvision('alice',second).runtime};
  const asked:{runtime:string;method:string;path:string;body?:unknown}[]=[];
  const admin:AuthAdmin=async(runtime,method,path,body)=>{asked.push({runtime,method,path,body});
   if(path.startsWith('/admin/audit'))return {status:200,body:[{id:'a1',created_at:'t',ip_address:'1.2.3.4',payload:{actor_username:runtime===runtimes.first?'first@example.com':'second@example.com',action:'login',log_type:'account',traits:{x:1}}}],total:1};
   if(method==='POST')return {status:201,body:{id:ID,saml:{entity_id:'e'},domains:[{domain:'example.org'}]},total:null};
   if(method==='DELETE')return {status:200,body:{},total:null};
   return {status:200,body:{items:[{id:ID,saml:{entity_id:'e',metadata_xml:XML},domains:[{domain:'example.org'}]}]},total:null};
  };
  const handler=authAdminHandler(catalog,async request=>request.headers.get('authorization'),admin);
  const call=(actor:string,environment:string,leaf:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/${leaf}`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  for(const actor of ['carol','bob']){expect((await call(actor,first,'auth-audit')).status).toBe(403);expect((await call(actor,first,'sso-providers')).status).toBe(403);}
  expect(asked.length).toBe(0);
  expect((await call('alice',first,'auth-audit','POST')).status).toBe(405);
  expect((await call('alice',first,'sso-providers/'+ID,'GET')).status).toBe(405);
  const one=await (await call('alice',first,'auth-audit?page=1&per_page=20')).json() as {data:{entries:{actor:string}[];total:number}};
  const two=await (await call('alice',second,'auth-audit')).json() as {data:{entries:{actor:string}[]}};
  expect(one.data.entries[0]!.actor).toBe('first@example.com');expect(two.data.entries[0]!.actor).toBe('second@example.com');
  expect(JSON.stringify(one)).not.toContain('traits');
  expect(asked.map(item=>item.runtime)).toEqual([runtimes.first,runtimes.second]);
  for(const query of ['page=0','page=x','per_page=101','per_page=-1'])expect((await call('alice',first,'auth-audit?'+query)).status).toBe(400);
  expect((await call('alice',first,'sso-providers')).status).toBe(200);
  expect((await call('alice',first,'sso-providers','POST',{metadata_url:'https://x.example.com',domains:['example.org']})).status).toBe(400);
  const made=await call('alice',first,'sso-providers','POST',{metadata_xml:XML,domains:['example.org']});
  expect(made.status).toBe(201);
  expect((await call('alice',first,'sso-providers/'+ID,'DELETE')).status).toBe(200);
  const events=JSON.stringify(catalog.auditEvents('alice',org));
  expect(events).toContain('sso_provider.created');expect(events).toContain('sso_provider.removed');expect(events).toContain(ID);expect(events).not.toContain('idp.example.com');
  // An unreachable Auth is a 503, not a crash.
  const down=authAdminHandler(catalog,async request=>request.headers.get('authorization'),async()=>'unavailable');
  expect((await down(new Request(`http://local/management/v1/environments/${first}/auth-audit`,{headers:{authorization:'alice'}}))).status).toBe(503);
 } finally {catalog.close();}
});
