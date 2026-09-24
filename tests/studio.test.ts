import {test,expect} from 'bun:test';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {studioHandler,studioProxy,studioUpstream,studioHost,studioRuntime,signStudio,verifyStudio,serviceKey,STUDIO_COOKIE} from '../src/control/studio';

const key=Buffer.alloc(32,7);
const A='e_'+'a'.repeat(24),B='e_'+'b'.repeat(24);

function jwt(secret:string,claims:object) {
 const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode(claims);
 return message+'.'+createHmac('sha256',secret).update(message).digest('base64url');
}

test('each environment is its own Studio origin',()=>{
 expect(studioHost(A)).toBe('a'.repeat(24)+'.studio.localhost');
 expect(studioRuntime(studioHost(A))).toBe(A);
 expect(studioRuntime('localhost')).toBeNull();
 expect(studioRuntime('x.'+studioHost(A))).toBeNull();
 expect(()=>studioHost('../etc')).toThrow();
});

test('tickets and sessions are signed, bound to one environment and one kind, and expire',()=>{
 const ticket=signStudio(key,{runtime:A,actor:'alice',expires:Date.now()+60_000,kind:'ticket'});
 expect(verifyStudio(key,ticket,A,'ticket')?.actor).toBe('alice');
 expect(verifyStudio(key,ticket,B,'ticket')).toBeNull();
 expect(verifyStudio(key,ticket,A,'session')).toBeNull();
 expect(verifyStudio(Buffer.alloc(32,8),ticket,A,'ticket')).toBeNull();
 expect(verifyStudio(key,ticket,A,'ticket',Date.now()+120_000)).toBeNull();
 const [body,signature]=ticket.split('.');
 const forged=Buffer.from(JSON.stringify({runtime:A,actor:'mallory',expires:Date.now()+60_000,kind:'ticket'})).toString('base64url');
 expect(verifyStudio(key,forged+'.'+signature,A,'ticket')).toBeNull();
 expect(verifyStudio(key,body+'.'+signature+'.x',A,'ticket')).toBeNull();
});

test('the console starts, stops and opens Studio only for owners and admins of a ready environment',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const other=catalog.createOrganization('bob','B');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=studioHandler(catalog,async request=>request.headers.get('authorization'),()=>key);
  const call=(actor:string,method='GET',tail='')=>handler(new Request(`http://local/management/v1/environments/${environment}/studio${tail}`,
   {method,headers:{authorization:actor}}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol')).status).toBe(403);
  expect((await call('bob','POST')).status).toBe(403);
  expect((await call('alice','PUT')).status).toBe(405);
  const started=await call('alice','POST');expect(started.status).toBe(202);
  expect(((await started.json()) as {data:{desired:string}}).data.desired).toBe('running');
  expect((await call('alice','POST','/session')).status).toBe(409);
  // The supervisor records the outcome in the same row.
  (catalog as unknown as {db:{query:(sql:string)=>{run:(...a:unknown[])=>unknown}}}).db
   .query("UPDATE studio_sessions SET state='running'").run();
  const opened=await call('alice','POST','/session');expect(opened.status).toBe(201);
  const {host,path}=await opened.json() as {host:string;path:string};
  const runtime=catalog.getProvision('alice',environment).runtime;
  expect(host).toBe(studioHost(runtime));
  expect(verifyStudio(key,decodeURIComponent(path.split('ticket=')[1]!),runtime,'ticket')?.actor).toBe('alice');
  expect(catalog.studioAllowed('alice',runtime)).toBe(true);
  expect(catalog.studioAllowed('carol',runtime)).toBe(false);
  expect(catalog.studioAllowed('bob',runtime)).toBe(false);
  expect((await call('alice','DELETE')).status).toBe(202);
  expect(catalog.studio('alice',environment).desired).toBe('stopped');
  expect(other).toBeString();
 } finally {catalog.close();}
});

test('the Studio origin admits a ticket once, then only its session cookie, and never another environment',async()=>{
 const seen:Request[]=[];
 let members=new Set(['alice']);
 const proxy=studioProxy({key:()=>key,allowed:actor=>members.has(actor),upstream:runtime=>runtime===A?'http://studio-a.internal:3000':undefined,
  transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));
   return new Response('studio page',{status:200,headers:{'set-cookie':'studio_theme=dark; Path=/'}});}) as typeof fetch});
 const ticket=signStudio(key,{runtime:A,actor:'alice',expires:Date.now()+60_000,kind:'ticket'});
 const enter=await proxy(new Request(`http://${studioHost(A)}:8790/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`));
 expect(enter.status).toBe(302);
 expect(enter.headers.get('location')).toBe('/project/default');
 const cookie=enter.headers.get('set-cookie')!;
 expect(cookie).toContain('HttpOnly');expect(cookie).toContain('SameSite=Lax');
 const session=cookie.split(';')[0]!;
 // A page without the cookie is refused and never reaches Studio.
 expect((await proxy(new Request(`http://${studioHost(A)}/project/default`))).status).toBe(401);
 expect(seen.length).toBe(0);
 const page=await proxy(new Request(`http://${studioHost(A)}/project/default?x=1`,{headers:{cookie:`${session}; studio_theme=dark`}}));
 expect(page.status).toBe(200);
 expect(page.headers.getSetCookie()).toEqual(['studio_theme=dark; Path=/']);
 expect(seen[0]!.url).toBe('http://studio-a.internal:3000/project/default?x=1');
 // Studio never sees Sbarbase's own cookie.
 expect(seen[0]!.headers.get('cookie')).toBe('studio_theme=dark');
 // A session for A does not open B, even if a browser sent it there.
 expect((await proxy(new Request(`http://${studioHost(B)}/project/default`,{headers:{cookie:session}}))).status).toBe(401);
 // A ticket for A does not enter B.
 expect((await proxy(new Request(`http://${studioHost(B)}/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`))).status).toBe(401);
 // Losing the role ends access on the next request.
 members=new Set();
 expect((await proxy(new Request(`http://${studioHost(A)}/project/default`,{headers:{cookie:session}}))).status).toBe(401);
});

test('Studio server-side calls reach only their own environment, with its own service key',async()=>{
 const seen:Request[]=[];
 const upstream=studioUpstream({
  endpoints:runtime=>runtime===A?{auth:'http://auth-a:9999',rest:'http://rest-a:3000',storage:{url:'http://storage:5000',tenantHost:A+'.storage.internal'}}:undefined,
  secret:runtime=>runtime===A?'secret-a':runtime===B?'secret-b':undefined,active:()=>true,
  transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));return Response.json({ok:true});}) as typeof fetch});
 const service=jwt('secret-a',{role:'service_role'});
 const call=(path:string,token:string,method='GET')=>upstream(new Request('http://172.18.0.1:54320'+path,
  {method,headers:{authorization:'Bearer '+token,apikey:token}}));
 expect((await call(`/${A}/auth/v1/admin/users`,service)).status).toBe(200);
 expect(seen[0]!.url).toBe('http://auth-a:9999/admin/users');
 expect((await call(`/${A}/storage/v1/bucket`,service)).status).toBe(200);
 expect(seen[1]!.url).toBe('http://storage:5000/bucket');
 expect(seen[1]!.headers.get('x-forwarded-host')).toBe(A+'.storage.internal');
 expect((await call(`/${A}/rest/v1/notes?select=*`,service)).status).toBe(200);
 expect(seen[2]!.url).toBe('http://rest-a:3000/notes?select=*');
 // Anon keys, B's service key and forged tokens are refused before any call.
 expect((await call(`/${A}/auth/v1/admin/users`,jwt('secret-a',{role:'anon'}))).status).toBe(401);
 expect((await call(`/${A}/auth/v1/admin/users`,jwt('secret-b',{role:'service_role'}))).status).toBe(401);
 expect((await call(`/${A}/auth/v1/admin/users`,jwt('secret-a',{role:'service_role',exp:1}))).status).toBe(401);
 expect((await call(`/${B}/auth/v1/admin/users`,service)).status).toBe(503);
 expect((await call('/other/path',service)).status).toBe(404);
 expect(seen.length).toBe(3);
 expect(serviceKey(service,'secret-a')).toBe(true);
 expect(serviceKey(service,'secret-b')).toBe(false);
});
