import {test,expect} from 'bun:test';
import {mkdtempSync,existsSync,readdirSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {RequestLog} from '../src/gateway/observe';
import {commitVersion,deploy,deployment,discardVersion,functionsHandler,readManifest,remove,setVerifyJwt,stageVersion,DeployError} from '../src/control/functions';
import {bootMessage,bootResultOf,bootThrough,settingsChange,type Boot,type BootResult} from '../src/control/functions-write';

const A='e_'+'a'.repeat(24);
const temporary=()=>mkdtempSync(join(tmpdir(),'functions-write-'));
const files=(entries:Record<string,string>,verify=true)=>deployment({files:entries,verify_jwt:verify});

test('the runtime message loses the container paths and the engine prefixes, and stays short',()=>{
 expect(bootMessage('worker boot error: failed to bootstrap runtime: failed to create the graph: Module not found "file:///home/deno/functions/v1abc-0123abcd/hello/missing.ts".\n    at file:///home/deno/functions/v1abc-0123abcd/hello/index.ts:1:17'))
  .toBe('Module not found "missing.ts".\n    at index.ts:1:17');
 expect(bootMessage('worker boot error: failed to bootstrap runtime: could not find an appropriate entrypoint')).toBe('could not find an appropriate entrypoint');
 expect(bootMessage('error at file:///etc/passwd and file:///home/deno/main/index.ts')).toBe('error at [file] and [file]');
 expect(bootMessage('\u001b[31mred\u001b[0m   \n text')).toBe('red\n text');
 expect(bootMessage('x'.repeat(5000)).length).toBe(600);
});

test('a runtime answer becomes passed, refused with its message, or unreachable',async()=>{
 expect(await bootResultOf(Response.json({ok:true}))).toEqual({state:'passed'});
 const refused=await bootResultOf(Response.json({ok:false,message:'worker boot error: failed to bootstrap runtime: could not find an appropriate entrypoint'},{status:422}));
 expect(refused).toEqual({state:'refused',message:'could not find an appropriate entrypoint'});
 expect(await bootResultOf(Response.json({},{status:422}))).toMatchObject({state:'refused'});
 for(const status of [401,404,500,502,503])expect(await bootResultOf(new Response('x',{status}))).toEqual({state:'unreachable'});
});

test('a settings change is exactly verify_jwt as a boolean',()=>{
 expect(settingsChange({verify_jwt:false})).toEqual({verify_jwt:false});
 expect(settingsChange({verify_jwt:true})).toEqual({verify_jwt:true});
 for(const bad of [null,[],{},{verify_jwt:'no'},{verify_jwt:1},{verify_jwt:true,name:'x'},{name:'x'},'verify_jwt',7])expect(settingsChange(bad)).toBeUndefined();
});

test('staging writes a version and leaves the manifest alone until it is committed; a discard removes it',()=>{
 const root=temporary();
 try {
  deploy(root,A,'hello',files({'index.ts':'one'}),1_000);
  const staged=stageVersion(root,A,'hello',files({'index.ts':'two','lib/a.ts':'x'}),2_000);
  expect(existsSync(join(root,A,staged.version,'hello','lib','a.ts'))).toBe(true);
  expect(readManifest(root,A).functions.hello!.version).not.toBe(staged.version);
  discardVersion(root,A,staged.version);
  expect(existsSync(join(root,A,staged.version))).toBe(false);
  const again=stageVersion(root,A,'hello',files({'index.ts':'three'}),3_000);
  const entry=commitVersion(root,A,'hello',again,false,3_000);
  expect(entry).toMatchObject({version:again.version,verify_jwt:false,deployments:2,created_at:1_000,updated_at:3_000});
  expect(()=>discardVersion(root,A,'../etc')).toThrow();
  expect(()=>discardVersion(root,A,'hello')).toThrow();
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('the JWT check changes without a new version, and only for a function that is deployed',()=>{
 const root=temporary();
 try {
  const first=deploy(root,A,'hello',files({'index.ts':'one'}),1_000);
  const changed=setVerifyJwt(root,A,'hello',false)!;
  expect(changed).toEqual({...first,verify_jwt:false});
  expect(readManifest(root,A).functions.hello).toEqual({...first,verify_jwt:false});
  expect(setVerifyJwt(root,A,'missing',false)).toBeUndefined();
  expect(setVerifyJwt(root,A,'constructor',false)).toBeUndefined();
  expect(setVerifyJwt(root,A,'toString',true)).toBeUndefined();
  expect(readdirSync(join(root,A)).filter(name=>name.startsWith('v'))).toEqual([first.version]);
 } finally {rmSync(root,{recursive:true,force:true});}
});

async function setup(boot:Boot|undefined,log?:RequestLog){
 const catalog=new Catalog(':memory:'),code=temporary(),secrets=temporary();
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const other=catalog.createOrganization('bob','B');
 const second=catalog.createEnvironment('bob',catalog.createProject('bob',other,'Q'),'production');
 for(const id of [environment,second]){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment??id,job.claim!,true);}
 const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets,log,boot);
 const call=(actor:string,env:string,path:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${env}/${path}`,
  {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
 const runtime=(await (await call('alice',environment,'functions')).json() as any).data.runtime as string;
 const turnOn=()=>(catalog as any).db.query("UPDATE functions_settings SET state='on' WHERE runtime=?").run(runtime);
 const audit=()=>((catalog as any).db.query('SELECT action,detail FROM audit_events ORDER BY rowid').all() as {action:string;detail:string}[]);
 return {catalog,code,secrets,call,environment,second,runtime,turnOn,audit,done:()=>{catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}};
}
const SERVE={files:{'index.ts':'Deno.serve(()=>new Response("hi"))'}};

test('a deploy the running runtime cannot start is refused, leaves the live version and no folder behind',async()=>{
 const asked:string[][]=[];
 const boot:Boot=async(runtime,version,name)=>{asked.push([runtime,version,name]);return {state:'refused',message:'Module not found "missing.ts".'};};
 const t=await setup(boot);
 try {
  // The first deploy has no runtime to ask: it is skipped, and turns Edge Functions on.
  const first=await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  expect(first.status).toBe(201);
  expect(((await first.json()) as any).data.deployed.boot_check).toBe('skipped');
  expect(asked).toEqual([]);
  t.turnOn();
  const live=readManifest(t.code,t.runtime).functions.hello!.version;
  const folders=()=>readdirSync(join(t.code,t.runtime)).sort();
  const before=folders();
  const refused=await t.call('alice',t.environment,'functions/hello','PUT',{files:{'index.ts':'import "./missing.ts"'}});
  expect(refused.status).toBe(422);
  expect(await refused.json()).toEqual({message:'Module not found "missing.ts".',boot_check:'failed'});
  expect(asked).toHaveLength(1);expect(asked[0]![0]).toBe(t.runtime);expect(asked[0]![2]).toBe('hello');
  expect(readManifest(t.code,t.runtime).functions.hello!.version).toBe(live);
  expect(folders()).toEqual(before);
  expect(t.audit().some(row=>row.action==='functions.deploy_refused'&&JSON.parse(row.detail).name==='hello')).toBe(true);
 } finally {t.done();}
});

test('a deploy the runtime starts goes live and says it was checked; an unreachable runtime does not block it',async()=>{
 let result:BootResult={state:'passed'};
 const t=await setup(async()=>result);
 try {
  await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  t.turnOn();
  const passed=await t.call('alice',t.environment,'functions/hello','PUT',{files:{'index.ts':'Deno.serve(()=>new Response("two"))'}});
  expect(passed.status).toBe(201);
  const body=(await passed.json()) as any;
  expect(body.data.deployed).toMatchObject({name:'hello',boot_check:'passed',deployments:2});
  result={state:'unreachable'};
  const unreachable=await t.call('alice',t.environment,'functions/hello','PUT',{files:{'index.ts':'Deno.serve(()=>new Response("three"))'}});
  expect(unreachable.status).toBe(201);
  expect(((await unreachable.json()) as any).data.deployed).toMatchObject({boot_check:'unreachable',deployments:3});
  expect(t.audit().filter(row=>row.action==='functions.deployed').map(row=>JSON.parse(row.detail).boot_check)).toEqual(['skipped','passed','unreachable']);
 } finally {t.done();}
});

test('with no runtime behind it, a deploy is not checked and still works',async()=>{
 const t=await setup(undefined);
 try {
  const answer=await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  expect(answer.status).toBe(201);
  expect(((await answer.json()) as any).data.deployed.boot_check).toBe('skipped');
 } finally {t.done();}
});

test('the JWT check route is for owners and admins of this environment, takes one boolean and does not deploy',async()=>{
 const t=await setup(undefined);
 try {
  await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  await t.call('bob',t.second,'functions/hello','PUT',SERVE);
  const version=readManifest(t.code,t.runtime).functions.hello!.version;
  expect((await t.call('carol',t.environment,'functions/hello','PATCH',{verify_jwt:false})).status).toBe(403);
  expect((await t.call('bob',t.environment,'functions/hello','PATCH',{verify_jwt:false})).status).toBe(403);
  expect((await t.call('alice',t.second,'functions/hello','PATCH',{verify_jwt:false})).status).toBe(403);
  for(const bad of [{},{verify_jwt:'no'},{verify_jwt:false,x:1},[],null])expect((await t.call('alice',t.environment,'functions/hello','PATCH',bad)).status).toBe(400);
  expect((await t.call('alice',t.environment,'functions/missing','PATCH',{verify_jwt:false})).status).toBe(404);
  expect((await t.call('alice',t.environment,'functions/constructor','PATCH',{verify_jwt:false})).status).toBe(404);
  expect(readManifest(t.code,t.runtime).functions.hello!.verify_jwt).toBe(true);
  const changed=await t.call('alice',t.environment,'functions/hello','PATCH',{verify_jwt:false});
  expect(changed.status).toBe(200);
  expect(((await changed.json()) as any).data.functions[0]).toMatchObject({name:'hello',verify_jwt:false});
  expect(readManifest(t.code,t.runtime).functions.hello).toMatchObject({version,verify_jwt:false});
  expect(t.audit().filter(row=>row.action==='functions.settings_changed').map(row=>JSON.parse(row.detail))).toEqual([{name:'hello',verify_jwt:false}]);
 } finally {t.done();}
});

test('deploy errors from the old route keep their shape',async()=>{
 const t=await setup(async()=>({state:'passed'}));
 try {
  expect((await t.call('alice',t.environment,'functions/bad','PUT',{files:{'main.ts':'x'}})).status).toBe(400);
  expect((await t.call('alice',t.environment,'functions/_x','PUT',SERVE)).status).toBe(400);
  expect((await t.call('carol',t.environment,'functions/x','PUT',SERVE)).status).toBe(403);
  expect(()=>deployment({files:{'index.ts':1}})).toThrow(DeployError);
 } finally {t.done();}
});

test('the boot call goes to the environment\'s runtime with its service token, and never throws',async()=>{
 const seen:{url:string;method:string;authorization:string}[]=[];
 const ok=(async(url:URL,init:RequestInit)=>{seen.push({url:String(url),method:String(init.method),authorization:String((init.headers as Record<string,string>).authorization)});return Response.json({ok:true});}) as unknown as typeof fetch;
 const boot=bootThrough(runtime=>runtime===A?{url:'http://10.0.0.7:9000',token:'service.token'}:undefined,ok);
 expect(await boot(A,'v1abc-0123abcd','hello')).toEqual({state:'passed'});
 expect(seen).toEqual([{url:'http://10.0.0.7:9000/_sb_boot/hello/v1abc-0123abcd',method:'POST',authorization:'Bearer service.token'}]);
 expect(await boot('e_'+'b'.repeat(24),'v1abc-0123abcd','hello')).toEqual({state:'skipped'});
 expect(seen).toHaveLength(1);
 expect(await bootThrough(()=>'maintenance',ok)(A,'v','hello')).toEqual({state:'unreachable'});
 expect(await bootThrough(()=>{throw new Error('placement');},ok)(A,'v','hello')).toEqual({state:'unreachable'});
 expect(await bootThrough(()=>({url:'http://x',token:'t'}),(async()=>{throw new Error('connection refused');}) as unknown as typeof fetch)(A,'v','hello')).toEqual({state:'unreachable'});
 expect(await bootThrough(()=>({url:'http://x',token:'t'}),(async()=>new Response('no',{status:502})) as unknown as typeof fetch)(A,'v','hello')).toEqual({state:'unreachable'});
 const slow=(async(_url:URL,init:RequestInit)=>new Promise<Response>((_,reject)=>init.signal!.addEventListener('abort',()=>reject(new Error('aborted'))))) as unknown as typeof fetch;
 expect(await bootThrough(()=>({url:'http://x',token:'t'}),slow,30)(A,'v','hello')).toEqual({state:'unreachable'});
});

test('a refused first deploy of a new name leaves no function and no folder, and a boot that throws does not block a deploy',async()=>{
 let mode:'refuse'|'throw'='refuse';
 const t=await setup(async()=>{if(mode==='throw')throw new Error('network');return {state:'refused',message:'could not find an appropriate entrypoint'};});
 try {
  await t.call('alice',t.environment,'functions/seed','PUT',SERVE);
  t.turnOn();
  const before=readdirSync(join(t.code,t.runtime)).sort();
  const refused=await t.call('alice',t.environment,'functions/fresh','PUT',SERVE);
  expect(refused.status).toBe(422);
  expect(Object.keys(readManifest(t.code,t.runtime).functions)).toEqual(['seed']);
  expect(readdirSync(join(t.code,t.runtime)).sort()).toEqual(before);
  mode='throw';
  const thrown=await t.call('alice',t.environment,'functions/fresh','PUT',SERVE);
  expect(thrown.status).toBe(201);
  expect(((await thrown.json()) as any).data.deployed.boot_check).toBe('unreachable');
 } finally {t.done();}
});

test('the audit rows of a deploy hold the name, the version, the size and the check, never the code or a secret',async()=>{
 const t=await setup(async()=>({state:'passed'}));
 try {
  await t.call('alice',t.environment,'functions/hello','PUT',{files:{'index.ts':'const SECRET_IN_CODE="sk_live_CODE_SENTINEL"'}});
  await t.call('alice',t.environment,'function-secrets','PUT',{secrets:{KEY:'sk_live_VALUE_SENTINEL'}});
  await t.call('alice',t.environment,'functions/hello','PATCH',{verify_jwt:false});
  const text=JSON.stringify(t.audit());
  expect(text).not.toContain('SENTINEL');
  const actions=t.audit().map(row=>row.action);
  for(const action of ['functions.deployed','functions.secrets_changed','functions.settings_changed'])expect(actions).toContain(action);
 } finally {t.done();}
});

test('a deploy refused by the check answers 422 with the runtime message to the Supabase compatible route too',async()=>{
 const t=await setup(async()=>({state:'refused',message:'Module not found "missing.ts".'}));
 try {
  await t.call('alice',t.environment,'functions/seed','PUT',SERVE);
  t.turnOn();
  const refused=await t.call('alice',t.environment,'functions/seed','PUT',{files:{'index.ts':'import "./missing.ts"'},shared:{'cors.ts':'x'},verify_jwt:false});
  expect(refused.status).toBe(422);
  expect(((await refused.json()) as any).message).toBe('Module not found "missing.ts".');
  expect(readManifest(t.code,t.runtime).functions.seed!.verify_jwt).toBe(true);
 } finally {t.done();}
});

test('the check is not asked for while Edge Functions are off or applying, and not for a function other than the one deployed',async()=>{
 const asked:string[]=[];
 const t=await setup(async(_runtime,_version,name)=>{asked.push(name);return {state:'passed'};});
 try {
  await t.call('alice',t.environment,'functions/a','PUT',SERVE);
  expect(asked).toEqual([]);
  t.turnOn();
  await t.call('alice',t.environment,'functions/b','PUT',SERVE);
  expect(asked).toEqual(['b']);
  (t.catalog as any).db.query("UPDATE functions_settings SET state='pending' WHERE runtime=?").run(t.runtime);
  await t.call('alice',t.environment,'functions/c','PUT',SERVE);
  expect(asked).toEqual(['b']);
 } finally {t.done();}
});

test('staging and committing refuse a runtime id that is not one, and a settings change finds nothing there',()=>{
 const root=temporary();
 try {
  expect(()=>stageVersion(root,'../x','hello',files({'index.ts':'x'}))).toThrow();
  expect(()=>stageVersion(root,'e_short','hello',files({'index.ts':'x'}))).toThrow();
  expect(()=>commitVersion(root,'../x','hello',{version:'v1-aaaaaaaa',size:1},true)).toThrow();
  expect(setVerifyJwt(root,'../x','hello',true)).toBeUndefined();
  expect(readdirSync(root)).toEqual([]);
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('a settings change after the function was deleted is a 404 and changes nothing',async()=>{
 const t=await setup(undefined);
 try {
  await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  expect((await t.call('alice',t.environment,'functions/hello','DELETE')).status).toBe(200);
  expect((await t.call('alice',t.environment,'functions/hello','PATCH',{verify_jwt:false})).status).toBe(404);
  expect(readManifest(t.code,t.runtime).functions).toEqual({});
 } finally {t.done();}
});

test('a deploy starts "since the last deploy" again, as it did before the boot check',async()=>{
 const log=new RequestLog();
 const t=await setup(async()=>({state:'passed'}),log);
 try {
  await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  log.recordFunction(t.runtime,'hello',{method:'GET',path:'/',status:200,ms:1});log.recordFunction(t.runtime,'hello',{method:'GET',path:'/',status:500,ms:1});
  expect(log.functionMetrics(t.runtime,'hello','15m').sinceDeploy).toMatchObject({requests:2,serverErrors:1,complete:true});
  t.turnOn();
  await t.call('alice',t.environment,'functions/hello','PUT',SERVE);
  expect(log.functionMetrics(t.runtime,'hello','15m').sinceDeploy).toMatchObject({requests:0,serverErrors:0,complete:true});
  // A refused deploy does not reset it.
  log.recordFunction(t.runtime,'hello',{method:'GET',path:'/',status:200,ms:1});
 } finally {t.done();}
});

test('two deploys of new names cannot both take the last place while the check runs',async()=>{
 let release:()=>void=()=>{};
 const gate=new Promise<void>(resolve=>{release=resolve;});
 const t=await setup(async()=>{await gate;return {state:'passed'};});
 try {
  for(let index=0;index<99;index++)deploy(t.code,t.runtime,`f${index}`,files({'index.ts':'x'}),1_000+index);
  await t.call('alice',t.environment,'functions/seed','PUT',SERVE);
  t.turnOn();
  // 100 now: remove one place, then three new names race for it.
  expect(Object.keys(readManifest(t.code,t.runtime).functions)).toHaveLength(100);
  remove(t.code,t.runtime,'f0');
  const races=['a','b','c'].map(name=>t.call('alice',t.environment,`functions/new-${name}`,'PUT',SERVE));
  await new Promise(resolve=>setTimeout(resolve,50));
  release();
  const statuses=(await Promise.all(races)).map(answer=>answer.status).sort();
  expect(statuses).toEqual([201,400,400]);
  expect(Object.keys(readManifest(t.code,t.runtime).functions)).toHaveLength(100);
  // The two that lost left no version folder behind.
  const versions=readdirSync(join(t.code,t.runtime)).filter(name=>name.startsWith('v'));
  const live=new Set(Object.values(readManifest(t.code,t.runtime).functions).map(entry=>entry.version));
  const unused=versions.filter(version=>!live.has(version));
  // Only the removed function's old version (kept for a few minutes for calls still running) may be left.
  for(const version of unused)for(const name of ['new-a','new-b','new-c'])expect(existsSync(join(t.code,t.runtime,version,name))).toBe(false);
 } finally {t.done();}
});

test('a message from the runtime loses plain container paths as well as file addresses',()=>{
 expect(bootMessage('error at /home/deno/functions/v1abc-0123abcd/hello/index.ts:3 and /var/tmp/sb-compile-edge-runtime/x/index.ts (/deno-dir/npm/x)'))
  .toBe('error at [path] and [path] ([path])');
 expect(bootMessage('import "../_shared/cors.ts" failed')).toBe('import "../_shared/cors.ts" failed');
});
