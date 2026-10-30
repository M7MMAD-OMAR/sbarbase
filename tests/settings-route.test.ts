import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,existsSync,writeFileSync as write,symlinkSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {settingsFromInput} from '../src/control/sign-in';
import {cleanText,readPrivateJson,readPrivateObject,runtimeFile,settingsHandler,wellFormed,writePrivateFile,SIGN_IN_FILE_MESSAGE,type SettingsRoute} from '../src/control/settings-route';

class DemoError extends Error {}
const RUNTIME='e_'+'a'.repeat(24);

test('text checks: no line break, NUL or lone surrogate, a length cap, empty only where allowed, control characters when asked',()=>{
 expect(wellFormed('ok ب 😀')).toBe(true);
 expect(wellFormed('a\ud800b')).toBe(false);expect(wellFormed('\udc00')).toBe(false);
 expect(cleanText('host.example.com',253)).toBe(true);
 expect(cleanText('',10)).toBe(false);expect(cleanText('',10,{allowEmpty:true})).toBe(true);
 expect(cleanText('x'.repeat(11),10)).toBe(false);expect(cleanText('x'.repeat(10),10)).toBe(true);
 for(const bad of ['a\nb','a\rb','a\x00b','a\ud800b',5,null,undefined,{}])expect(cleanText(bad,50)).toBe(false);
 // A tab passes the line check and fails the control check.
 expect(cleanText('a\tb',50)).toBe(true);expect(cleanText('a\tb',50,{control:true})).toBe(false);expect(cleanText('a\x7fb',50,{control:true})).toBe(false);
});

test('the private file name is one environment, and a name that is not a runtime is refused',()=>{
 expect(runtimeFile('/d',RUNTIME,'-x.json')).toBe(`/d/${RUNTIME}-x.json`);
 for(const bad of ['../etc','e_'+'A'.repeat(24),'e_'+'a'.repeat(23),''])expect(()=>runtimeFile('/d',bad,'-x.json')).toThrow('Invalid runtime');
});

test('a private file is read only when it is a plain file, closed to others, small and JSON; a missing one is null',()=>{
 const dir=mkdtempSync(join(tmpdir(),'sr-')),path=join(dir,'a.json'),fail=()=>new DemoError('file');
 expect(readPrivateJson(path,100,fail)).toBeUndefined();expect(readPrivateObject(path,100,fail)).toBeNull();
 write(path,'{"a":1}',{mode:0o600});
 expect(readPrivateObject(path,100,fail)).toEqual({a:1});
 expect(readPrivateJson(path,100,fail)).toEqual({a:1});
 expect(()=>readPrivateJson(path,3,fail)).toThrow(DemoError);
 chmodSync(path,0o640);expect(()=>readPrivateJson(path,100,fail)).toThrow(DemoError);
 chmodSync(path,0o604);expect(()=>readPrivateJson(path,100,fail)).toThrow(DemoError);
 chmodSync(path,0o600);write(path,'{nope',{mode:0o600});expect(()=>readPrivateJson(path,100,fail)).toThrow(DemoError);
 // A file that holds null, a list or a bare value is a bad file, never "no file".
 for(const text of ['null','[]','5','"x"','false']){write(path,text,{mode:0o600});expect(()=>readPrivateObject(path,100,fail)).toThrow(DemoError);}
 write(path,'{"a":1}',{mode:0o600});
 // A link is refused, a dangling one too, and a directory is not a file.
 rmSync(path);symlinkSync('/etc/hostname',path);expect(()=>readPrivateJson(path,100,fail)).toThrow(DemoError);
 rmSync(path);symlinkSync(join(dir,'missing'),path);expect(()=>readPrivateJson(path,100,fail)).toThrow(DemoError);
 rmSync(path);expect(()=>readPrivateJson(dir,100,fail)).toThrow(DemoError);
});

test('a private file is written 0600 by a pending copy, replaces the old one, removes on null, and never follows a link',()=>{
 const base=mkdtempSync(join(tmpdir(),'sr-')),dir=join(base,'nested','dir'),path=join(dir,'a.json'),fail=()=>new DemoError('file');
 writePrivateFile(dir,path,'{"a":1}',fail);
 expect(statSync(path).mode&0o777).toBe(0o600);expect(statSync(dir).mode&0o777).toBe(0o700);
 // A pending file left by a crash does not block the next save.
 write(path+'.pending','stale',{mode:0o600});
 writePrivateFile(dir,path,'{"a":2}',fail);
 expect(readFileSync(path,'utf8')).toBe('{"a":2}');expect(existsSync(path+'.pending')).toBe(false);
 writePrivateFile(dir,path,null,fail);expect(existsSync(path)).toBe(false);
 writePrivateFile(dir,path,null,fail);
 // A link in its place is refused for a write and for a removal, and its target is untouched.
 const target=join(base,'target');write(target,'keep',{mode:0o600});symlinkSync(target,path);
 expect(()=>writePrivateFile(dir,path,'{"a":3}',fail)).toThrow(DemoError);
 expect(()=>writePrivateFile(dir,path,null,fail)).toThrow(DemoError);
 expect(readFileSync(target,'utf8')).toBe('keep');
});

type Demo={value:string};
function fixture() {
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'sr-si-')),dir=mkdtempSync(join(tmpdir(),'sr-dir-'));
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);
 const runtime=catalog.getProvision('alice',environment).runtime;
 const path=join(dir,runtime+'-demo.json'),fail=()=>new DemoError('file');
 const log:string[]=[];
 const route:SettingsRoute<Demo>={
  segment:'demo',error:DemoError,noun:'thing',fileMessage:'the demo file is bad',
  read:name=>{log.push('read');return readPrivateObject(join(dir,name+'-demo.json'),100,fail) as Demo|null;},
  check:body=>{log.push('check');if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new DemoError('enabled');},
  next:(_input,body,before)=>{log.push('next');if(body.boom)throw new Error('boom');if(typeof body.value!=='string')throw new DemoError('value_text');return body.enabled===false?null:{value:body.value+(before?'+':'')};},
  write:(name,after)=>{log.push('write');writePrivateFile(dir,join(dir,name+'-demo.json'),after&&JSON.stringify(after),fail);},
  changed:(before,after)=>before?.value===after?.value?[]:['DEMO_VALUE'],
  view:(_name,saved)=>({demo:saved})};
 const make=(interval:number)=>settingsHandler(catalog,async request=>request.headers.get('authorization'),signIn,interval,route);
 const call=(handler:ReturnType<typeof make>,actor:string,method='GET',body?:unknown,id=environment,segment='demo')=>
  handler(new Request(`http://local/management/v1/environments/${id}/${segment}`,{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:typeof body==='string'?body:JSON.stringify(body)}));
 return {catalog,signIn,dir,org,environment,runtime,path,log,make,call};
}

test('the shared route: its own path only, GET and PUT only, owners and admins only, a bad body refused before anything is read',async()=>{
 const f=fixture();
 try {
  const handler=f.make(0);
  expect((await f.call(handler,'alice','GET',undefined,f.environment,'other')).status).toBe(404);
  expect((await f.call(handler,'alice','GET',undefined,'not-an-id')).status).toBe(404);
  expect((await f.call(handler,'alice','DELETE')).status).toBe(405);
  expect((await f.call(handler,'carol')).status).toBe(403);expect((await f.call(handler,'bob','PUT',{value:'x'})).status).toBe(403);
  expect(f.log).toEqual([]);
  const first=await f.call(handler,'alice');
  expect(first.status).toBe(200);
  expect(await first.json()).toMatchObject({data:{runtime:f.runtime,demo:null}});
  f.log.length=0;
  expect((await f.call(handler,'alice','PUT','{not json')).status).toBe(400);
  expect(f.log).toEqual([]);
  const response=await f.call(handler,'alice','PUT',{enabled:'x',value:'a'});
  expect(response.status).toBe(400);expect((await response.json() as {message:string}).message).toBe('Check the enabled thing');
  // The route's check runs before its file is read, and nothing is written for a refusal.
  expect(f.log).toEqual(['check']);expect(existsSync(f.path)).toBe(false);
 } finally {f.catalog.close();}
});

test('the shared route: a save checks, reads, then writes, moves the sign-in file with the row, and names what changed',async()=>{
 const f=fixture();
 try {
  const handler=f.make(0);
  write(join(f.signIn,f.runtime+'-auth.json'),JSON.stringify(settingsFromInput({site_url:'https://a.example.com'},null)),{mode:0o600});
  const saved=await f.call(handler,'alice','PUT',{value:'one'});
  expect(saved.status).toBe(202);
  expect(f.log).toEqual(['check','read','next','write']);
  const body=await saved.json() as {data:{state:string;revision:number;demo:Demo}};
  expect(body.data).toMatchObject({state:'pending',demo:{value:'one'}});
  expect(JSON.parse(readFileSync(join(f.signIn,f.runtime+'-auth.json'),'utf8')).revision).toBe(body.data.revision);
  expect(statSync(f.path).mode&0o777).toBe(0o600);
  expect(JSON.stringify(f.catalog.auditEvents('alice',f.org,50))).toContain('DEMO_VALUE');
  // Without a sign-in file the row still moves ahead of its last revision.
  const again=await (await f.call(handler,'alice','PUT',{value:'two'})).json() as {data:{revision:number;demo:Demo}};
  expect(again.data.demo.value).toBe('two+');expect(again.data.revision).toBe(body.data.revision+1);
  // Off removes the file.
  expect((await f.call(handler,'alice','PUT',{enabled:false,value:'x'})).status).toBe(202);
  expect(existsSync(f.path)).toBe(false);
 } finally {f.catalog.close();}
});

test('the shared route: a stale page is 409, a quick second save is 429 with retry-after, a null base revision is accepted',async()=>{
 const f=fixture();
 try {
  const slow=f.make(60_000);
  expect((await f.call(slow,'alice','PUT',{value:'x',base_revision:99})).status).toBe(409);
  expect((await f.call(slow,'alice','PUT',{value:'x',base_revision:null})).status).toBe(202);
  const busy=await f.call(slow,'alice','PUT',{value:'y'});
  expect(busy.status).toBe(429);
  const wait=Number(busy.headers.get('retry-after'));expect(wait).toBeGreaterThan(0);expect(wait).toBeLessThanOrEqual(60);
  expect((await busy.json() as {message:string}).message).toBe('Wait a few seconds before saving again.');
  // The refusals wrote nothing more than the one accepted save.
  expect(JSON.parse(readFileSync(f.path,'utf8'))).toEqual({value:'x'});
 } finally {f.catalog.close();}
});

test('the shared route: every refusal has its own status and text, and no unexpected error leaks',async()=>{
 const f=fixture();
 try {
  const handler=f.make(0);
  // The route's own file problem is 409 with its message; a value problem is 400 naming it with underscores read as spaces.
  write(f.path,'{}');chmodSync(f.path,0o644);
  let response=await f.call(handler,'alice');
  expect(response.status).toBe(409);expect((await response.json() as {message:string}).message).toBe('the demo file is bad');
  rmSync(f.path);
  response=await f.call(handler,'alice','PUT',{value:5});
  expect(response.status).toBe(400);expect((await response.json() as {message:string}).message).toBe('Check the value text thing');
  // The sign-in settings file has its own 409.
  const sign=join(f.signIn,f.runtime+'-auth.json');
  write(sign,JSON.stringify(settingsFromInput({site_url:'https://a.example.com'},null)));chmodSync(sign,0o644);
  response=await f.call(handler,'alice','PUT',{value:'x'});
  expect(response.status).toBe(409);expect((await response.json() as {message:string}).message).toBe(SIGN_IN_FILE_MESSAGE);
  expect(existsSync(f.path)).toBe(false);
  rmSync(sign);
  // Anything else is a 500 that names nothing.
  response=await f.call(handler,'alice','PUT',{boom:true});
  expect(response.status).toBe(500);expect(await response.json()).toEqual({message:'Management operation failed'});
  // An environment that is not ready is 409.
  const second=f.catalog.createEnvironment('alice',f.catalog.createProject('alice',f.org,'Q'),'production');
  response=await f.call(handler,'alice','GET',undefined,second);
  expect(response.status).toBe(409);expect((await response.json() as {message:string}).message).toBe('Environment is not ready');
 } finally {f.catalog.close();}
});

test('a mail, phone or templates file that holds only null is refused by its route, not read as no file',async()=>{
 const {smtpHandler}=await import('../src/control/smtp'),{smsHandler}=await import('../src/control/sms'),{mailTemplatesHandler}=await import('../src/control/mail-templates');
 const f=fixture();
 try {
  const who=async(request:Request)=>request.headers.get('authorization');
  const cases:[string,(dir:string)=>ReturnType<typeof f.make>][]=[['mail','smtp'],['sms','sms'],['templates','mail-templates']].map(([suffix,segment])=>[segment!,()=>{
   const make=segment==='smtp'?smtpHandler:segment==='sms'?smsHandler:mailTemplatesHandler;return make(f.catalog,who,f.signIn,f.dir,0) as ReturnType<typeof f.make>;}] as [string,(dir:string)=>ReturnType<typeof f.make>]);
  for(const [segment,build] of cases){
   const suffix=segment==='smtp'?'mail':segment==='sms'?'sms':'templates';
   const path=join(f.dir,`${f.runtime}-${suffix}.json`);
   write(path,'null');chmodSync(path,0o600);
   const handler=build(f.dir);
   expect((await f.call(handler,'alice','GET',undefined,f.environment,segment)).status).toBe(409);
   expect((await f.call(handler,'alice','PUT',{enabled:false},f.environment,segment)).status).toBe(409);
   expect(readFileSync(path,'utf8')).toBe('null');
   rmSync(path);
  }
 } finally {f.catalog.close();}
});
