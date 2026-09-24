import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,existsSync,rmSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {deploy,deployment,remove,readManifest,changeSecrets,functionsHandler,DeployError} from '../src/control/functions';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import {Catalog} from '../src/control/catalog';

const E='e_'+'a'.repeat(24);
const temporary=()=>mkdtempSync(join(tmpdir(),'functions-'));

test('a deploy needs an entry point and safe relative paths',()=>{
 expect(deployment({files:{'index.ts':'Deno.serve(()=>new Response("hi"))'}})).toMatchObject({verify_jwt:true});
 expect(deployment({files:{'index.ts':'x','lib/util.ts':'y'},shared:{'cors.ts':'z'},verify_jwt:false}).verify_jwt).toBe(false);
 for(const bad of [{files:{'util.ts':'x'}},{files:{'index.ts':'x','../escape.ts':'y'}},{files:{'index.ts':'x','/abs.ts':'y'}},
  {files:{'index.ts':'x','a/../../b.ts':'y'}},{files:{'index.ts':1}},{files:{'index.ts':'x'},verify_jwt:'no'},{files:{'index.ts':'x'},other:1},
  {files:{'index.ts':'x'},shared:{'../x.ts':'y'}},null,[]])
  expect(()=>deployment(bad)).toThrow(DeployError);
});

test('a deploy writes a new version with the shared files beside it and switches the manifest',()=>{
 const root=temporary();
 try {
  const first=deploy(root,E,'hello',deployment({files:{'index.ts':'one'},shared:{'cors.ts':'c'}}),1_000);
  expect(readFileSync(join(root,E,first.version,'hello','index.ts'),'utf8')).toBe('one');
  expect(readFileSync(join(root,E,first.version,'_shared','cors.ts'),'utf8')).toBe('c');
  const second=deploy(root,E,'hello',deployment({files:{'index.ts':'two'},verify_jwt:false}),2_000);
  expect(second.version).not.toBe(first.version);
  expect(readManifest(root,E).functions.hello).toMatchObject({version:second.version,verify_jwt:false});
  // The previous version stays for requests already running on it.
  expect(existsSync(join(root,E,first.version))).toBe(true);
  expect(remove(root,E,'hello')).toBe(true);
  expect(remove(root,E,'hello')).toBe(false);
  expect(readManifest(root,E).functions).toEqual({});
  expect(()=>deploy(root,'../x','hello',deployment({files:{'index.ts':'x'}}))).toThrow();
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('secrets are private, never named SUPABASE_, and removed with null',()=>{
 const root=temporary();
 try {
  expect(changeSecrets(root,E,{secrets:{STRIPE_KEY:'sk_test',OTHER:'1'}})).toEqual(['OTHER','STRIPE_KEY']);
  expect(changeSecrets(root,E,{secrets:{OTHER:null}})).toEqual(['STRIPE_KEY']);
  const path=join(root,E,'secrets.json');
  expect(JSON.parse(readFileSync(path,'utf8'))).toEqual({STRIPE_KEY:'sk_test'});
  expect(statSync(path).mode&0o077).toBe(0);
  for(const bad of [{secrets:{SUPABASE_URL:'x'}},{secrets:{lower:'x'}},{secrets:{SB_JWT_SECRET:'x'}},{secrets:{A:1}},{secrets:[]},{},{secrets:{},x:1}])
   expect(()=>changeSecrets(root,E,bad)).toThrow(DeployError);
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('owners and admins deploy; the first deploy turns Edge Functions on; values of secrets never come back',async()=>{
 const catalog=new Catalog(':memory:'),code=temporary(),secrets=temporary();
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets);
  const call=(actor:string,path:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice','functions')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol','functions')).status).toBe(403);
  const empty=(await (await call('alice','functions')).json()) as any;
  expect(empty.data).toMatchObject({state:'off',functions:[],secrets:[]});
  const deployed=await call('alice','functions/hello-world','PUT',{files:{'index.ts':'Deno.serve(()=>new Response("hi"))'}});
  expect(deployed.status).toBe(201);
  const body=(await deployed.json()) as any;
  expect(body.data).toMatchObject({desired:'on',state:'pending'});
  expect(body.data.functions[0]).toMatchObject({name:'hello-world',verify_jwt:true});
  expect(body.data.functions[0].path).toMatch(/^\/e_[a-f0-9]{24}\/functions\/v1\/hello-world$/);
  expect((await call('alice','functions/_internal','PUT',{files:{'index.ts':'x'}})).status).toBe(400);
  expect((await call('alice','functions/bad','PUT',{files:{'main.ts':'x'}})).status).toBe(400);
  expect((await call('carol','functions/x','PUT',{files:{'index.ts':'x'}})).status).toBe(403);
  const saved=await call('alice','function-secrets','PUT',{secrets:{STRIPE_KEY:'sk_live_value'}});
  expect(saved.status).toBe(200);
  const listed=await (await call('alice','functions')).text();
  expect(listed).toContain('STRIPE_KEY');expect(listed).not.toContain('sk_live_value');
  expect((await call('alice','functions/hello-world','DELETE')).status).toBe(200);
  expect((await call('alice','functions/hello-world','DELETE')).status).toBe(404);
  expect((await call('alice','functions','POST')).status).toBe(405);
 } finally {catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}
});

test('the gateway runs functions with or without a key, and never reaches the runtime\'s own routes',async()=>{
 const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',keys:[],anonymousToken:'anon-token',enabled:true,
  functions:{url:'http://10.0.0.7:9000'}};
 const calls:{url:string;headers:Headers}[]=[];
 const handler=createGateway(new Map([[E,route]]),(async(url:URL,init:RequestInit)=>{calls.push({url:String(url),headers:new Headers(init.headers)});
  return new Response('ran');}) as unknown as typeof fetch,(runtime,key)=>key==='sb_publishable_ok');
 const call=(path:string,headers:Record<string,string>={},method='POST')=>handler(new Request(`http://local/${E}/functions/v1${path}`,{method,headers,body:method==='GET'?undefined:'{}'}));
 // With the key: the anonymous token stands in, as for every other service.
 expect((await call('/hello?x=1',{apikey:'sb_publishable_ok','stripe-signature':'t=1,v1=abc',cookie:'a=b','x-forwarded-for':'1.2.3.4'})).status).toBe(200);
 expect(calls[0]!.url).toBe('http://10.0.0.7:9000/hello?x=1');
 expect(calls[0]!.headers.get('authorization')).toBe('Bearer anon-token');
 expect(calls[0]!.headers.get('stripe-signature')).toBe('t=1,v1=abc');
 expect(calls[0]!.headers.has('x-forwarded-for')).toBe(false);
 // A webhook without a key: no token is added, so a function that verifies JWTs refuses it.
 expect((await call('/hello/sub/path',{'stripe-signature':'s'})).status).toBe(200);
 expect(calls[1]!.url).toBe('http://10.0.0.7:9000/hello/sub/path');
 expect(calls[1]!.headers.has('authorization')).toBe(false);
 expect((await call('/hello',{authorization:'Bearer user.jwt.here'})).status).toBe(200);
 expect(calls[2]!.headers.get('authorization')).toBe('Bearer user.jwt.here');
 // A wrong key is still refused, and the runtime's own routes are never reachable.
 expect((await call('/hello',{apikey:'sb_publishable_wrong'})).status).toBe(401);
 for(const path of ['/_sb/rest/v1/items','/','/-bad','/'+'a'.repeat(65)])
  expect((await call(path,{apikey:'sb_publishable_ok'})).status).toBe(404);
 expect(calls).toHaveLength(3);
 const off=createGateway(new Map([[E,{...route,functions:undefined}]]),(async()=>new Response('no')) as unknown as typeof fetch,()=>true);
 expect((await off(new Request(`http://local/${E}/functions/v1/hello`,{headers:{apikey:'k'}}))).status).toBe(404);
});
