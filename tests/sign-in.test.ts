import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {signInHandler,settingsFromInput,SettingsError} from '../src/control/sign-in';

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
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory);
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
