import {test,expect,beforeEach,afterEach} from 'bun:test';
import {mkdtempSync,readFileSync,rmSync,statSync,writeFileSync,existsSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {UPDATE_MESSAGES,DEFAULT_SETTINGS} from '../src/control/updates';

const COMMIT='a'.repeat(40),NEXT='b'.repeat(40);
let directory:string,checkout:string,catalog:Catalog,handler:ReturnType<typeof managementHandler>;

beforeEach(()=>{
  directory=mkdtempSync(join(tmpdir(),'sbarbase-updates-'));
  checkout=mkdtempSync(join(tmpdir(),'sbarbase-checkout-'));
  writeFileSync(join(checkout,'release.json'),JSON.stringify({version:'0.1.0'}));
  catalog=new Catalog(':memory:');
  const installation=catalog.initializeInstallation('first','olga','Installation');
  catalog.setMember('olga',installation,'vera','viewer');
  catalog.createOrganization('olga','Client');
  handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.',undefined,directory,checkout);
});
afterEach(()=>{catalog.close();rmSync(directory,{recursive:true,force:true});rmSync(checkout,{recursive:true,force:true});});

function write(name:string,value:unknown){writeFileSync(join(directory,name),JSON.stringify(value));}
function file(name:string){return JSON.parse(readFileSync(join(directory,name),'utf8'));}
async function call(path:string,method='GET',body?:unknown,actor='olga') {
  const response=await handler(new Request('http://local/management/v1/updates'+path,{method,
    headers:{authorization:actor,...(body===undefined?{}:{'content-type':'application/json'})},
    ...(body===undefined?{}:{body:JSON.stringify(body)})}));
  return {status:response.status,body:await response.json() as any};
}
function release(overrides:Record<string,unknown>={}) {
  return {version:'0.2.0',tag:'v0.2.0',commit:NEXT,class:'safe',signed:true,reasons:[],
    notes:{en:'Faster start.',ar:'بدء أسرع.'},changes:[{image:'images.lock.json:rest',from:'postgrest:v1',to:'postgrest:v2'}],
    minimum_from:'0.1.0',migrations:[],...overrides};
}
function checked(available:unknown=release(),refusals:string[]=[],commit=COMMIT,extra:Record<string,unknown>={}) {
  write('available.json',{current:{version:'0.1.0',commit},available,refusals,checked_at:'2026-09-25T10:00:00+00:00',...extra});
}
const ZONE={name:'Asia/Dubai',offset:'+04:00'};
function running(){write('current.json',{version:'0.1.0',commit:COMMIT,written_at:'2026-09-25T10:00:00+00:00',
  rollback:{started_at:null,possible:false,reason:UPDATE_MESSAGES.no_rollback},timezone:ZONE});}

test('every updates route answers 403 to anyone who is not an installation operator',async()=>{
  for(const actor of ['vera','mallory']) {
    expect((await call('','GET',undefined,actor)).status).toBe(403);
    expect((await call('/check','POST',undefined,actor)).status).toBe(403);
    expect((await call('/apply','POST',{version:'0.2.0'},actor)).status).toBe(403);
    expect((await call('/rollback','POST',undefined,actor)).status).toBe(403);
    expect((await call('/settings','PUT',DEFAULT_SETTINGS,actor)).status).toBe(403);
  }
  expect((await call('','GET',undefined,'')).status).toBe(401);
  expect(existsSync(join(directory,'request.json'))).toBe(false);
  expect(existsSync(join(directory,'settings.json'))).toBe(false);
});

test('wrong methods and unknown actions are refused before anything is read',async()=>{
  expect((await call('','POST')).status).toBe(405);
  expect((await call('/check','GET')).status).toBe(405);
  expect((await call('/settings','POST',DEFAULT_SETTINGS)).status).toBe(405);
  expect((await call('/other','POST')).status).toBe(404);
});

test('with nothing recorded yet the view shows the checkout version and the default settings',async()=>{
  const answer=await call('');
  expect(answer.status).toBe(200);
  const {timezone,...rest}=answer.body.data;
  expect(rest).toEqual({current:{version:'0.1.0',commit:''},available:null,refusals:[],skipped:[],newest:null,checkedAt:null,checkError:null,
    settings:DEFAULT_SETTINGS,last:null,request:null,canRollback:false});
  // Until the supervisor publishes its zone, the console's own process zone is shown.
  expect(timezone.offset).toMatch(/^[+-]\d{2}:\d{2}$/);
  expect(typeof timezone.name).toBe('string');
});

test('the view shows the time zone the supervisor reads the window in, and saving settings returns it',async()=>{
  running();
  expect((await call('')).body.data.timezone).toEqual(ZONE);
  const saved=await call('/settings','PUT',DEFAULT_SETTINGS);
  expect(saved).toEqual({status:200,body:{data:DEFAULT_SETTINGS,timezone:ZONE}});
  write('current.json',{version:'0.1.0',commit:COMMIT,timezone:{name:'x',offset:'4 hours'}});
  expect((await call('')).body.data.timezone).not.toEqual({name:'x',offset:'4 hours'});
});

test('releases the check passed over are information, never a refusal of the one on offer',async()=>{
  running();
  checked(release(),[],COMMIT,{skipped:['v0.3.0 was passed over: v0.3.0 is not signed by a key listed in release-signers'],
    newest:{version:'0.3.0',tag:'v0.3.0',class:'manual',signed:false,reasons:['v0.3.0 is not signed'],extra:1}});
  const {body}=await call('');
  expect(body.data.refusals).toEqual([]);
  expect(body.data.skipped).toEqual(['v0.3.0 was passed over: v0.3.0 is not signed by a key listed in release-signers']);
  expect(body.data.newest).toEqual({version:'0.3.0',tag:'v0.3.0',class:'manual',signed:false,reasons:['v0.3.0 is not signed']});
  expect((await call('/apply','POST',{version:'0.2.0'})).status).toBe(202);
  // With nothing installable, the newest release is still named.
  rmSync(join(directory,'request.json'));
  checked(null,[],COMMIT,{newest:{version:'0.3.0',tag:'v0.3.0',class:null,signed:false,reasons:['v0.3.0 has no release.json']}});
  const none=(await call('')).body.data;
  expect(none.newest).toEqual({version:'0.3.0',tag:'v0.3.0',class:null,signed:false,reasons:['v0.3.0 has no release.json']});
  expect(none.available).toBeNull();
});

test('a release that migrates environment databases installs only with the acknowledgement, which the request carries',async()=>{
  running();checked(release({class:'attended',reasons:['lab/images.lock.json changes the Auth image']}));
  expect((await call('')).body.data.available.class).toBe('attended');
  expect(await call('/apply','POST',{version:'0.2.0'})).toEqual({status:409,body:{message:UPDATE_MESSAGES.acknowledge}});
  expect(await call('/apply','POST',{version:'0.2.0',acknowledged:false})).toEqual({status:409,body:{message:UPDATE_MESSAGES.acknowledge}});
  expect((await call('/apply','POST',{version:'0.2.0',acknowledged:'yes'})).status).toBe(400);
  expect(existsSync(join(directory,'request.json'))).toBe(false);
  expect((await call('/apply','POST',{version:'0.2.0',acknowledged:true})).status).toBe(202);
  expect(file('request.json')).toMatchObject({kind:'apply',version:'0.2.0',acknowledged:true,trigger:'console'});
});

test('a safe release does not record an acknowledgement it never needed',async()=>{
  running();checked();
  expect((await call('/apply','POST',{version:'0.2.0',acknowledged:true})).status).toBe(202);
  expect(file('request.json').acknowledged).toBeUndefined();
});

test('the view maps the check, the upgrade record and the request into the console shape',async()=>{
  running();
  checked(release(),['A backup or restore is running; wait for it to finish']);
  write('check.json',{attempted_at:'2026-09-25T10:00:00+00:00',error:null,failures:0});
  write('state.json',{phase:'confirmed',from:'c'.repeat(40),to:COMMIT,started_at:'2026-09-24T10:00:00+00:00',finished_at:'2026-09-24T10:05:00+00:00',
    automatic:false,trigger:'console',snapshot:'x',release:{version:'0.1.0',tag:'v0.1.0',class:'safe',signed:true}});
  write('last-request.json',{id:'r',kind:'check',trigger:'console',state:'done',requested_at:'2026-09-25T09:59:00+00:00'});
  const {body}=await call('');
  expect(body.data.available).toEqual({version:'0.2.0',tag:'v0.2.0',commit:NEXT,class:'safe',signed:true,reasons:[],
    notes:{en:'Faster start.',ar:'بدء أسرع.'},changes:[{label:'images.lock.json:rest',before:'postgrest:v1',after:'postgrest:v2'}]});
  expect(body.data.refusals).toEqual(['A backup or restore is running; wait for it to finish']);
  expect(body.data.checkedAt).toBe('2026-09-25T10:00:00+00:00');
  expect(body.data.current).toEqual({version:'0.1.0',commit:COMMIT});
  expect(body.data.last).toEqual({phase:'confirmed',from:'c'.repeat(40),to:COMMIT,version:'0.1.0',startedAt:'2026-09-24T10:00:00+00:00',
    finishedAt:'2026-09-24T10:05:00+00:00',automatic:false,trigger:'console'});
  expect(body.data.request).toEqual({kind:'check',state:'done',requestedAt:'2026-09-25T09:59:00+00:00'});
  // The supervisor's verdict was for another upgrade record, so no rollback is offered.
  expect(body.data.canRollback).toBe(false);
});

test('a check made on another version is not shown: it still names the release just installed',async()=>{
  running();
  checked(release(),[], 'd'.repeat(40));
  write('check.json',{attempted_at:'2026-09-25T10:00:00+00:00',error:'The release source could not be read: offline',failures:2});
  const {body}=await call('');
  expect(body.data.available).toBeNull();
  expect(body.data.refusals).toEqual([]);
  expect(body.data.checkError).toBe('The release source could not be read: offline');
});

test('an apply is recorded once, privately, and a second one waits for the first',async()=>{
  running();checked();
  const first=await call('/apply','POST',{version:'0.2.0'});
  expect(first.status).toBe(202);
  const request=file('request.json');
  expect(request).toMatchObject({kind:'apply',version:'0.2.0',tag:'v0.2.0',trigger:'console',state:'requested'});
  expect(statSync(join(directory,'request.json')).mode&0o777).toBe(0o600);
  expect(statSync(directory).mode&0o777).toBe(0o700);
  expect(readdirSync(directory).filter(name=>name.endsWith('.tmp'))).toEqual([]);
  const second=await call('/apply','POST',{version:'0.2.0'});
  expect(second).toEqual({status:409,body:{message:UPDATE_MESSAGES.busy}});
  expect((await call('/check','POST')).body.message).toBe(UPDATE_MESSAGES.busy);
  expect(file('request.json').id).toBe(request.id);
  // The view shows it under way.
  expect((await call('')).body.data.request).toEqual({kind:'apply',version:'0.2.0',state:'requested',requestedAt:request.requested_at});
});

test('an apply is refused with a sentence unless the checked release is safe, signed and clear',async()=>{
  running();
  expect((await call('/apply','POST',{version:'0.2.0'})).body.message).toBe(UPDATE_MESSAGES.nothing);
  checked(release(),[],'d'.repeat(40));
  expect((await call('/apply','POST',{version:'0.2.0'})).body.message).toBe(UPDATE_MESSAGES.stale);
  checked();
  expect((await call('/apply','POST',{version:'0.3.0'})).body.message).toBe(UPDATE_MESSAGES.other);
  checked(release({class:'rebuild'}));
  expect((await call('/apply','POST',{version:'0.2.0'})).body.message).toBe(UPDATE_MESSAGES.class);
  checked(release({signed:false}));
  expect((await call('/apply','POST',{version:'0.2.0'})).body.message).toBe(UPDATE_MESSAGES.unsigned);
  checked(release(),['A backup or restore is running; wait for it to finish']);
  expect((await call('/apply','POST',{version:'0.2.0'})).body.message).toBe(UPDATE_MESSAGES.refused);
  checked();
  write('state.json',{phase:'applied',from:'c'.repeat(40),to:COMMIT,started_at:'2026-09-24T10:00:00+00:00'});
  const pending=await call('/apply','POST',{version:'0.2.0'});
  expect(pending).toEqual({status:409,body:{message:UPDATE_MESSAGES.pending}});
  expect(existsSync(join(directory,'request.json'))).toBe(false);
});

test('an apply needs exactly a version, and check and rollback take no body',async()=>{
  running();checked();
  expect((await call('/apply','POST',{})).status).toBe(400);
  expect((await call('/apply','POST',{version:'0.2.0',force:true})).status).toBe(400);
  expect((await call('/apply','POST',{version:'latest'})).status).toBe(400);
  expect((await call('/check','POST',{})).status).toBe(400);
  expect((await call('/rollback','POST',{})).status).toBe(400);
  expect(existsSync(join(directory,'request.json'))).toBe(false);
});

test('a check is recorded even when periodic checking is off',async()=>{
  write('settings.json',{check:false,automatic:false,window:{start:'03:00',end:'05:00'}});
  expect((await call('/check','POST')).status).toBe(202);
  expect(file('request.json')).toMatchObject({kind:'check',state:'requested',trigger:'console'});
});

test('a rollback is offered only for a confirmed upgrade the supervisor judged possible',async()=>{
  const state={phase:'confirmed',from:'c'.repeat(40),to:COMMIT,started_at:'2026-09-24T10:00:00+00:00',automatic:false};
  expect((await call('/rollback','POST')).body.message).toBe(UPDATE_MESSAGES.no_rollback);
  write('state.json',state);
  write('current.json',{version:'0.2.0',commit:COMMIT,rollback:{started_at:state.started_at,possible:false,
    reason:'The control catalog is at schema 3, and cccccccccccc opens only up to 2.'}});
  expect((await call('')).body.data.canRollback).toBe(false);
  expect(await call('/rollback','POST')).toEqual({status:409,body:{message:'The control catalog is at schema 3, and cccccccccccc opens only up to 2.'}});
  write('current.json',{version:'0.2.0',commit:COMMIT,rollback:{started_at:state.started_at,possible:true,reason:null}});
  expect((await call('')).body.data.canRollback).toBe(true);
  expect((await call('/rollback','POST')).status).toBe(202);
  expect(file('request.json')).toMatchObject({kind:'rollback',state:'requested'});
  // While it is under way the page no longer offers it.
  expect((await call('')).body.data.canRollback).toBe(false);
  write('state.json',{...state,phase:'rolled_back'});
  rmSync(join(directory,'request.json'));
  expect((await call('')).body.data.canRollback).toBe(false);
});

test('settings are validated strictly and saved privately',async()=>{
  const saved=await call('/settings','PUT',{check:true,automatic:true,window:{start:'23:00',end:'01:00'}});
  expect(saved.status).toBe(200);
  expect(saved.body.data).toEqual({check:true,automatic:true,window:{start:'23:00',end:'01:00'}});
  expect(file('settings.json')).toEqual({check:true,automatic:true,window:{start:'23:00',end:'01:00'}});
  expect(statSync(join(directory,'settings.json')).mode&0o777).toBe(0o600);
  expect((await call('')).body.data.settings).toEqual({check:true,automatic:true,window:{start:'23:00',end:'01:00'}});
  const refused:[unknown,string][]=[
    [{check:true,automatic:false,window:{start:'03:00',end:'03:00'}},'The maintenance window needs different start and end times.'],
    [{check:true,automatic:false,window:{start:'24:00',end:'03:00'}},'The maintenance window needs a valid start and end time.'],
    [{check:true,automatic:false,window:{start:'3:00',end:'05:00'}},'The maintenance window needs a valid start and end time.'],
    [{check:true,automatic:false,window:{start:'03:00',end:'05:00',days:7}},'The maintenance window needs a start and an end.'],
    [{check:'yes',automatic:false,window:{start:'03:00',end:'05:00'}},'Check and automatic must each be on or off.'],
    [{check:false,automatic:true,window:{start:'03:00',end:'05:00'}},'Automatic updates need checking for new releases turned on.'],
  ];
  for(const [input,message] of refused)expect(await call('/settings','PUT',input)).toEqual({status:400,body:{message}});
  expect((await call('/settings','PUT',{check:true,automatic:false})).status).toBe(400);
  expect(file('settings.json')).toEqual({check:true,automatic:true,window:{start:'23:00',end:'01:00'}});
});

test('a damaged settings file reads as the defaults, so it never turns automatic updates on',async()=>{
  writeFileSync(join(directory,'settings.json'),'{"check":true,"automatic":true');
  expect((await call('')).body.data.settings).toEqual(DEFAULT_SETTINGS);
});
