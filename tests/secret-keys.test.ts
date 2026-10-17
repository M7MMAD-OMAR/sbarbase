import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {keyHandler} from '../src/control/key-http';
import {managedGateway} from '../src/gateway/managed';
import {createGateway,looksLikeBrowser,type EnvironmentRoute} from '../src/gateway/handler';
import {realtimeUpgrade} from '../src/gateway/realtime';
import {serviceToken} from '../lab/upstream-app';
const nativeFetch=globalThis.fetch;

const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',storage:{url:'http://storage:5000',tenantHost:'t.storage'},
 functions:{url:'http://functions:9000'},keys:[],anonymousToken:'anon-token',serviceToken:'service-token',enabled:true};

function gateway(entry:EnvironmentRoute=route) {
 const calls:{url:string;headers:Headers}[]=[];
 const handler=createGateway(new Map([['e_prod',entry]]),(async(url,options)=>{calls.push({url:String(url),headers:new Headers(options!.headers)});return Response.json([]);}) as typeof fetch,
  (_environment,key)=>key==='sb_secret_s'?'secret':key==='sb_publishable_p'?'publishable':null);
 return {handler,calls};
}
const SERVER={'user-agent':'node'};

test('a secret key reaches every service as service_role, a publishable key as anon',async()=>{
 const {handler,calls}=gateway();
 for(const service of ['rest/v1/items','auth/v1/admin/users','storage/v1/bucket','functions/v1/hello']) {
  expect((await handler(new Request(`http://local/e_prod/${service}`,{headers:{...SERVER,apikey:'sb_secret_s'}}))).status).toBe(200);
  expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer service-token');
 }
 await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{apikey:'sb_publishable_p'}}));
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer anon-token');
});

test('the key repeated as the bearer is swapped; a user token wins over a secret key, as on Supabase',async()=>{
 const {handler,calls}=gateway();
 await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{...SERVER,apikey:'sb_secret_s',authorization:'Bearer sb_secret_s'}}));
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer service-token');
 await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{...SERVER,apikey:'sb_secret_s',authorization:'Bearer user.jwt.token'}}));
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer user.jwt.token');
});

test('a browser never gets through with a secret key, and its publishable key still works',async()=>{
 const {handler,calls}=gateway();
 for(const headers of <Record<string,string>[]>[{'user-agent':'Mozilla/5.0 (X11; Linux x86_64) Chrome/140'},{origin:'https://app.example'},{'sec-fetch-mode':'cors'},{'sec-fetch-site':'cross-site'}]) {
  const response=await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{...headers,apikey:'sb_secret_s'}}));
  expect(response.status).toBe(401);
  expect((await response.json()).message).toContain('cannot be used in a browser');
  // The refusal still reaches the page, so its developer sees why.
  expect(response.headers.get('access-control-allow-origin')).toBe('*');
 }
 expect(calls).toHaveLength(0);
 expect((await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{'user-agent':'Mozilla/5.0',origin:'https://app.example',apikey:'sb_publishable_p'}}))).status).toBe(200);
 expect(looksLikeBrowser(new Headers({'user-agent':'supabase-js-node/2'}))).toBe(false);
});

test('without a service token for the environment a secret key is refused, never sent as anon',async()=>{
 const {handler,calls}=gateway({...route,serviceToken:undefined});
 expect((await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{...SERVER,apikey:'sb_secret_s'}}))).status).toBe(503);
 expect(calls).toHaveLength(0);
});

test('Realtime broadcast over HTTP: the short service token for a secret key, the anon token for a publishable key',async()=>{
 const entry={...route,realtime:{url:'http://realtime:4000',tenantHost:'0123456789abcdef01234567.realtime'},realtimeToken:'rt-anon',realtimeServiceToken:'rt-service'};
 const {handler,calls}=gateway(entry);
 await handler(new Request('http://local/e_prod/realtime/v1/api/broadcast',{method:'POST',headers:{...SERVER,apikey:'sb_secret_s'},body:'{}'}));
 expect(calls.at(-1)!.headers.get('apikey')).toBe('service-token');expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer service-token');
 await handler(new Request('http://local/e_prod/realtime/v1/api/broadcast',{method:'POST',headers:{apikey:'sb_publishable_p'},body:'{}'}));
 expect(calls.at(-1)!.headers.get('apikey')).toBe('rt-anon');
});

test('an Edge Function sees the caller headers, the key included, as on Supabase, with the service token as its bearer',async()=>{
 const {handler,calls}=gateway();
 await handler(new Request('http://local/e_prod/functions/v1/hello',{method:'POST',headers:{...SERVER,apikey:'sb_secret_s','x-signature':'abc'},body:'{}'}));
 expect(calls.at(-1)!.headers.get('apikey')).toBe('sb_secret_s');
 expect(calls.at(-1)!.headers.get('authorization')).toBe('Bearer service-token');
 expect(calls.at(-1)!.headers.get('x-signature')).toBe('abc');
});

test('Realtime: a secret key opens a service_role socket from a server, never from a browser',()=>{
 const realtime={...route,realtime:{url:'http://realtime:4000',tenantHost:'0123456789abcdef01234567.realtime'},realtimeToken:'rt-anon',realtimeServiceToken:'rt-service'};
 const upgrade=realtimeUpgrade({route:()=>realtime,verifyKey:(_runtime,key)=>key==='sb_secret_s'?'secret':key==='sb_publishable_p'?'publishable':null});
 const server=upgrade('/e_prod/realtime/v1/websocket?apikey=sb_secret_s&vsn=1.0.0',new Headers({'user-agent':'node'}));
 expect(server.ok&&server.head).toContain('apikey=rt-service');
 const browser=upgrade('/e_prod/realtime/v1/websocket?apikey=sb_secret_s',new Headers({origin:'https://app.example'}));
 expect(browser.ok).toBe(false);expect(!browser.ok&&browser.status).toBe(401);
 const page=upgrade('/e_prod/realtime/v1/websocket?apikey=sb_publishable_p',new Headers({origin:'https://app.example'}));
 expect(page.ok&&page.head).toContain('apikey=rt-anon');
 expect(upgrade('/e_prod/realtime/v1/websocket?apikey=sb_secret_x',new Headers()).ok).toBe(false);
});

test('service tokens carry service_role and an expiry, are reused, and follow a new signing secret',()=>{
 const now=1_800_000_000;
 const first=serviceToken('secret-one',3600,now);
 const claims=JSON.parse(Buffer.from(first.split('.')[1]!,'base64url').toString());
 expect(claims.role).toBe('service_role');expect(claims.exp).toBe(now+3600);
 expect(serviceToken('secret-one',3600,now+60)).toBe(first);
 expect(serviceToken('secret-one',3600,now+2800)).not.toBe(first);
 expect(serviceToken('secret-two',3600,now)).not.toBe(first);
});

test('the key API issues named secret keys once, audits them, and the managed gateway honours and revokes them',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try {
  const a=catalog.createOrganization('alice','A');
  catalog.setMember('alice',a,'viewer','viewer');
  const e=catalog.createEnvironment('alice',catalog.createProject('alice',a,'P'),'production');
  let job;while((job=catalog.claimProvision())) catalog.finishProvision(job.environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',e).runtime!;
  const handler=keyHandler(catalog,keys,async r=>r.headers.get('authorization'));
  const post=(actor:string,body?:unknown)=>handler(new Request(`http://localhost/management/v1/environments/${e}/keys`,{method:'POST',
   headers:{authorization:actor,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:typeof body==='string'?body:JSON.stringify(body)})}));
  expect((await post('viewer',{type:'secret',name:'backend'})).status).toBe(403);
  for(const bad of [{type:'service_role'},{name:'X'},{name:'abc'},{type:'secret',extra:1},'not json',[1],'{"name":"'+'a'.repeat(5000)+'"}'])expect((await post('alice',bad)).status).toBe(400);
  // A body is read only as JSON, through the bounded reader.
  expect((await handler(new Request(`http://localhost/management/v1/environments/${e}/keys`,{method:'POST',headers:{authorization:'alice','content-type':'text/plain'},body:'{"type":"secret"}'}))).status).toBe(400);
  const issued=await post('alice',{type:'secret',name:'backend'});expect(issued.status).toBe(201);
  const secret=await issued.json();
  expect(secret.token).toMatch(/^sb_secret_[A-Za-z0-9_-]{43}$/);expect(secret).toMatchObject({type:'secret',name:'backend'});
  const plain=await (await post('alice')).json();
  expect(plain.token).toMatch(/^sb_publishable_/);expect(plain.name).toBe('default');
  const listed=await (await handler(new Request(`http://localhost/management/v1/environments/${e}/keys`,{headers:{authorization:'alice'}}))).text();
  expect(listed).not.toContain(secret.token);expect(JSON.parse(listed).data.map((key:any)=>key.kind+':'+key.name).sort()).toEqual(['publishable:default','secret:backend']);
  const audit=JSON.stringify(catalog.auditEvents('alice',a));
  expect(audit).toContain('keys.created');expect(audit).not.toContain(secret.token);
  const calls:string[]=[];
  const gate=managedGateway(catalog,keys,()=>({...route,auth:'http://auth.invalid',rest:'http://rest.invalid'}),
   Object.assign(async(_url:unknown,init:RequestInit)=>{calls.push(new Headers(init.headers).get('authorization')!);return Response.json([]);},{preconnect:nativeFetch.preconnect}) as typeof fetch);
  const call=(key:string,headers:Record<string,string>=SERVER)=>gate(new Request(`http://localhost/${runtime}/rest/v1/items`,{headers:{...headers,apikey:key}}));
  expect((await call(secret.token)).status).toBe(200);expect(calls.at(-1)).toBe('Bearer service-token');
  expect((await call(secret.token,{'user-agent':'Mozilla/5.0'})).status).toBe(401);
  // No service token for the environment: the secret key is refused, never sent as anon.
  const bare=managedGateway(catalog,keys,()=>({...route,serviceToken:undefined,auth:'http://auth.invalid',rest:'http://rest.invalid'}),
   Object.assign(async()=>Response.json([]),{preconnect:nativeFetch.preconnect}) as typeof fetch);
  expect((await bare(new Request(`http://localhost/${runtime}/rest/v1/items`,{headers:{...SERVER,apikey:secret.token}}))).status).toBe(503);
  expect((await handler(new Request(`http://localhost/management/v1/environments/${e}/keys/${secret.id}`,{method:'DELETE',headers:{authorization:'alice'}}))).status).toBe(200);
  expect((await call(secret.token)).status).toBe(401);
  expect(JSON.stringify(catalog.auditEvents('alice',a))).toContain('keys.revoked');
 } finally {catalog.close();keys.close();}
});

test('an older key store gains names; its keys are named default and keep working',()=>{
 const {Database}=require('bun:sqlite');
 const directory=require('node:fs').mkdtempSync(require('node:path').join(require('node:os').tmpdir(),'sbarbase-keys-')),path=directory+'/keys.sqlite';
 const old=new Database(path,{create:true});
 old.exec(`CREATE TABLE api_keys(id TEXT PRIMARY KEY, environment TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('publishable','secret')),
  digest TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, revoked_at INTEGER, runtime_epoch INTEGER NOT NULL DEFAULT 0);`);
 old.close();
 const keys=new KeyStore(path);
 try {
  const issued=keys.issue('e_prod','secret','worker_one');
  expect(keys.resolve('e_prod',issued.token)).toBe('secret');
  expect(keys.list('e_prod')[0]!.name).toBe('worker_one');
  expect(()=>keys.issue('e_prod','secret','Bad Name')).toThrow();
 } finally {keys.close();require('node:fs').rmSync(directory,{recursive:true,force:true});}
});

test('upstreams are asked for uncompressed answers, whatever encoding the caller accepts',async()=>{
 const {handler,calls}=gateway();
 await handler(new Request('http://local/e_prod/functions/v1/hello',{headers:{...SERVER,apikey:'sb_publishable_p','accept-encoding':'gzip, br'}}));
 expect(calls.at(-1)!.headers.get('accept-encoding')).toBe('identity');
 await handler(new Request('http://local/e_prod/rest/v1/items',{headers:{apikey:'sb_publishable_p'}}));
 expect(calls.at(-1)!.headers.get('accept-encoding')).toBe('identity');
});
