import {expect,test} from 'bun:test';
import {createHmac,timingSafeEqual} from 'node:crypto';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';

const jwt=(aud:unknown,extra:Record<string,unknown>={})=>[
 Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url'),
 Buffer.from(JSON.stringify({aud,role:'storage_runtime',exp:2000000000,...extra})).toString('base64url'),
 'fixture_signature_verified_by_upstream_only',
].join('.');
const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',storage:{url:'http://storage:5000',tenantHost:'environment.storage.internal'},
 realtime:{url:'http://realtime:4000',tenantHost:'aaaaaaaaaaaaaaaaaaaaaaaa.realtime'},realtimeToken:'realtime-anon',functions:{url:'http://functions:9000'},
 keys:['publishable'],anonymousToken:'anon-token',serviceToken:'service-role',enabled:true};
function fixture() {
 const calls:{url:string;headers:Headers}[]=[];
 const handler=createGateway(new Map([['environment',route]]),(async(url,init)=>{
  calls.push({url:String(url),headers:new Headers(init?.headers)});return new Response('upstream');
 }) as typeof fetch,(_environment,key)=>key==='publishable'?'publishable':key==='secret'?'secret':null);
 const request=(path:string,token:string,apiKey:string|null='publishable',method='GET')=>handler(new Request(`http://local/environment/${path}`,{
  method,headers:{authorization:`Bearer ${token}`,...(apiKey===null?{}:{apikey:apiKey})},
 }));
 return {calls,request,handler};
}
for(const aud of ['sbarbase:storage',['sbarbase:storage']]) test(`reserved audience ${JSON.stringify(aud)} never reaches another service`,async()=>{
 const {calls,request}=fixture();
 for(const [path,method] of [['auth/v1/user','GET'],['auth/v1/admin/users','GET'],['rest/v1/private_table','GET'],['rest/v1/rpc/private_function','POST'],
  ['realtime/v1/api/broadcast','POST'],['functions/v1/webhook','POST'],['auth/v1/verify?token=public','GET'],['auth/v1/callback','POST']]){
  expect((await request(path!,jwt(aud), 'publishable',method)).status).toBe(403);
 }
 expect(calls).toHaveLength(0);
});
test('valid Storage admission keeps custom Bearer and fixed tenant, but does not authenticate its signature',async()=>{
 const {calls,request}=fixture(),token=jwt('sbarbase:storage');
 expect((await request('storage/v1/object/authenticated/private/object',token)).status).toBe(200);
 expect(calls).toHaveLength(1);
 expect(calls[0]!.headers.get('authorization')).toBe(`Bearer ${token}`);
 expect(calls[0]!.headers.get('x-forwarded-host')).toBe('environment.storage.internal');
 expect(calls[0]!.url).toBe('http://storage:5000/object/authenticated/private/object');
});
test('scoped Storage requires verified distinct publishable key even for public or signed reads',async()=>{
 const {calls,request}=fixture(),token=jwt('sbarbase:storage');
 for(const path of ['storage/v1/object/authenticated/private/object','storage/v1/object/public/private/object','storage/v1/object/sign/private/object?token=signed']){
  for(const key of [null,'wrong','secret'])expect((await request(path,token,key)).status).toBe(401);
 }
 expect(calls).toHaveLength(0);
 const acceptsAny=createGateway(new Map([['environment',route]]),(async()=>{throw new Error('must not reach upstream');}) as unknown as typeof fetch,()=>true);
 expect((await acceptsAny(new Request('http://local/environment/storage/v1/object/public/private/object',{headers:{apikey:token,authorization:`Bearer ${token}`}}))).status).toBe(401);
 expect((await acceptsAny(new Request('http://local/environment/rest/v1/table',{headers:{apikey:token,authorization:`Bearer ${token}`}}))).status).toBe(403);
});
test('scoped tokens cannot take keyless function or browser Auth exceptions',async()=>{
 const {calls,request}=fixture(),token=jwt('sbarbase:storage');
 for(const [path,method] of [['functions/v1/webhook','POST'],['auth/v1/verify?token=public','GET'],['auth/v1/authorize?provider=google','GET'],['auth/v1/callback','POST']]){
  expect((await request(path!,token,null,method)).status).toBe(403);
 }
 expect(calls).toHaveLength(0);
});
test('malformed scoped JWTs and conflicting reserved audiences fail closed before dispatch',async()=>{
 const {calls,request}=fixture(),valid=jwt('sbarbase:storage'),parts=valid.split('.');
 const malformed=[jwt(['sbarbase:storage','authenticated']),jwt(['sbarbase:storage','sbarbase:storage']),`${parts[0]}.${parts[1]}=.${parts[2]}`,
  `${parts[0]}.${Buffer.from('{"aud":"sbarbase:storage"').toString('base64url')}.${parts[2]}`,`${parts[0]}.${'e'.repeat(4097)}.${parts[2]}`,
  `${parts[0]}.${parts[1]}.`,`${parts[0]}.${parts[1]}`,`${valid}.extra`];
 for(const token of malformed)for(const path of ['storage/v1/object/public/private/object','functions/v1/webhook','rest/v1/table']){
  expect((await request(path,token,null)).status).toBe(401);
 }
 expect(calls).toHaveLength(0);
});
test('ordinary user JWTs and API-key bearer substitutions keep existing behavior',async()=>{
 const {calls,request}=fixture();
 for(const token of [jwt('authenticated'),jwt(['authenticated']),jwt(undefined), 'opaque-user-token', 'user.jwt.token']){
  expect((await request('rest/v1/table',token)).status).toBe(200);
  expect(calls.at(-1)!.headers.get('authorization')).toBe(`Bearer ${token}`);
 }
 expect((await request('rest/v1/table','publishable')).status).toBe(200);
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer anon-token');
 expect((await request('functions/v1/webhook','opaque-user-token',null,'POST')).status).toBe(200);
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer opaque-user-token');
});
test('a changed audience never grants authentication or skips upstream signature verification',async()=>{
 let signatureChecks=0;
 const secret='isolated-fixture-signing-secret';
 const signed=jwt('sbarbase:storage').split('.').slice(0,2).join('.');
 const signature=createHmac('sha256',secret).update(signed).digest('base64url');
 const changed=[signed.split('.')[0],Buffer.from(JSON.stringify({aud:'authenticated',role:'storage_runtime',exp:2000000000})).toString('base64url'),signature].join('.');
 const handler=createGateway(new Map([['environment',route]]),(async(_url,init)=>{
  signatureChecks++;
  const token=new Headers(init?.headers).get('authorization')!.slice(7),parts=token.split('.');
  const actual=Buffer.from(parts[2]!,'base64url'),expected=createHmac('sha256',secret).update(parts.slice(0,2).join('.')).digest();
  return new Response(null,{status:actual.length===expected.length&&timingSafeEqual(actual,expected)?200:401});
 }) as typeof fetch);
 expect((await handler(new Request('http://local/environment/rest/v1/table',{headers:{apikey:'publishable',authorization:`Bearer ${changed}`}}))).status).toBe(401);
 expect(signatureChecks).toBe(1);
});

// This HTTP gateway does not inspect Realtime WebSocket frames. An installation
// must keep Realtime disabled for scoped runtime credentials until the native
// Realtime JWT audience policy is independently enforced and tested.
test('current Storage-only installation has no HTTP or socket entry to inactive services',async()=>{
 let calls=0;
 const handler=createGateway(new Map([['environment',{...route,realtime:undefined,functions:undefined}]]),(async()=>{calls++;return new Response('wrong');}) as unknown as typeof fetch);
 for(const path of ['realtime/v1/api/broadcast','realtime/v1/websocket?apikey=publishable','functions/v1/webhook']){
  const response=await handler(new Request('http://local/environment/'+path,{method:path.includes('websocket')?'GET':'POST',headers:{apikey:'publishable',authorization:`Bearer ${jwt('sbarbase:storage')}`}}));
  expect([403,404]).toContain(response.status);
 }
 expect(calls).toBe(0);
});
