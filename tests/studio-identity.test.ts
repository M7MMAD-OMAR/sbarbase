import {test,expect} from 'bun:test';
import {gzipSync} from 'node:zlib';
import {identifyStudioDocument} from '../src/control/studio-identity';
import {studioProxy,studioHost,signStudio,studioCookie} from '../src/control/studio';
import type {StudioNavigation} from '../src/control/catalog';

const runtime='e_'+'a'.repeat(24),key=Buffer.alloc(32,44);
const navigation={current:{organization:{id:'org',name:'Company'},project:{id:'project',name:'Manhaj Syria'},environment:{id:'env',name:'production'}},
 selection:{organization:'org',project:'project'},organizations:[],projects:[],environments:[]} as StudioNavigation;

test('document identity honors vendor CSP and uses no project name in HTML',async()=>{
 const response=identifyStudioDocument(new Response('<html><head><title>Emails</title></head><body>Studio</body></html>',
  {headers:{'content-type':'text/html','content-security-policy':"script-src 'nonce-vendor'; object-src 'none'",'content-encoding':'gzip','content-length':'10'}}));
 const body=await response.text();
 expect(body).toContain('<script src="/__sbarbase/identity.js" defer nonce="vendor"></script>');
 expect(response.headers.get('content-security-policy')).toBe("script-src 'nonce-vendor'; object-src 'none'");
 expect(response.headers.has('content-encoding')).toBe(false);expect(response.headers.has('content-length')).toBe(false);
 expect(body).not.toContain('Manhaj');
 const denied=identifyStudioDocument(new Response('<head><title>Studio</title></head>',{headers:{'content-security-policy':"script-src 'none'"}}));
 expect(await denied.text()).not.toContain('identity.js');
});

test('real compressed Studio documents and error responses remain readable through the proxy',async()=>{
 const vendor=Bun.serve({port:0,fetch:request=>{
  const path=new URL(request.url).pathname;
  expect(new URL(request.url).searchParams.has('workspace')).toBe(false);
  return new Response(gzipSync(path.endsWith('error')?'Unavailable':path.endsWith('json')?'{}':'<html><head><title>Emails</title></head><body>Studio</body></html>'),
   {status:path.endsWith('error')?503:200,headers:{'content-encoding':'gzip','content-type':path.endsWith('json')?'application/json':'text/html'}});
 }});
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,navigation:()=>navigation,upstream:()=>`http://127.0.0.1:${vendor.port}`});
 const cookie=`${studioCookie()}=${signStudio(key,{runtime,actor:'owner',expires:Date.now()+60_000,kind:'session',epoch:0})}`;
 try{
  for(const [path,expected] of [['emails','identity.js'],['error','Unavailable'],['json','{}']]){
   const response=await proxy(new Request('http://'+studioHost(runtime)+'/project/default/'+path+'?workspace=forged',{headers:{cookie,accept:'text/html','accept-encoding':'gzip'}}));
   expect(response.headers.has('content-encoding')).toBe(false);expect(response.headers.has('content-length')).toBe(false);
   expect(await response.text()).toContain(expected!);
  }
  expect((await proxy(new Request('http://'+studioHost(runtime)+'/__sbarbase/identity.js'))).status).toBe(401);
  const asset=await proxy(new Request('http://'+studioHost(runtime)+'/__sbarbase/identity.js',{headers:{cookie}}));
  expect(asset.status).toBe(200);expect(asset.headers.get('cache-control')).toBe('no-store');
 }finally{vendor.stop(true);}
});
