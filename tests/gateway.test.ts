import {test,expect} from 'bun:test';
import {createGateway, type EnvironmentRoute} from '../src/gateway/handler';
const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',keys:['key-a'],anonymousToken:'anon-a',enabled:true};
function setup() {
 const calls:{url:string;options:RequestInit}[]=[];
 const registry=new Map([['a_prod',route],['b_prod',{...route,keys:['key-b']}],['disabled',{...route,enabled:false}]]);
 const handler=createGateway(registry,(async(url,options)=>{calls.push({url:String(url),options:options!});return Response.json([]);}) as typeof fetch);
 return {handler,calls};
}
for(const [name,path,key,status] of [
 ['missing key','/a_prod/rest/v1/items','',401],
 ['cross environment','/b_prod/rest/v1/items','key-a',401],
 ['disabled environment','/disabled/rest/v1/items','key-a',404],
 ['unknown environment','/unknown/rest/v1/items','key-a',404],
 ['encoded slash','/a_prod/rest/v1/%2fadmin','key-a',400],
] as const) test(name,async()=>{
 const {handler,calls}=setup();
 expect((await handler(new Request('http://local'+path,{headers:{apikey:key}}))).status).toBe(status);
 expect(calls).toHaveLength(0);
});
test('forward selected route and strip client-injected internal headers',async()=>{
 const {handler,calls}=setup();
 await handler(new Request('http://local/a_prod/rest/v1/items?select=id',{headers:{apikey:'key-a','x-forwarded-host':'evil','x-project-id':'b_prod',authorization:'Bearer user-token',prefer:'return=representation'}}));
 expect(calls[0]?.url).toBe('http://rest:3000/items?select=id');
 const headers=new Headers(calls[0]?.options.headers);
 expect(headers.get('authorization')).toBe('Bearer user-token');
 expect(headers.get('prefer')).toBe('return=representation');
 expect(headers.has('x-forwarded-host')).toBe(false);
 expect(headers.has('x-project-id')).toBe(false);
});
test('anonymous JWT used when no bearer supplied',async()=>{
 const {handler,calls}=setup(); await handler(new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a'}}));
 expect(new Headers(calls[0]?.options.headers).get('authorization')).toBe('Bearer anon-a');
});
test('oversize stream rejected before upstream',async()=>{
 const {handler,calls}=setup();
 const response=await handler(new Request('http://local/a_prod/rest/v1/items',{method:'POST',headers:{apikey:'key-a'},body:'x'.repeat(1048577)}));
 expect(response.status).toBe(413); expect(calls).toHaveLength(0);
});
test('SDK API-key bearer becomes anonymous upstream token',async()=>{
 const {handler,calls}=setup();
 await handler(new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a',authorization:'Bearer key-a'}}));
 expect(new Headers(calls[0]?.options.headers).get('authorization')).toBe('Bearer anon-a');
});
test('removed key stops subsequent requests',async()=>{
 const registry=new Map([['a_prod',route]]);
 let calls=0;
 const handler=createGateway(registry,(async()=>{calls++;return Response.json([]);}) as typeof fetch);
 const request=()=>new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a'}});
 expect((await handler(request())).status).toBe(200);
 registry.set('a_prod',{...route,keys:[]});
 expect((await handler(request())).status).toBe(401);
 expect(calls).toBe(1);
});
test('upstream failure is sanitized',async()=>{
 const handler=createGateway(new Map([['a_prod',route]]),(async()=>{throw new Error('private upstream detail');}) as typeof fetch);
 const response=await handler(new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a'}}));
 expect(response.status).toBe(502);
 expect(await response.text()).not.toContain('private');
});
test('key store failure cannot fall back to static key acceptance',async()=>{
 let forwarded=false;
 const handler=createGateway(new Map([['a_prod',route]]),(async()=>{forwarded=true;return Response.json([]);}) as typeof fetch,()=>{throw new Error('private storage failure');});
 const response=await handler(new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a'}}));
 expect(response.status).toBe(503);expect(forwarded).toBe(false);
 expect(await response.text()).not.toContain('private');
});

test('Storage tenant header comes only from trusted configuration and preserves upload headers',async()=>{
 let seen:Headers|undefined,target='';
 const handler=createGateway(new Map([['a_prod',{...route,storage:{url:'http://shared-storage:5000',tenantHost:'a_prod.storage.internal'}}]]),
  (async(url,init)=>{target=String(url);seen=new Headers(init?.headers);return new Response('ok');}) as typeof fetch);
 const response=await handler(new Request('http://local/a_prod/storage/v1/object/private/file.txt',{method:'POST',body:'abc',headers:{
  apikey:'key-a',authorization:'Bearer user','x-forwarded-host':'b_prod.storage.internal','x-forwarded-prefix':'/tenants/b_prod',
  'x-upsert':'true','cache-control':'max-age=60','content-type':'text/plain'}}));
 expect(response.status).toBe(200);expect(target).toBe('http://shared-storage:5000/object/private/file.txt');
 expect(seen?.get('x-forwarded-host')).toBe('a_prod.storage.internal');
 expect(seen?.has('x-forwarded-prefix')).toBe(false);expect(seen?.has('apikey')).toBe(false);
 expect(seen?.get('x-upsert')).toBe('true');expect(seen?.get('authorization')).toBe('Bearer user');
});
test('unconfigured Storage is unavailable and broken request streams never reach upstream',async()=>{
 const {handler,calls}=setup();
 expect((await handler(new Request('http://local/a_prod/storage/v1/object/private/file',{headers:{apikey:'key-a'}}))).status).toBe(404);
 const stream=new ReadableStream({start(controller){controller.error(new Error('private body detail'));}});
 const response=await handler(new Request('http://local/a_prod/rest/v1/items',{method:'POST',headers:{apikey:'key-a'},body:stream}));
 expect(response.status).toBe(400);expect(await response.text()).not.toContain('private');expect(calls).toHaveLength(0);
});
test('slow body deadline rejects even when cancellation resolves the pending read',async()=>{
 let forwarded=false,cancelled=false;
 const handler=createGateway(new Map([['a_prod',route]]),(async()=>{forwarded=true;return new Response('wrong');}) as typeof fetch,undefined,20);
 const delayed=new ReadableStream<Uint8Array>({cancel(){cancelled=true;}});
 expect((await handler(new Request('http://local/a_prod/rest/v1/items',{method:'POST',headers:{apikey:'key-a'},body:delayed}))).status).toBe(400);
 expect(forwarded).toBe(false);expect(cancelled).toBe(true);
});

test('only public and signed Storage reads may omit API keys',async()=>{
 let forwarded=0;
 const storage={url:'http://storage:5000',tenantHost:'a_prod.storage.internal'};
 const handler=createGateway(new Map([['a_prod',{...route,storage}]]),(async()=>{forwarded++;return new Response('upstream');}) as typeof fetch);
 for(const path of ['/object/public/bucket/file','/object/sign/bucket/file?token=capability']) {
  expect((await handler(new Request('http://local/a_prod/storage/v1'+path))).status).toBe(200);
  expect((await handler(new Request('http://local/a_prod/storage/v1'+path,{method:'HEAD'}))).status).toBe(200);
  expect((await handler(new Request('http://local/a_prod/storage/v1'+path,{method:'DELETE'}))).status).toBe(401);
 }
 for(const path of ['/object/sign/bucket/file','/object/sign/bucket/file?token=one&token=two','/object/authenticated/bucket/file','/object/bucket/file','/bucket'])
  expect((await handler(new Request('http://local/a_prod/storage/v1'+path))).status).toBe(401);
 expect((await handler(new Request('http://local/a_prod/storage/v1/object/public/bucket/file',{headers:{apikey:'wrong'}}))).status).toBe(401);
 expect(forwarded).toBe(4);
});

test('a browser on any origin can call the API, and the key still decides what it may do',async()=>{
 const {handler,calls}=setup();
 const origin={origin:'https://app.example.com'};
 const preflight=await handler(new Request('http://local/a_prod/rest/v1/items',{method:'OPTIONS',
  headers:{...origin,'access-control-request-method':'POST','access-control-request-headers':'apikey, authorization, content-type, x-my-app'}}));
 expect(preflight.status).toBe(204);
 expect(preflight.headers.get('access-control-allow-origin')).toBe('*');
 expect(preflight.headers.get('access-control-allow-methods')).toContain('PATCH');
 expect(preflight.headers.get('access-control-allow-headers')).toBe('apikey, authorization, content-type, x-my-app');
 expect(preflight.headers.get('access-control-allow-credentials')).toBeNull();
 // A preflight reaches no upstream and needs no key.
 expect(calls).toHaveLength(0);
 const odd=await handler(new Request('http://local/a_prod/rest/v1/items',{method:'OPTIONS',headers:{...origin,'access-control-request-headers':'bad header; x=y'}}));
 expect(odd.headers.get('access-control-allow-headers')).toContain('x-client-info');
 // Refusals are readable by the page, so supabase-js reports the real status.
 const refused=await handler(new Request('http://local/a_prod/rest/v1/items',{headers:origin}));
 expect(refused.status).toBe(401);
 expect(refused.headers.get('access-control-allow-origin')).toBe('*');
 // Unknown environments answer 404 to a preflight too.
 expect((await handler(new Request('http://local/unknown/rest/v1/items',{method:'OPTIONS',headers:origin}))).status).toBe(404);
});

test('upstream answers carry the gateway browser headers, not the upstream ones',async()=>{
 const upstream=(async()=>new Response('[1]',{status:206,headers:{'content-range':'0-0/5','access-control-allow-origin':'https://evil.example',
  'access-control-allow-credentials':'true',vary:'Accept-Encoding'}})) as unknown as typeof fetch;
 const handler=createGateway(new Map([['a_prod',route]]),upstream);
 const response=await handler(new Request('http://local/a_prod/rest/v1/items',{headers:{apikey:'key-a',origin:'https://app.example.com'}}));
 expect(response.status).toBe(206);
 expect(await response.text()).toBe('[1]');
 expect(response.headers.get('access-control-allow-origin')).toBe('*');
 expect(response.headers.get('access-control-allow-credentials')).toBeNull();
 expect(response.headers.get('access-control-expose-headers')).toContain('content-range');
 expect(response.headers.get('content-range')).toBe('0-0/5');
 expect(response.headers.get('vary')).toBe('Accept-Encoding, Origin');
});

test('a browser following an email link or an OAuth redirect reaches Auth without a key, and only there',async()=>{
 const {handler,calls}=setup();
 for(const [method,path] of [['GET','/a_prod/auth/v1/verify?token=t&type=signup'],['GET','/a_prod/auth/v1/authorize?provider=github'],
  ['GET','/a_prod/auth/v1/callback?code=c&state=s'],['POST','/a_prod/auth/v1/callback']] as const)
  expect((await handler(new Request('http://local'+path,{method,body:method==='POST'?'code=c':undefined}))).status).toBe(200);
 expect(calls).toHaveLength(4);
 expect(calls[0]!.url).toBe('http://auth:9999/verify?token=t&type=signup');
 // The anonymous token stands in for the missing key, as it does for a keyed call.
 expect(new Headers(calls[1]!.options.headers).get('authorization')).toBe('Bearer anon-a');
 for(const [method,path] of [['POST','/a_prod/auth/v1/verify'],['GET','/a_prod/auth/v1/user'],['GET','/a_prod/auth/v1/admin/users'],
  ['GET','/a_prod/rest/v1/authorize'],['DELETE','/a_prod/auth/v1/callback']] as const)
  expect((await handler(new Request('http://local'+path,{method}))).status).toBe(401);
 expect(calls).toHaveLength(4);
});
test('a Storage upload streams through up to the upload limit; other bodies stay small',async()=>{
 const storageRoute={...route,storage:{url:'http://shared-storage:5000',tenantHost:'a_prod.storage.internal'}};
 let received=0,streamed=false;
 const transport=(async(_url:URL,init:RequestInit)=>{
  streamed=init.body instanceof ReadableStream;
  if(streamed){const reader=(init.body as ReadableStream<Uint8Array>).getReader();
   while(true){const item=await reader.read();if(item.done)break;received+=item.value.byteLength;}}
  return Response.json({Key:'ok'});
 }) as unknown as typeof fetch;
 const handler=createGateway(new Map([['a_prod',storageRoute]]),transport,undefined,10_000,undefined,4*1024*1024);
 const chunked=(bytes:number)=>new ReadableStream<Uint8Array>({start(controller){
  for(let sent=0;sent<bytes;sent+=256*1024)controller.enqueue(new Uint8Array(Math.min(256*1024,bytes-sent)));controller.close();}});
 const upload=(bytes:number,headers:Record<string,string>={})=>handler(new Request('http://local/a_prod/storage/v1/object/photos/a.jpg',
  {method:'POST',headers:{apikey:'key-a','content-type':'image/jpeg',...headers},body:chunked(bytes),duplex:'half'} as RequestInit));
 const ok=await upload(3*1024*1024);
 expect(ok.status).toBe(200);expect(streamed).toBe(true);expect(received).toBe(3*1024*1024);
 // Declared too large: refused before anything is sent. Undeclared: refused once the stream passes the limit.
 received=0;
 expect((await upload(1024,{'content-length':String(5*1024*1024)})).status).toBe(413);
 expect(received).toBe(0);
 expect((await upload(5*1024*1024)).status).toBe(413);
 expect(received).toBeLessThanOrEqual(4*1024*1024);
 const rest=await handler(new Request('http://local/a_prod/rest/v1/items',{method:'POST',headers:{apikey:'key-a'},body:chunked(2*1024*1024),duplex:'half'} as RequestInit));
 expect(rest.status).toBe(413);
});
test('the upload limit comes from SBARBASE_UPLOAD_LIMIT_MB',async()=>{
 const {uploadLimit}=await import('../src/gateway/handler');
 expect(uploadLimit(undefined)).toBe(50*1024*1024);
 expect(uploadLimit('')).toBe(50*1024*1024);
 expect(uploadLimit('200')).toBe(200*1024*1024);
 for(const bad of ['0','5121','1.5','ten','-1'])expect(()=>uploadLimit(bad)).toThrow();
});
