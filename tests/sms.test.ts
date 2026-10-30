import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,existsSync,writeFileSync as write,symlinkSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {smsHandler,smsFromInput,smsView,changedSmsKeys,readSms,SmsError,PROVIDERS} from '../src/control/sms';

const twilio={provider:'twilio',credentials:{account_sid:'AC123',auth_token:'THE-TWILIO-TOKEN',message_service_sid:'MG123'}};

test('the input becomes a complete file, a blank secret keeps the saved one only for the same provider, and bad input is refused',()=>{
 const first=smsFromInput({...twilio,phone_confirmations:false,rate_limit_sms_sent:5},null);
 expect(first).toEqual({version:1,enabled:true,provider:'twilio',autoconfirm:true,rate_limit_sms_sent:5,twilio:{account_sid:'AC123',auth_token:'THE-TWILIO-TOKEN',message_service_sid:'MG123'}});
 const kept=smsFromInput({provider:'twilio',credentials:{account_sid:'AC999'}},first);
 expect(kept.twilio).toEqual({account_sid:'AC999',auth_token:'THE-TWILIO-TOKEN',message_service_sid:'MG123'});
 expect(kept).toMatchObject({autoconfirm:false,rate_limit_sms_sent:30});
 // Another provider needs its own secret: the saved Twilio one is not carried over.
 expect(()=>smsFromInput({provider:'vonage',credentials:{api_key:'k',from:'f'}},first)).toThrow(SmsError);
 const vonage=smsFromInput({provider:'vonage',credentials:{api_key:'k',api_secret:'s',from:'f'}},first);
 expect(vonage.vonage).toEqual({api_key:'k',api_secret:'s',from:'f'});expect(vonage.twilio).toBeUndefined();
 for(const bad of [{...twilio,provider:'nope'},{...twilio,credentials:{...twilio.credentials,extra:'x'}},{...twilio,credentials:{...twilio.credentials,auth_token:'a\nb'}},{...twilio,credentials:{...twilio.credentials,auth_token:'x'.repeat(257)}},
  {...twilio,phone_confirmations:'yes'},{...twilio,min_interval_seconds:30},{...twilio,rate_limit_sms_sent:0},{...twilio,extra:1},{provider:'twilio'},[],null])
  expect(()=>smsFromInput(bad,null)).toThrow(SmsError);
 for(const [id,item] of Object.entries(PROVIDERS)){
  const creds=Object.fromEntries(item.fields.map(field=>[field.name,'v-'+field.name]));
  expect(smsFromInput({provider:id,credentials:creds},null)[id]).toEqual(creds);
 }
});

test('the view carries every plain value and only whether each secret is set',()=>{
 const saved=smsFromInput(twilio,null);
 const view=smsView(saved);
 expect(view).toMatchObject({enabled:true,provider:'twilio',phone_confirmations:true,rate_limit_sms_sent:30,fields:{account_sid:'AC123',message_service_sid:'MG123'},secrets_set:{auth_token:true}});
 expect(JSON.stringify(view)).not.toContain('THE-TWILIO-TOKEN');
 expect(smsView(null)).toMatchObject({enabled:false,provider:'twilio',secrets_set:{auth_token:false}});
 expect(changedSmsKeys(null,saved)).toEqual(['EXTERNAL_PHONE_ENABLED','SMS_PROVIDER']);
 expect(changedSmsKeys(saved,smsFromInput({...twilio,credentials:{auth_token:'NEW'},rate_limit_sms_sent:9},saved))).toEqual(['RATE_LIMIT_SMS_SENT','SMS_TWILIO_AUTH_TOKEN']);
 expect(changedSmsKeys(saved,null)).toEqual(['EXTERNAL_PHONE_ENABLED','SMS_PROVIDER']);
 expect(changedSmsKeys(null,null)).toEqual([]);
});

test('the route: owners and admins only, a private file, no secret in any answer or audit row, off removes the file, a bad file is refused',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'sms-si-')),dir=mkdtempSync(join(tmpdir(),'sms-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const project=catalog.createProject('alice',org,'P');
  const first=catalog.createEnvironment('alice',project,'production'),second=catalog.createEnvironment('alice',project,'staging');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const handler=smsHandler(catalog,async request=>request.headers.get('authorization'),signIn,dir,0);
  const call=(actor:string,environment:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/sms`,{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('carol',first)).status).toBe(403);expect((await call('bob',first)).status).toBe(403);
  expect((await call('alice',first,'DELETE')).status).toBe(405);
  expect((await call('alice',first,'PUT',{enabled:'x'})).status).toBe(400);
  expect((await call('alice',first,'PUT',{...twilio,credentials:{account_sid:'AC1'}})).status).toBe(400);
  const runtime=catalog.getProvision('alice',first).runtime,other=catalog.getProvision('alice',second).runtime;
  const path=join(dir,runtime+'-sms.json');
  expect(existsSync(path)).toBe(false);
  const saved=await call('alice',first,'PUT',{...twilio,base_revision:(await (await call('alice',first)).json() as {data:{revision:number}}).data.revision});
  expect(saved.status).toBe(202);
  expect(statSync(path).mode&0o777).toBe(0o600);
  const text=JSON.stringify(await saved.json())+JSON.stringify(await (await call('alice',first)).json())+JSON.stringify(catalog.auditEvents('alice',org));
  expect(text).not.toContain('THE-TWILIO-TOKEN');
  expect(text).toContain('EXTERNAL_PHONE_ENABLED');
  expect(readFileSync(path,'utf8')).toContain('THE-TWILIO-TOKEN');
  // Another environment is untouched.
  const two=await (await call('alice',second)).json() as {data:{sms:{enabled:boolean}}};
  expect(two.data.sms.enabled).toBe(false);expect(existsSync(join(dir,other+'-sms.json'))).toBe(false);
  // A stale page is refused.
  expect((await call('alice',first,'PUT',{...twilio,base_revision:0})).status).toBe(409);
  // A linked or open file is refused, not read.
  const now=await (await call('alice',first)).json() as {data:{revision:number}};
  chmodSync(path,0o644);expect((await call('alice',first)).status).toBe(409);chmodSync(path,0o600);
  // Off removes the file and the secret with it.
  expect((await call('alice',first,'PUT',{enabled:false,base_revision:now.data.revision})).status).toBe(202);
  expect(existsSync(path)).toBe(false);
  rmSync(path,{force:true});symlinkSync('/etc/hostname',path);
  expect(()=>readSms(dir,runtime)).toThrow(SmsError);
 } finally {catalog.close();}
});
