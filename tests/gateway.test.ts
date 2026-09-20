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
