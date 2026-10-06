import {test,expect} from 'bun:test';
import {createHmac} from 'node:crypto';
import {studioProxy,studioUpstream,studioHost,signStudio,STUDIO_COOKIE} from '../src/control/studio';

const key=Buffer.alloc(32,7),runtime='e_'+'a'.repeat(24);
const session=signStudio(key,{runtime,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'session'});
const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
const body=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role:'service_role'});
const token=body+'.'+createHmac('sha256','dummy-secret').update(body).digest('base64url');

for(const path of ['/project/default','//alternate.invalid/dummy','/.//alternate.invalid/dummy','/%2f%2falternate.invalid/dummy']) {
 test(`Studio browser routing retains its configured origin for ${path}`,async()=>{
  const seen:Request[]=[];
  const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,upstream:()=> 'http://trusted.invalid:3000',
   transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));return new Response('dummy');}) as typeof fetch});
  const request=new Request(`http://${studioHost(runtime)}${path}?x=1`,{headers:{cookie:`${STUDIO_COOKIE}=${session}; studio_theme=dark`}});
  expect((await proxy(request)).status).toBe(200);
  expect(seen).toHaveLength(1);
  const destination=new URL(seen[0]!.url),source=new URL(request.url);
  expect(destination.origin).toBe('http://trusted.invalid:3000');
  expect(destination.pathname).toBe(source.pathname);
  expect(destination.search).toBe('?x=1');
  expect(seen[0]!.headers.get('cookie')).toBe('studio_theme=dark');
 });
}

for(const service of ['auth','rest','storage']) {
 test(`Studio ${service} routing retains its configured origin for a double slash path`,async()=>{
  const seen:Request[]=[];
  const upstream=studioUpstream({endpoints:()=>({auth:'http://auth.invalid:9999',rest:'http://rest.invalid:3000',
   storage:{url:'http://storage.invalid:5000',tenantHost:runtime+'.storage.internal'}}),secret:()=> 'dummy-secret',active:()=>true,
   transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));return Response.json({ok:true});}) as typeof fetch});
  const response=await upstream(new Request(`http://local/${runtime}/${service}/v1//alternate.invalid/dummy?x=1`,
   {headers:{authorization:'Bearer '+token}}));
  expect(response.status).toBe(200);
  expect(seen).toHaveLength(1);
  const destination=new URL(seen[0]!.url);
  expect(destination.origin).toBe({auth:'http://auth.invalid:9999',rest:'http://rest.invalid:3000',storage:'http://storage.invalid:5000'}[service]!);
  expect(destination.pathname).toBe('//alternate.invalid/dummy');
  expect(destination.search).toBe('?x=1');
  if(service==='storage')expect(seen[0]!.headers.get('x-forwarded-host')).toBe(runtime+'.storage.internal');
 });
}
