import {afterEach,expect,test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {studioProxy,studioHost,signStudio,studioCookie,useStudioHosts} from '../src/control/studio';
import {studioHosts} from '../src/control/studio-host';
import {STUDIO_SMTP_ASSET_VERSION} from '../src/control/studio-smtp-assets';

const runtime='e_'+'a'.repeat(24),key=Buffer.alloc(32,44);
const path='/_next/static/chunks/08dh2y9gjjf0f.js';
const original=readFileSync(new URL('./fixtures/studio-smtp/08dh2y9gjjf0f.module.txt',import.meta.url),'utf8');
afterEach(()=>useStudioHosts(studioHosts(undefined)));
function fixture(status=200,body=original){
 let calls=0;
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,upstream:()=> 'http://studio.invalid',
  authConfig:async()=>new Response('{}'),
  transport:(async()=>{calls++;return new Response(body,{status,headers:{'content-type':'application/javascript','cache-control':'public, max-age=31536000, immutable'}});}) as unknown as typeof fetch});
 const cookie=()=>`${studioCookie()}=${signStudio(key,{runtime,actor:'owner',expires:Date.now()+60_000,kind:'session',epoch:0})}`;
 const request=(query='',authorized=true)=>new Request('http://'+studioHost(runtime)+path+query,{headers:authorized?{cookie:cookie()}:undefined});
 return {proxy,request,calls:()=>calls};
}

test('a current versioned SMTP chunk can stay in the private browser cache',async()=>{
 const f=fixture(),response=await f.proxy(f.request('?sbarbase_smtp='+STUDIO_SMTP_ASSET_VERSION));
 expect(response.status).toBe(200);
 expect(response.headers.get('cache-control')).toBe('private, max-age=31536000, immutable');
 expect(response.headers.has('etag')).toBe(false);
 expect(response.headers.has('content-encoding')).toBe(false);
 expect(await response.text()).toContain('sbarbase-smtp');
});
for(const query of ['', '?sbarbase_smtp=old', '?sbarbase_smtp='+STUDIO_SMTP_ASSET_VERSION+'&sbarbase_smtp=old']){
 test('an unversioned or ambiguous SMTP chunk is not cached: '+query,async()=>{
  const f=fixture(),response=await f.proxy(f.request(query));
  expect(response.status).toBe(200);expect(response.headers.get('cache-control')).toBe('no-store');await response.text();
 });
}
for(const [status,body] of [[503,'Unavailable'],[200,'Unexpected build']] as const){
 test('an unavailable or unrecognized SMTP chunk is never cached: '+status,async()=>{
  const f=fixture(status,body),response=await f.proxy(f.request('?sbarbase_smtp='+STUDIO_SMTP_ASSET_VERSION));
  expect(response.status).toBe(503);expect(response.headers.get('cache-control')).toBe('no-store');await response.text();
 });
}
test('private asset caching does not bypass Studio authentication',async()=>{
 const f=fixture(),response=await f.proxy(f.request('?sbarbase_smtp='+STUDIO_SMTP_ASSET_VERSION,false));
 expect(response.status).toBe(401);expect(response.headers.get('cache-control')).toBe('no-store');expect(f.calls()).toBe(0);
});
