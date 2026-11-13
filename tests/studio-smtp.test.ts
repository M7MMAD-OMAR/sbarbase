import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,readFileSync,statSync,writeFileSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {studioSmtpConfig} from '../src/control/studio-smtp';
import {studioProxy,studioHost,signStudio,STUDIO_COOKIE} from '../src/control/studio';
import {STUDIO_SMTP_ASSET_VERSION} from '../src/control/studio-smtp-assets';

const input={SMTP_ADMIN_EMAIL:'noreply@example.com',SMTP_SENDER_NAME:'Example',SMTP_HOST:'smtp.example.com',SMTP_PORT:'587',SMTP_USER:'apikey',SMTP_PASS:'smtp-private-password',SMTP_MAX_FREQUENCY:60,RATE_LIMIT_EMAIL_SENT:30};
function fixture(){
 const directory=mkdtempSync(join(tmpdir(),'studio-smtp-')),catalog=new Catalog(':memory:');
 const org=catalog.createOrganization('alice','A'),project=catalog.createProject('alice',org,'P');
 const environment=catalog.createEnvironment('alice',project,'production'),other=catalog.createEnvironment('alice',project,'other');
 for(let j=catalog.claimProvision();j;j=catalog.claimProvision())catalog.finishProvision(j.environment,j.claim!,true);
 catalog.setMember('alice',org,'viewer','viewer');
 catalog.setMember('alice',org,'bob','owner');
 const runtime=catalog.getProvision('alice',environment).runtime,neighbor=catalog.getProvision('alice',other).runtime;
 const handler=studioSmtpConfig(catalog,directory,directory,0),key=Buffer.alloc(32,16);
 let calls=0,epoch=0;
 const proxy=studioProxy({key:()=>key,epoch:()=>epoch,allowed:(actor,value)=>catalog.studioAllowed(actor,value),upstream:()=>undefined,
  authConfig:handler,transport:(async()=>{calls++;return new Response('unreachable');}) as unknown as typeof fetch});
 const origin='http://'+studioHost(runtime)+':8787';
 const cookie=STUDIO_COOKIE+'='+signStudio(key,{runtime,actor:'alice',epoch:0,kind:'session',expires:Date.now()+60000});
 const request=(method='GET',body?:unknown,headers:Record<string,string>={})=>new Request(origin+'/api/platform/auth/default/config',{method,headers:{cookie,origin,'content-type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)})});
 return {catalog,directory,runtime,neighbor,handler,proxy,origin,request,environment,other,get calls(){return calls;},revokeEpoch(){epoch++;},close(){catalog.close();rmSync(directory,{recursive:true,force:true});}};
}
test('native SMTP loads, saves privately, redacts secrets and retains a blank password',async()=>{
 const f=fixture();try{
  const initial=await f.proxy(f.request());expect(initial.status).toBe(200);expect((await initial.json()).SMTP_PASS).toBeNull();
  const saved=await f.proxy(f.request('PATCH',input));expect(saved.status).toBe(202);const text=await saved.text();expect(text).not.toContain(input.SMTP_PASS);expect(JSON.parse(text).SMTP_PASS).toBeTruthy();
  expect(statSync(join(f.directory,f.runtime+'-mail.json')).mode&0o077).toBe(0);
  const edit=await f.proxy(f.request('PATCH',{SMTP_SENDER_NAME:'Changed',SMTP_PASS:''}));expect(edit.status).toBe(202);
  expect(JSON.parse(readFileSync(join(f.directory,f.runtime+'-mail.json'),'utf8')).pass).toBe(input.SMTP_PASS);
  const loaded=await f.proxy(f.request());expect((await loaded.json()).SMTP_SENDER_NAME).toBe('Changed');expect(f.calls).toBe(0);
 }finally{f.close();}
});
test('native disable removes only SMTP and keeps confirmation, templates and neighbor settings',async()=>{
 const f=fixture();try{
  await f.proxy(f.request('PATCH',input));const revision=f.catalog.signIn('alice',f.other).revision;
  const template=JSON.stringify({version:1,token:'existing-template-token',templates:{confirmation:{subject:'Keep this',body:'Existing custom template'}}});
  writeFileSync(join(f.directory,f.runtime+'-templates.json'),template,{mode:0o600});
  const disabled=await f.proxy(f.request('PATCH',Object.fromEntries(Object.keys(input).filter(k=>k.startsWith('SMTP_')&&k!=='SMTP_MAX_FREQUENCY').map(k=>[k,null]))));
  expect(disabled.status).toBe(202);const data=await disabled.json();expect(data.SMTP_PASS).toBeNull();expect(data.MAILER_AUTOCONFIRM).toBe(false);
  expect(f.catalog.signIn('alice',f.other).revision).toBe(revision);
  expect(readFileSync(join(f.directory,f.runtime+'-templates.json'),'utf8')).toBe(template);
  expect((await f.handler('alice',f.neighbor,f.request())).status).toBe(200);expect((await (await f.handler('alice',f.neighbor,f.request())).json()).SMTP_PASS).toBeNull();
 }finally{f.close();}
});
test('native adapter rejects unsupported fields, mixed nulls, bad values and other project refs',async()=>{
 const f=fixture();try{
  for(const bad of [{...input,SMTP_HOST:'127.0.0.1'},{...input,SMTP_PORT:'587x'},{...input,SMTP_PASS:null},{...input,EXTERNAL_GOOGLE_ENABLED:true},{...input,SMTP_ADMIN_EMAIL:null},[],null])
   expect((await f.proxy(f.request('PATCH',bad))).status).toBe(400);
  expect((await f.handler('alice',f.runtime,new Request(f.origin+'/api/platform/auth/other/config'))).status).toBe(404);
  expect((await f.proxy(f.request('POST',{}))).status).toBe(405);
  expect(f.catalog.signIn('alice',f.environment).revision).toBe(0);
 }finally{f.close();}
});
test('native SMTP writes retain Studio CSRF, session, method, request bounds and live membership checks',async()=>{
 const f=fixture();try{
  expect((await f.proxy(f.request('PATCH',input,{origin:'https://evil.example'}))).status).toBe(403);
  expect((await f.proxy(f.request('PATCH',input,{cookie:''}))).status).toBe(401);
  expect((await f.proxy(f.request('PATCH',input,{'content-type':'text/plain'}))).status).toBe(415);
  expect((await f.proxy(f.request('PATCH',{...input,SMTP_PASS:'x'.repeat(20000)}))).status).toBe(413);
  expect((await f.handler('viewer',f.runtime,f.request())).status).toBe(403);
  f.catalog.setMember('alice',f.catalog.studioNavigation('alice',f.runtime).current.organization.id,'alice','viewer');
  expect((await f.proxy(f.request())).status).toBe(401);
 }finally{f.close();}
});

test('returning native browsers refresh old immutable SMTP assets once without clearing storage',async()=>{
 const f=fixture();try{
  const key=Buffer.alloc(32,16),proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,runtime)=>f.catalog.studioAllowed(actor,runtime),
   navigation:(...args)=>f.catalog.studioNavigation(...args),authConfig:f.handler,upstream:()=> 'http://vendor.invalid',
   transport:(async()=>new Response('<html><head></head><body>Native Studio</body></html>',{headers:{'content-type':'text/html'}})) as unknown as typeof fetch});
  const cookie=f.request().headers.get('cookie')!;
  const load=(extra='')=>proxy(new Request(f.origin+'/project/default/auth/smtp',{headers:{cookie:cookie+extra,accept:'text/html'}}));
  const first=await load();expect(first.status).toBe(200);expect(first.headers.get('clear-site-data')).toBe('"cache"');
  expect(first.headers.getSetCookie().join(';')).toContain(STUDIO_COOKIE+'_smtp_assets='+STUDIO_SMTP_ASSET_VERSION);
  const next=await load('; '+STUDIO_COOKIE+'_smtp_assets='+STUDIO_SMTP_ASSET_VERSION);expect(next.headers.get('clear-site-data')).toBeNull();
 }finally{f.close();}
});

test('an epoch revoked while decoding a native save cannot publish settings',async()=>{
 const f=fixture();try{
  let authorized=true;const request=f.request('PATCH',input);
  request.json=async()=>{authorized=false;return input;};
  expect((await f.handler('alice',f.runtime,request,()=>authorized)).status).toBe(403);
  expect(f.catalog.signIn('alice',f.environment).revision).toBe(0);
 }finally{f.close();}
});

test('updating a shared limit while SMTP is disabled does not enable it',async()=>{
 const f=fixture();try{
  const saved=await f.proxy(f.request('PATCH',{RATE_LIMIT_EMAIL_SENT:45}));expect(saved.status).toBe(202);
  const view=await saved.json();expect(view.SMTP_PASS).toBeNull();expect(view.RATE_LIMIT_EMAIL_SENT).toBe(45);
 }finally{f.close();}
});

test('network gzip HTML retains its document through identity rewriting and guarded publication',async()=>{
 const f=fixture(),key=Buffer.alloc(32,16);
 const upstream=Bun.serve({hostname:'127.0.0.1',port:0,fetch:()=>new Response(Bun.gzipSync('<html><head></head><body>Native SMTP form</body></html>'),{headers:{'content-type':'text/html','content-encoding':'gzip'}})});
 try{
  const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,runtime)=>f.catalog.studioAllowed(actor,runtime),
   navigation:(...args)=>f.catalog.studioNavigation(...args),authConfig:f.handler,upstream:()=>upstream.url.origin});
  const response=await proxy(new Request(f.origin+'/project/default/auth/smtp',{headers:{cookie:f.request().headers.get('cookie')!,accept:'text/html'}}));
  const body=await response.text();expect(body).toContain('Native SMTP form');expect(body).toContain('/__sbarbase/identity.js');
  expect(response.headers.has('content-encoding')).toBe(false);
 }finally{upstream.stop(true);f.close();}
});

test('native SMTP asset publication bypasses stale conditionals and validates GET bytes for HEAD',async()=>{
 const f=fixture(),key=Buffer.alloc(32,16);let sent:RequestInit|undefined;
 const source=readFileSync(new URL('./fixtures/studio-smtp/08dh2y9gjjf0f.module.txt',import.meta.url),'utf8');
 try{
  const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,runtime)=>f.catalog.studioAllowed(actor,runtime),authConfig:f.handler,upstream:()=> 'http://vendor.invalid',
   transport:(async(_url,init)=>{sent=init;return new Response(source,{headers:{'content-type':'application/javascript','etag':'old','content-encoding':'gzip'}});}) as typeof fetch});
  const response=await proxy(new Request(f.origin+'/_next/static/chunks/08dh2y9gjjf0f.js',{method:'HEAD',headers:{cookie:f.request().headers.get('cookie')!,'if-none-match':'old','if-modified-since':'yesterday',range:'bytes=0-10','if-range':'old'}}));
  expect(response.status).toBe(200);expect(await response.text()).toBe('');expect(sent?.method).toBe('GET');
  const headers=new Headers(sent?.headers);for(const name of ['range','if-range','if-none-match','if-modified-since'])expect(headers.has(name)).toBe(false);
  expect((sent as RequestInit&{decompress?:boolean}).decompress).toBe(true);
  expect(response.headers.get('cache-control')).toBe('no-store');expect(response.headers.has('etag')).toBe(false);expect(response.headers.has('content-encoding')).toBe(false);
 }finally{f.close();}
});

test('native revocation remains bound inside the reused settings handler before its writes',async()=>{
 const f=fixture(),original=Request.prototype.json;let authorized=true;
 Request.prototype.json=async function(){const value=await original.call(this);if(this.url.startsWith('http://management.internal/')&&this.method==='PUT')authorized=false;return value;};
 try{
  const response=await f.handler('alice',f.runtime,f.request('PATCH',input),()=>authorized);
  expect(response.status).toBe(403);expect(existsSync(join(f.directory,f.runtime+'-mail.json'))).toBe(false);
  expect(f.catalog.signIn('alice',f.environment).revision).toBe(0);
 }finally{Request.prototype.json=original;f.close();}
});

test('the actual Studio proxy binds session epoch across the internal PUT body await',async()=>{
 const f=fixture(),original=Request.prototype.json;
 Request.prototype.json=async function(){const value=await original.call(this);if(this.url.startsWith('http://management.internal/')&&this.method==='PUT')f.revokeEpoch();return value;};
 try{
  const response=await f.proxy(f.request('PATCH',input));expect(response.status).toBe(403);
  expect(existsSync(join(f.directory,f.runtime+'-mail.json'))).toBe(false);expect(f.catalog.signIn('alice',f.environment).revision).toBe(0);
 }finally{Request.prototype.json=original;f.close();}
});
