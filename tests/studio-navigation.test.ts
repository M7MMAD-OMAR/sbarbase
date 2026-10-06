import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {studioProxy,studioHost,signStudio,verifyStudio,STUDIO_COOKIE} from '../src/control/studio';

const key=Buffer.alloc(32,19);
function fixture(){
 const catalog=new Catalog(':memory:');
 const org=catalog.createOrganization('alice','<img src=x onerror=alert(1)>');
 const project=catalog.createProject('alice',org,'Shop');
 const environment=catalog.createEnvironment('alice',project,'Production');
 const other=catalog.createOrganization('bob','Other');catalog.setMember('bob',other,'alice','admin');
 const otherProject=catalog.createProject('bob',other,'Analytics');
 const target=catalog.createEnvironment('bob',otherProject,'Staging');
 const hidden=catalog.createOrganization('bob','Viewer only');catalog.setMember('bob',hidden,'alice','viewer');
 const empty=catalog.createOrganization('alice','Empty');
 for(let job=catalog.claimProvision();job;job=catalog.claimProvision())catalog.finishProvision(job.environment,job.claim!,true);
 for(const id of [environment,target])catalog.requestStudio('alice',id,'running');
 (catalog as unknown as {db:{query:(sql:string)=>{run:()=>unknown}}}).db.query("UPDATE studio_sessions SET state='running'").run();
 const runtime=catalog.getProvision('alice',environment).runtime,targetRuntime=catalog.getProvision('alice',target).runtime;
 let allow=true,calls=0,selected=0,available=true;
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,value)=>allow&&catalog.studioAllowed(actor,value),
  navigation:(...args)=>catalog.studioNavigation(...args),selection:(actor,id)=>{selected++;return catalog.studio(actor,id);},
  upstream:()=>available?'http://vendor.internal:3000':undefined,
  transport:(async()=>{calls++;return new Response('original vendor',{headers:{'content-type':'text/html','content-security-policy':"default-src 'self'; script-src 'nonce-vendor'; frame-ancestors 'none'",'x-frame-options':'DENY'}});}) as unknown as typeof fetch});
 const origin='http://'+studioHost(runtime)+':8787';
 const cookie=STUDIO_COOKIE+'='+signStudio(key,{runtime,actor:'alice',epoch:0,kind:'session',expires:Date.now()+60_000});
 const request=(path:string,method='GET',body?:string,originHeader:string|null=origin)=>new Request(origin+path,
  {method,headers:{cookie,...(originHeader?{origin:originHeader}:{}),'content-type':'application/json'},...(body===undefined?{}:{body})});
 return {catalog,org,project,environment,other,otherProject,target,hidden,empty,runtime,targetRuntime,proxy,origin,cookie,request,
  get calls(){return calls;},get selected(){return selected;},revoke(){allow=false;},unavailable(){available=false;}};
}

test('Studio navigation uses live ownership, filters viewers and lists only the selected scope',()=>{
 const f=fixture();try{
  const value=f.catalog.studioNavigation('alice',f.runtime);
  expect(value.current.project.name).toBe('Shop');expect(value.selection).toEqual({organization:f.org,project:f.project});
  expect(value.organizations.some(row=>row.id===f.hidden)).toBe(false);
  expect(value.environments).toEqual([{id:f.environment,name:'Production',ready:true,state:'running'}]);
  expect(f.catalog.studioNavigation('alice',f.runtime,f.other).selection.project).toBe(f.otherProject);
  const empty=f.catalog.studioNavigation('alice',f.runtime,f.empty);expect(empty.projects).toEqual([]);expect(empty.environments).toEqual([]);expect(empty.selection.project).toBeNull();
  expect(()=>f.catalog.studioNavigation('alice',f.runtime,f.hidden)).toThrow('Forbidden');
  expect(()=>f.catalog.studioNavigation('alice',f.runtime,f.org,f.otherProject)).toThrow('Forbidden');
 }finally{f.catalog.close();}
});

test('workspace entry, assets and metadata never reach vendor APIs or contain user HTML',async()=>{
 const f=fixture();try{
  const ticket=signStudio(key,{runtime:f.runtime,actor:'alice',epoch:0,kind:'ticket',expires:Date.now()+60_000});
  expect((await f.proxy(new Request(f.origin+'/__sbarbase/enter?ticket='+ticket))).headers.get('location')).toBe('/__sbarbase/workspace');
  const html=await f.proxy(f.request('/__sbarbase/workspace'));expect(html.status).toBe(200);
  expect(await html.text()).not.toContain('<img src=x');expect(html.headers.get('content-security-policy')).toContain("frame-ancestors 'none'");
  for(const path of ['/__sbarbase/workspace.js','/__sbarbase/workspace.css','/__sbarbase/navigation']){
   const response=await f.proxy(f.request(path));expect(response.status).toBe(200);expect(response.headers.get('cache-control')).toBe('no-store');
  }
  expect(f.calls).toBe(0);
  expect((await f.proxy(new Request(f.origin+'/__sbarbase/navigation'))).status).toBe(401);
  f.revoke();const rejected=await f.proxy(f.request('/__sbarbase/navigation'));expect(rejected.status).toBe(401);expect((await rejected.json()).message).toContain('sign in');
 }finally{f.catalog.close();}
});

test('switch creates a fresh target-only ticket without launching or changing Studio',async()=>{
 const f=fixture();try{
  const response=await f.proxy(f.request('/__sbarbase/switch','POST',JSON.stringify({environment:f.target})));
  expect(response.status).toBe(200);const next=new URL((await response.json()).href);
  expect(next.hostname).toBe(studioHost(f.targetRuntime));expect(next.port).toBe('8787');expect(next.pathname).toBe('/__sbarbase/enter');
  const ticket=next.searchParams.get('ticket');expect(verifyStudio(key,ticket,f.targetRuntime,'ticket')?.actor).toBe('alice');
  expect(verifyStudio(key,ticket,f.runtime,'ticket')).toBeNull();expect(verifyStudio(key,ticket,f.targetRuntime,'session')).toBeNull();
  expect(f.catalog.studio('alice',f.target).state).toBe('running');expect(f.calls).toBe(0);
 }finally{f.catalog.close();}
});

test('switch rejects sibling-origin CSRF, malformed input, excessive bodies and stopped targets',async()=>{
 const f=fixture();try{
  const body=JSON.stringify({environment:f.target});
  for(const origin of [null,'null','http://'+studioHost(f.targetRuntime)+':8787'])expect((await f.proxy(f.request('/__sbarbase/switch','POST',body,origin))).status).toBe(403);
  expect((await f.proxy(f.request('/__sbarbase/switch'))).status).toBe(405);
  expect((await f.proxy(f.request('/__sbarbase/switch','POST','{"environment":"../other"}'))).status).toBe(400);
  expect((await f.proxy(f.request('/__sbarbase/switch','POST','x'.repeat(513)))).status).toBe(413);
  expect(f.selected).toBe(0);
  f.catalog.requestStudio('alice',f.target,'stopped');expect((await f.proxy(f.request('/__sbarbase/switch','POST',body))).status).toBe(409);
  expect(f.catalog.studio('alice',f.target).desired).toBe('stopped');
 }finally{f.catalog.close();}
});

test('switch rechecks both target membership and source membership after asynchronous body reads',async()=>{
 const f=fixture();try{
  f.catalog.setMember('bob',f.other,'alice','viewer');
  expect((await f.proxy(f.request('/__sbarbase/switch','POST',JSON.stringify({environment:f.target})))).status).toBe(403);
  let control!:ReadableStreamDefaultController<Uint8Array>;
  const body=new ReadableStream<Uint8Array>({start(controller){control=controller;}});
  const pending=f.proxy(new Request(f.origin+'/__sbarbase/switch',{method:'POST',headers:{cookie:f.cookie,origin:f.origin,'content-type':'application/json'},body,duplex:'half'} as RequestInit));
  const before=f.selected;f.revoke();control.enqueue(new TextEncoder().encode(JSON.stringify({environment:f.target})));control.close();
  expect((await pending).status).toBe(403);expect(f.selected).toBe(before);
 }finally{f.catalog.close();}
});

test('a stopped upstream cannot be selected even with a stale running catalog row',async()=>{
 const f=fixture();try{f.unavailable();expect((await f.proxy(f.request('/__sbarbase/switch','POST',JSON.stringify({environment:f.target})))).status).toBe(409);expect(f.calls).toBe(0);}finally{f.catalog.close();}
});

test('signed console origin survives entry and switching without trusting arbitrary return addresses',async()=>{
 const f=fixture();try{
  const ticket=signStudio(key,{runtime:f.runtime,actor:'alice',epoch:0,kind:'ticket',expires:Date.now()+60_000,console:'http://localhost:8787'});
  const entered=await f.proxy(new Request(f.origin+'/__sbarbase/enter?ticket='+ticket));
  const cookie=entered.headers.get('set-cookie')!.split(';')[0]!;
  const nav=await f.proxy(new Request(f.origin+'/__sbarbase/navigation',{headers:{cookie}}));expect((await nav.json()).consoleUrl).toBe('http://localhost:8787');
  const moved=await f.proxy(new Request(f.origin+'/__sbarbase/switch',{method:'POST',headers:{cookie,origin:f.origin,'content-type':'application/json'},body:JSON.stringify({environment:f.target})}));
  const destination=new URL((await moved.json()).href);expect(verifyStudio(key,destination.searchParams.get('ticket'),f.targetRuntime,'ticket')?.console).toBe('http://localhost:8787');
  const bad=signStudio(key,{runtime:f.runtime,actor:'alice',epoch:0,kind:'session',expires:Date.now()+60_000,console:'https://attacker.example/path'});
  const safe=await f.proxy(new Request(f.origin+'/__sbarbase/navigation',{headers:{cookie:STUDIO_COOKIE+'='+bad}}));expect((await safe.json()).consoleUrl).toBe('http://127.0.0.1:8787/');
 }finally{f.catalog.close();}
});

 test('workspace permits same-origin vendor frames while preserving other CSP restrictions',async()=>{
 const f=fixture();try{
  const response=await f.proxy(f.request('/project/default/editor'));
  expect(await response.text()).toBe('original vendor');expect(response.headers.get('x-frame-options')).toBe('SAMEORIGIN');
  expect(response.headers.get('content-security-policy')).toBe("default-src 'self'; script-src 'nonce-vendor'; frame-ancestors 'self'");
  const plain=studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,upstream:()=> 'http://vendor.internal',transport:(async()=>new Response('vendor',{headers:{'content-type':'text/html','content-security-policy':"frame-ancestors 'none'"}})) as unknown as typeof fetch});
  expect((await plain(f.request('/project/default/editor'))).headers.get('content-security-policy')).toBe("frame-ancestors 'none'");
 }finally{f.catalog.close();}
});
