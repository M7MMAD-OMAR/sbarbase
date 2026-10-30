import {test,expect} from 'bun:test';
import {chmodSync,existsSync,mkdtempSync,readFileSync,renameSync,rmSync,statSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {readSettings,signInHandler,settingsFromInput,SettingsError,changedKeys} from '../src/control/sign-in';

const valid={site_url:'https://app.example.com',redirect_urls:['https://app.example.com/**','myapp://callback'],signup:true,anonymous:false,
 providers:{github:{enabled:true,client_id:'Iv1.abc',secret:'s3cret',url:''}}};

test('settings are checked field by field, and an empty secret keeps the saved one',()=>{
 const first=settingsFromInput(valid,null);
 expect(first.revision).toBe(1);
 const again=settingsFromInput({...valid,providers:{github:{enabled:true,client_id:'Iv1.abc',secret:''}}},first);
 expect(again.providers.github!.secret).toBe('s3cret');
 expect(again.revision).toBe(2);
 for(const [input,field] of [
  [{...valid,site_url:'javascript:alert(1)'},'site_url'],
  [{...valid,site_url:'https://user:pass@app.example.com'},'site_url'],
  [{...valid,redirect_urls:['https://a.example.com,https://b.example.com']},'redirect_urls'],
  [{...valid,redirect_urls:['no-scheme']},'redirect_urls'],
  [{...valid,providers:{myspace:{enabled:true}}},'providers'],
  [{...valid,providers:{github:{enabled:true,client_id:'',secret:'x'}}},'github'],
  [{...valid,providers:{keycloak:{enabled:true,client_id:'a',secret:'b'}}},'keycloak'],
  [{...valid,providers:{github:{enabled:true,client_id:'a',secret:'b',url:'https://x.example.com'}}},'github'],
  [{...valid,extra:true},'settings'],
 ] as const) {
  try{settingsFromInput(input,null);throw new Error('accepted '+field);}
  catch(error){expect(error).toBeInstanceOf(SettingsError);expect((error as Error).message).toBe(field);}
 }
});

test('owners and admins save sign-in settings; the secret goes to a private file and never comes back',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const call=(actor:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol')).status).toBe(403);
  expect((await call('bob','PUT',valid)).status).toBe(403);
  expect((await call('alice','DELETE')).status).toBe(405);
  const empty=await (await call('alice')).json() as {data:{state:string;settings:unknown;callback_url:string}};
  expect(empty.data.state).toBe('unconfigured');expect(empty.data.settings).toBeNull();
  const runtime=catalog.getProvision('alice',environment).runtime;
  expect(empty.data.callback_url).toBe(`http://localhost/${runtime}/auth/v1/callback`);
  expect((await call('alice','PUT',{...valid,site_url:'ftp://x'})).status).toBe(400);
  const saved=await call('alice','PUT',valid);
  expect(saved.status).toBe(202);
  const text=await saved.text();
  expect(text).not.toContain('s3cret');
  const body=JSON.parse(text) as {data:{state:string;revision:number;settings:{providers:{github:{secret_set:boolean}}}}};
  expect(body.data.state).toBe('pending');expect(body.data.revision).toBe(1);
  expect(body.data.settings.providers.github.secret_set).toBe(true);
  const file=join(directory,runtime+'-auth.json');
  expect(statSync(file).mode&0o777).toBe(0o600);
  expect(JSON.parse(readFileSync(file,'utf8')).providers.github.secret).toBe('s3cret');
  expect(await (await call('alice')).text()).not.toContain('s3cret');
 } finally {catalog.close();}
});

const lifetime={access_token_seconds:1800,timebox_seconds:7200,inactivity_seconds:600};

test('the three session lifetimes are saved, read back, and kept by a save that omits them',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const call=(method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method,headers:{authorization:'alice','content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const file=join(directory,catalog.getProvision('alice',environment).runtime+'-auth.json');
  const answer=async()=>await (await call()).json() as {data:{settings:{session:unknown}}};
  expect((await call('PUT',{...valid,session:lifetime})).status).toBe(202);
  expect(JSON.parse(readFileSync(file,'utf8')).session).toEqual(lifetime);
  expect((await answer()).data.settings.session).toEqual(lifetime);
  // A console save that does not touch sessions must not wipe them.
  expect((await call('PUT',valid)).status).toBe(202);
  expect((await answer()).data.settings.session).toEqual(lifetime);
  expect(JSON.parse(readFileSync(file,'utf8')).session).toEqual(lifetime);
 } finally {catalog.close();}
});

test('each session lifetime is checked against its own range, and no cap is a null',()=>{
 for(const [session,field] of [
  [{access_token_seconds:59},'access_token_seconds'],
  [{access_token_seconds:86401},'access_token_seconds'],
  [{access_token_seconds:true},'access_token_seconds'],
  [{timebox_seconds:3599},'timebox_seconds'],
  [{timebox_seconds:'7200'},'timebox_seconds'],
  [{timebox_seconds:315360001},'timebox_seconds'],
  [{inactivity_seconds:0},'inactivity_seconds'],
  [{inactivity_seconds:299},'inactivity_seconds'],
  [{inactivity_seconds:315360001},'inactivity_seconds'],
  [{access_token_seconds:3600,unknown_seconds:1},'unknown_seconds'],
 ] as const) {
  try{settingsFromInput({...valid,session},null);throw new Error('accepted '+field);}
  catch(error){expect(error).toBeInstanceOf(SettingsError);expect((error as Error).message).toBe(field);}
 }
 const bounds=settingsFromInput({...valid,session:{access_token_seconds:60,timebox_seconds:3600,inactivity_seconds:300}},null);
 expect(bounds.session).toEqual({access_token_seconds:60,timebox_seconds:3600,inactivity_seconds:300});
 const noCap=settingsFromInput({...valid,session:{access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null}},null);
 expect(noCap.session).toEqual({access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null});
});

test('a saved file with no session key reads back as three nulls and is left untouched',()=>{
 const directory=mkdtempSync(join(tmpdir(),'sign-in-')),runtime='e_'+'a'.repeat(24);
 const file=join(directory,runtime+'-auth.json');
 const before=JSON.stringify({revision:1,site_url:'https://app.example.com',redirect_urls:[],signup:true,anonymous:false,providers:{}});
 writeFileSync(file,before,{mode:0o600});
 expect(readSettings(directory,runtime)!.session).toEqual({access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null});
 expect(readFileSync(file,'utf8')).toBe(before);
});

test('a save made from an older page is refused, and a link or open file is not read',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put({...valid,base_revision:0})).status).toBe(202);
  // Two pages opened at revision 0: the second save carries a base the first one already moved.
  expect((await put({...valid,base_revision:0})).status).toBe(409);
  expect((await put({...valid,base_revision:1})).status).toBe(202);
  expect((await put(valid)).status).toBe(202);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const file=join(directory,runtime+'-auth.json');
  chmodSync(file,0o644);
  expect(()=>readSettings(directory,runtime)).toThrow(SettingsError);
  chmodSync(file,0o600);
  expect(readSettings(directory,runtime)!.revision).toBe(3);
  const target=join(directory,'elsewhere.json');renameSync(file,target);symlinkSync(target,file);
  expect(()=>readSettings(directory,runtime)).toThrow(SettingsError);
 } finally {catalog.close();}
});

test('a dangling link and a newer file version are refused, and the console says so',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const call=(method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method,headers:{authorization:'alice','content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const file=join(directory,runtime+'-auth.json');
  symlinkSync(join(directory,'missing.json'),file);
  expect(()=>readSettings(directory,runtime)).toThrow(SettingsError);
  const refused=await call();
  expect(refused.status).toBe(409);expect((await refused.json() as {message:string}).message).toContain('cannot be read on the server');
  rmSync(file);
  writeFileSync(file,JSON.stringify({...valid,version:3,revision:1}),{mode:0o600});
  expect(()=>readSettings(directory,runtime)).toThrow(SettingsError);
  expect((await call('PUT',valid)).status).toBe(409);
  rmSync(file);
  expect((await call('PUT',valid)).status).toBe(202);
  // No version 2 switch is on, so the file keeps the shape a build from before them reads.
  const written=JSON.parse(readFileSync(file,'utf8'));
  expect(written.version).toBe(1);expect('manual_linking' in written).toBe(false);expect('skip_nonce_check' in written.providers.github).toBe(false);
 } finally {catalog.close();}
});

test('the audit lists the platform key names a save changed, never a value',async()=>{
 const first=settingsFromInput({...valid,manual_linking:true},null);
 const second=settingsFromInput({...valid,site_url:'https://b.example.com',manual_linking:false,
  providers:{github:{enabled:true,client_id:'Iv1.abc',secret:'a-new-secret'},apple:{enabled:true,client_id:'x',secret:'y',skip_nonce_check:true}}},first);
 const changed=changedKeys(first,second);
 expect(changed).toEqual(['SITE_URL','SECURITY_MANUAL_LINKING_ENABLED','EXTERNAL_GITHUB_SECRET','EXTERNAL_APPLE_ENABLED','EXTERNAL_APPLE_CLIENT_ID',
  'EXTERNAL_APPLE_SECRET','EXTERNAL_APPLE_SKIP_NONCE_CHECK']);
 expect(JSON.stringify(changed)).not.toContain('a-new-secret');
 expect(changedKeys(first,first)).toEqual([]);
 // An omitted switch keeps what is saved, like the session lifetimes.
 const kept=settingsFromInput({...valid,providers:{github:{enabled:true,client_id:'Iv1.abc',secret:''},apple:{enabled:true,client_id:'x',secret:''}}},second);
 expect(kept.manual_linking).toBe(false);expect(kept.providers.apple!.skip_nonce_check).toBe(true);
});

test('saves of one environment are spaced, and the audit event carries the changed names only',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,60_000);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put(valid)).status).toBe(202);
  const again=await put({...valid,manual_linking:true});
  expect(again.status).toBe(429);
  const events=catalog.auditEvents('alice',org,50).filter(event=>event.action==='sign_in.saved');
  expect(events.length).toBe(1);
  const detail=JSON.stringify(events[0]);
  expect(detail).toContain('EXTERNAL_GITHUB_SECRET');expect(detail).not.toContain('s3cret');
 } finally {catalog.close();}
});

test('google takes a tidy list of client IDs, the nonce switch is for apple and google only, and a stepped back clock never locks a save out',async()=>{
 const base={...valid,providers:{google:{enabled:true,client_id:'a.apps.example, b.apps.example',secret:'s'}}};
 expect(settingsFromInput(base,null).providers.google!.client_id).toBe('a.apps.example,b.apps.example');
 for(const id of ['a,,b','a,','a b'])expect(()=>settingsFromInput({...base,providers:{google:{enabled:true,client_id:id,secret:'s'}}},null)).toThrow(SettingsError);
 expect(()=>settingsFromInput({...valid,providers:{github:{enabled:true,client_id:'a',secret:'s',skip_nonce_check:true}}},null)).toThrow(SettingsError);
 expect(settingsFromInput({...valid,providers:{apple:{enabled:true,client_id:'a',secret:'s',skip_nonce_check:true}}},null).providers.apple!.skip_nonce_check).toBe(true);
 // Any switch on writes the version 2 shape; changedKeys never grows without bound.
 const many=Object.fromEntries(['apple','azure','bitbucket','discord','facebook','figma','github','gitlab','google','kakao','linkedin_oidc','notion','slack_oidc','spotify','twitch','twitter','zoom'].map(name=>[name,{enabled:true,client_id:'c',secret:'s'}]));
 const big=settingsFromInput({...valid,providers:many},null);
 expect(changedKeys(null,big).length).toBeLessThanOrEqual(61);

 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,60_000);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put(valid)).status).toBe(202);
  const refused=await put(valid);
  expect(refused.status).toBe(429);expect(Number(refused.headers.get('retry-after'))).toBeGreaterThan(0);
  // The clock stepped back an hour: the row now looks like it was saved in the future.
  const real=Date.now;Date.now=()=>real()-3_600_000;
  try{expect((await put(valid)).status).toBe(202);}finally{Date.now=real;}
 } finally {catalog.close();}
});

test('a file with only the version 2 switches carries no config key',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,
   {method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put({...valid,manual_linking:true})).status).toBe(202);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const written=JSON.parse(readFileSync(join(directory,runtime+'-auth.json'),'utf8'));
  expect(written.version).toBe(2);expect(written.manual_linking).toBe(true);expect('config' in written).toBe(false);
  expect((await put({...valid,manual_linking:true,config:{SESSIONS_SINGLE_PER_USER:true}})).status).toBe(202);
  expect(JSON.parse(readFileSync(join(directory,runtime+'-auth.json'),'utf8')).config).toEqual({SESSIONS_SINGLE_PER_USER:true});
 } finally {catalog.close();}
});

test('the simple settings of the registry are validated by type and range, kept when omitted, cleared by null, and audited by name',async()=>{
 const first=settingsFromInput({...valid,config:{SESSIONS_SINGLE_PER_USER:true,MFA_MAX_ENROLLED_FACTORS:20}},null);
 expect(first.config).toEqual({SESSIONS_SINGLE_PER_USER:true,MFA_MAX_ENROLLED_FACTORS:20});
 // Omitted object or member: kept. Null: cleared.
 expect(settingsFromInput(valid,first).config).toEqual(first.config);
 const second=settingsFromInput({...valid,config:{MFA_MAX_ENROLLED_FACTORS:null,PASSWORD_MIN_LENGTH:10}},first);
 expect(second.config).toEqual({SESSIONS_SINGLE_PER_USER:true,PASSWORD_MIN_LENGTH:10});
 expect(changedKeys(first,second)).toEqual(['PASSWORD_MIN_LENGTH','MFA_MAX_ENROLLED_FACTORS']);
 for(const config of [{SESSIONS_SINGLE_PER_USER:1},{MFA_MAX_ENROLLED_FACTORS:0},{MFA_MAX_ENROLLED_FACTORS:1.5},{PASSWORD_MIN_LENGTH:5},{PASSWORD_MIN_LENGTH:73},{NOT_A_KEY:1},[]])
  expect(()=>settingsFromInput({...valid,config},null)).toThrow(SettingsError);
 // A retired key is ignored on input and dropped from a saved one, not refused.
 expect(settingsFromInput({...valid,config:{RATE_LIMIT_WEB3:20,SESSIONS_SINGLE_PER_USER:true}},null).config).toEqual({SESSIONS_SINGLE_PER_USER:true});
 expect(settingsFromInput(valid,{...first,config:{...first.config,RATE_LIMIT_WEB3:5}} as never).config).toEqual(first.config);
});

test('turning SAML on makes the environment signing key once, a refused save makes none, and off keeps it',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'sign-in-saml-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sign-in`,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  const key=join(directory,runtime+'-saml.json');
  expect((await put({...valid,config:{SAML_ENABLED:'yes'}})).status).toBe(400);
  expect(existsSync(key)).toBe(false);
  expect((await put({...valid,config:{SAML_ENABLED:true}})).status).toBe(202);
  const made=readFileSync(key,'utf8');
  expect(statSync(key).mode&0o777).toBe(0o600);
  expect((await put({...valid,config:{SAML_ENABLED:false}})).status).toBe(202);
  expect(readFileSync(key,'utf8')).toBe(made);
  expect((await put({...valid,config:{SAML_ENABLED:true}})).status).toBe(202);
  expect(readFileSync(key,'utf8')).toBe(made);
  // The key is in no answer or audit row.
  const answer=JSON.stringify(await (await handler(new Request(`http://local/management/v1/environments/${environment}/auth-config`,{headers:{authorization:'alice'}}))).json())+JSON.stringify(catalog.auditEvents('alice',org));
  expect(answer).not.toContain(JSON.parse(made).private_key);
 } finally {catalog.close();}
});
