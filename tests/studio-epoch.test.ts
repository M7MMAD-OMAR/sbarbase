import {test,expect} from 'bun:test';
import {createHmac} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {signStudio,verifyStudio,studioHandler,studioProxy,studioHost,STUDIO_COOKIE} from '../src/control/studio';

const key=Buffer.alloc(32,11),runtime='e_'+'a'.repeat(24),target='e_'+'b'.repeat(24);
const origin='http://'+studioHost(runtime);
const bytes=(value:string)=>new TextEncoder().encode(value);
function signed(value:object){
 const body=Buffer.from(JSON.stringify(value)).toString('base64url');
 return body+'.'+createHmac('sha256',key).update(body).digest('base64url');
}
function fixture(transport:typeof fetch=(async()=>new Response('vendor')) as unknown as typeof fetch){
 let epoch=4,selections=0;
 const proxy=studioProxy({key:()=>key,epoch:()=>epoch,allowed:()=>true,upstream:()=> 'http://vendor.invalid:3000',transport,
  navigation:()=>({organizations:[],projects:[],environments:[],selection:{organization:'example',project:null},
   current:{organization:{id:'example',name:'Example'},project:{id:'shop',name:'Shop'},environment:{id:'production',name:'Production'}}}),
  selection:()=>{selections++;return {runtime:target,state:'running',desired:'running',failure:null,updatedAt:null};}});
 const token=(kind:'ticket'|'session',value=epoch)=>signStudio(key,{runtime,actor:'alice',kind,epoch:value,expires:Date.now()+60_000});
 const request=(path='/query',method='GET',body?:ReadableStream<Uint8Array>|string,value=epoch)=>new Request(origin+path,
  {method,headers:{cookie:STUDIO_COOKIE+'='+token('session',value),origin,'content-type':'application/json'},
   ...(body===undefined?{}:{body,duplex:'half'})} as RequestInit);
 const enter=(ticket=token('ticket'))=>proxy(new Request(origin+'/__sbarbase/enter?ticket='+encodeURIComponent(ticket)));
 return {proxy,token,request,enter,revoke(){epoch++;},get epoch(){return epoch;},get selections(){return selections;}};
}

test('Studio refuses legacy credentials without an epoch even when membership is unchanged',async()=>{
 const f=fixture();
 for(const kind of ['ticket','session'] as const){
  const token=signed({runtime,actor:'alice',kind,expires:Date.now()+60_000});
  const response=kind==='ticket'?await f.enter(token):await f.proxy(new Request(origin+'/query',{headers:{cookie:STUDIO_COOKIE+'='+token}}));
  expect(response.status).toBe(401);
 }
});

test('Studio rejects malformed signed epoch values',()=>{
 for(const epoch of [-1,0.5,'4',null,Number.MAX_SAFE_INTEGER+1])
  expect(verifyStudio(key,signed({runtime,actor:'alice',kind:'ticket',epoch,expires:Date.now()+60_000}),runtime,'ticket')).toBeNull();
});

test('management revocation prevents an outstanding ticket from entering Studio',async()=>{
 const f=fixture(),ticket=f.token('ticket');f.revoke();
 expect((await f.enter(ticket)).status).toBe(401);
});

test('entering preserves the ticket epoch and a new issuance works after revocation',async()=>{
 const f=fixture();f.revoke();
 const response=await f.enter();expect(response.status).toBe(302);
 const token=response.headers.get('set-cookie')!.split(';')[0]!.split('=')[1]!;
 expect(verifyStudio(key,token,runtime,'session')?.epoch).toBe(f.epoch);
 const request=new Request(origin+'/query',{headers:{cookie:STUDIO_COOKIE+'='+token}});
 expect((await f.proxy(request)).status).toBe(200);f.revoke();
 expect((await f.proxy(request)).status).toBe(401);
});

test('revocation during a pending Studio mutation prevents its upstream effect',async()=>{
 let controller!:ReadableStreamDefaultController<Uint8Array>,calls=0;
 const f=fixture((async()=>{calls++;return new Response('mutated');}) as unknown as typeof fetch);
 const pending=f.proxy(f.request('/query','POST',new ReadableStream({start(value){controller=value;}})));
 f.revoke();controller.enqueue(bytes('{}'));controller.close();
 expect((await pending).status).toBe(403);expect(calls).toBe(0);
});

test('revocation during a vendor response wait withholds that response',async()=>{
 let release!:(value:Response)=>void,cancelled=false;
 const f=fixture((async()=>await new Promise<Response>(resolve=>{release=resolve;})) as unknown as typeof fetch);
 const pending=f.proxy(f.request());f.revoke();
 release(new Response(new ReadableStream({cancel(){cancelled=true;}})));
 expect((await pending).status).toBe(403);expect(cancelled).toBe(true);
});

test('revocation during a pending stream read withholds newly arriving bytes',async()=>{
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const f=fixture((async()=>new Response(new ReadableStream({start(value){controller=value;}}))) as unknown as typeof fetch);
 const response=await f.proxy(f.request()),reader=response.body!.getReader(),pending=reader.read();
 f.revoke();controller.enqueue(bytes('private'));controller.close();
 await expect(pending).rejects.toThrow();
});

test('revocation withholds unread workspace navigation',async()=>{
 const f=fixture(),response=await f.proxy(f.request('/__sbarbase/navigation'));
 expect(response.status).toBe(200);f.revoke();await expect(response.text()).rejects.toThrow();
});

test('revocation during switch body reading prevents selection',async()=>{
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const f=fixture(),pending=f.proxy(f.request('/__sbarbase/switch','POST',new ReadableStream({start(value){controller=value;}})));
 f.revoke();controller.enqueue(bytes('{"environment":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}'));controller.close();
 expect((await pending).status).toBe(403);expect(f.selections).toBe(0);
});

test('switch tickets preserve the source epoch and are refused after management revocation',async()=>{
 const f=fixture(),response=await f.proxy(f.request('/__sbarbase/switch','POST','{"environment":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}'));
 expect(response.status).toBe(200);
 const {href}=await response.json() as {href:string},ticket=new URL(href).searchParams.get('ticket')!;
 expect(verifyStudio(key,ticket,target,'ticket')?.epoch).toBe(f.epoch);f.revoke();
 expect((await f.proxy(new Request(href))).status).toBe(401);
});

test('Studio issuance and proxy observe revocation through separate connections to the shared catalog',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'studio-epoch-')),path=join(directory,'control.sqlite');
 const issuer=new Catalog(path),revoker=new Catalog(path);
 try{
  const organization=issuer.createOrganization('alice','Example');
  const environment=issuer.createEnvironment('alice',issuer.createProject('alice',organization,'Shop'),'Production');
  const job=issuer.claimProvision()!;issuer.finishProvision(environment,job.claim!,true);issuer.requestStudio('alice',environment,'running');
  // A supervisor writes this state after starting the vendor service.
  (issuer as unknown as {db:{query:(sql:string)=>{run:()=>unknown}}}).db.query("UPDATE studio_sessions SET state='running'").run();
  const runtime=issuer.getProvision('alice',environment).runtime,origin='http://'+studioHost(runtime);
  const handler=studioHandler(issuer,async()=> 'alice',()=>key);
  const proxy=studioProxy({key:()=>key,epoch:actor=>issuer.managementSecurity.epoch(actor),allowed:(actor,id)=>issuer.studioAllowed(actor,id),
   upstream:()=> 'http://vendor.invalid:3000',transport:(async()=>new Response('vendor')) as unknown as typeof fetch});
  const issue=async()=>{
   const response=await handler(new Request('http://localhost/management/v1/environments/'+environment+'/studio/session',{method:'POST'}));
   expect(response.status).toBe(201);return (await response.json() as {path:string}).path;
  };
  const outstanding=await issue();revoker.managementSecurity.revoke('alice');
  expect(issuer.studioAllowed('alice',runtime)).toBe(true);
  expect((await proxy(new Request(origin+outstanding))).status).toBe(401);
  const fresh=await issue(),ticket=new URL(origin+fresh).searchParams.get('ticket')!;
  expect(verifyStudio(key,ticket,runtime,'ticket')?.epoch).toBe(1);
  const entered=await proxy(new Request(origin+fresh));expect(entered.status).toBe(302);
  const request=new Request(origin+'/query',{headers:{cookie:entered.headers.get('set-cookie')!.split(';')[0]!}});
  expect((await proxy(request)).status).toBe(200);
  revoker.managementSecurity.revoke('alice');
  expect((await proxy(request)).status).toBe(401);
 }finally{revoker.close();issuer.close();rmSync(directory,{recursive:true,force:true});}
},10_000);

test('an unavailable epoch store refuses access before vendor dispatch',async()=>{
 let calls=0;
 const proxy=studioProxy({key:()=>key,epoch:()=>{throw new Error('Unavailable');},allowed:()=>true,upstream:()=> 'http://vendor.invalid:3000',
  transport:(async()=>{calls++;return new Response('vendor');}) as unknown as typeof fetch});
 const token=signStudio(key,{runtime,actor:'alice',kind:'session',epoch:0,expires:Date.now()+60_000});
 expect((await proxy(new Request(origin+'/query',{headers:{cookie:STUDIO_COOKIE+'='+token}}))).status).toBe(401);expect(calls).toBe(0);
});
