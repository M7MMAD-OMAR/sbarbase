import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,existsSync,writeFileSync as write,chmodSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {smtpHandler,mailFromInput,changedSmtpKeys,SmtpError,readMail,smtpView} from '../src/control/smtp';
import {readSettings,settingsFromInput} from '../src/control/sign-in';

const cloud={enabled:true,admin_email:'noreply@example.com',sender_name:'Example',host:'smtp.example.com',port:587,user:'apikey',password:'THE-SMTP-SECRET'};

test('the console fields become a complete mail file, an empty password keeps the saved one, other keys are kept',()=>{
 const first=mailFromInput(cloud,null);
 expect(first).toMatchObject({host:'smtp.example.com',port:587,user:'apikey',pass:'THE-SMTP-SECRET',admin_email:'noreply@example.com',sender_name:'Example',
  autoconfirm:false,max_frequency:'60s',otp_length:6,secure_email_change:true,magic_link_template:''});
 const edited={...first,autoconfirm:true,max_frequency:'5s'};
 const second=mailFromInput({...cloud,password:'',host:'smtp2.example.com'},edited);
 expect(second.pass).toBe('THE-SMTP-SECRET');expect(second.host).toBe('smtp2.example.com');
 expect(second.autoconfirm).toBe(true);expect(second.max_frequency).toBe('5s');
 for(const change of [{host:'smtp://x'},{host:'a b'},{host:''},{port:0},{port:70000},{port:'587'},{admin_email:'no-at-sign'},{admin_email:'a b@x.co'},
  {admin_email:'<a@b.co>'},{sender_name:'a<b'},{sender_name:'a\nb'},{user:'a\nb'},{password:'a\nb'},{extra:1}])
  expect(()=>mailFromInput({...cloud,...change},null)).toThrow(SmtpError);
 expect(()=>mailFromInput({...cloud,password:''},null)).toThrow(SmtpError);
});

test('field sizes are capped so the file stays loadable, and a non boolean enabled is refused',()=>{
 for(const change of [{password:'x'.repeat(1025)},{host:'h'.repeat(254)},{user:'u'.repeat(513)},{sender_name:'n'.repeat(201)},{admin_email:'a'.repeat(250)+'@example.com'}])
  expect(()=>mailFromInput({...cloud,...change},null)).toThrow(SmtpError);
 // A host written file with many extra bytes cannot be carried over past the loader's limit.
 expect(()=>mailFromInput(cloud,{magic_link_template:'/'+'a'.repeat(8300)})).toThrow(SmtpError);
});

test('a failed apply restores the prior mail file, the first copy is kept across saves, and nothing is written when the sign-in file cannot be read',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'smtp-si-')),mail=mkdtempSync(join(tmpdir(),'smtp-ml-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const handler=smtpHandler(catalog,async request=>request.headers.get('authorization'),signIn,mail,0);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/smtp`,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  // A sign-in file that cannot be read: refused before the mail file changes.
  const bad=join(signIn,runtime+'-auth.json');write(bad,'{}',{mode:0o644});
  expect((await put(cloud)).status).toBe(409);expect(existsSync(join(mail,runtime+'-mail.json'))).toBe(false);
  rmSync(bad);
  expect((await put(cloud)).status).toBe(202);
  expect(readFileSync(join(mail,runtime+'-mail.prior'),'utf8')).toBe('none');
  expect((await put({...cloud,host:'two.example.com'})).status).toBe(202);
  expect(readFileSync(join(mail,runtime+'-mail.prior'),'utf8')).toBe('none');
  expect((await put({enabled:'false'})).status).toBe(400);
 } finally {catalog.close();}
});

test('Confirm email is the opposite of autoconfirm and the mail limits are bounded whole numbers',()=>{
 const on=mailFromInput({...cloud,confirm_email:false,rate_limit_email_sent:100,rate_limit_otp:20,rate_limit_verify:40,min_interval_seconds:30},null);
 expect(on).toMatchObject({autoconfirm:true,rate_limit_email_sent:100,rate_limit_otp:20,rate_limit_verify:40,max_frequency:'30s'});
 const view=smtpView(on);
 expect(view).toMatchObject({confirm_email:false,rate_limit_email_sent:100,min_interval_seconds:30});
 expect(smtpView(null)).toMatchObject({confirm_email:true,rate_limit_email_sent:30,min_interval_seconds:60});
 // Omitted: kept. A file the console does not shape (a duration like 1h30m) shows no interval.
 expect(mailFromInput(cloud,on).autoconfirm).toBe(true);
 expect(smtpView({...on,max_frequency:'1h30m'}).min_interval_seconds).toBeNull();
 for(const change of [{confirm_email:'no'},{rate_limit_email_sent:0},{rate_limit_otp:1.5},{rate_limit_verify:2_000_000},{min_interval_seconds:0},{min_interval_seconds:90_000}])
  expect(()=>mailFromInput({...cloud,...change},null)).toThrow(SmtpError);
 expect(changedSmtpKeys(mailFromInput(cloud,null),on)).toEqual(['MAILER_AUTOCONFIRM','RATE_LIMIT_EMAIL_SENT','RATE_LIMIT_OTP','RATE_LIMIT_VERIFY','SMTP_MAX_FREQUENCY']);
});

test('a limit the host wrote as text shows as the number, a form with a duration shows as unset, and writing the same number is no change',()=>{
 const host={...mailFromInput(cloud,null),rate_limit_email_sent:'100'};
 expect(smtpView(host).rate_limit_email_sent).toBe(100);
 expect(smtpView({...host,rate_limit_email_sent:'30/1h'}).rate_limit_email_sent).toBeNull();
 const same=mailFromInput({...cloud,rate_limit_email_sent:100},host);
 expect(changedSmtpKeys(host,same)).toEqual([]);
 // First enabling with no Confirm email field: confirmation stays required.
 expect(mailFromInput(cloud,null).autoconfirm).toBe(false);
});

test('the audit names what changed and never a value',()=>{
 const a=mailFromInput(cloud,null),b=mailFromInput({...cloud,host:'other.example.com',password:'NEW'},a);
 expect(changedSmtpKeys(a,b)).toEqual(['SMTP_HOST','SMTP_PASS']);
 expect(changedSmtpKeys(null,a).length).toBe(6);expect(changedSmtpKeys(a,a)).toEqual([]);
 expect(JSON.stringify(changedSmtpKeys(a,b))).not.toContain('NEW');
});

test('owners and admins save custom SMTP; the password goes to a private file and never comes back; the sign-in revision moves with it',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'smtp-si-')),mail=mkdtempSync(join(tmpdir(),'smtp-ml-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const other=catalog.createEnvironment('alice',catalog.createProject('alice',org,'Q'),'production');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const handler=smtpHandler(catalog,async request=>request.headers.get('authorization'),signIn,mail,0);
  const url=(id:string)=>`http://local/management/v1/environments/${id}/smtp`;
  const call=(actor:string,id:string,method='GET',body?:unknown)=>handler(new Request(url(id),{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('carol',environment)).status).toBe(403);
  expect((await call('bob',environment,'PUT',cloud)).status).toBe(403);
  expect((await call('alice',environment,'DELETE')).status).toBe(405);
  expect((await call('alice',environment,'PUT',{...cloud,port:0})).status).toBe(400);
  const runtime=catalog.getProvision('alice',environment).runtime,otherRuntime=catalog.getProvision('alice',other).runtime;
  // A sign-in file at revision 4 exists: the save moves it to 5 and the row to 5, so the apply can mark it done.
  write(join(signIn,runtime+'-auth.json'),JSON.stringify(settingsFromInput({site_url:'https://a.example.com'},null)),{mode:0o600});
  const saved=await call('alice',environment,'PUT',cloud);
  expect(saved.status).toBe(202);
  const text=await saved.text();
  expect(text).not.toContain('THE-SMTP-SECRET');
  const body=JSON.parse(text) as {data:{state:string;revision:number;smtp:{password_set:boolean;host:string}}};
  expect(body.data.state).toBe('pending');expect(body.data.smtp).toMatchObject({password_set:true,host:'smtp.example.com'});
  expect(readSettings(signIn,runtime)!.revision).toBe(body.data.revision);
  const file=join(mail,runtime+'-mail.json');
  expect(statSync(file).mode&0o777).toBe(0o600);
  expect(JSON.parse(readFileSync(file,'utf8')).pass).toBe('THE-SMTP-SECRET');
  expect(await (await call('alice',environment)).text()).not.toContain('THE-SMTP-SECRET');
  // The other environment has nothing, and its answer holds none of this one's values.
  const theirs=await (await call('alice',other)).text();
  expect(theirs).not.toContain('smtp.example.com');expect(existsSync(join(mail,otherRuntime+'-mail.json'))).toBe(false);
  expect(JSON.parse(theirs).data.smtp.enabled).toBe(false);
  // Off removes the file, including the password.
  expect((await call('alice',environment,'PUT',{enabled:false})).status).toBe(202);
  expect(existsSync(file)).toBe(false);
  const events=JSON.stringify(catalog.auditEvents('alice',org,50));
  expect(events).toContain('SMTP_HOST');expect(events).not.toContain('THE-SMTP-SECRET');
 } finally {catalog.close();}
});

test('a stale page is refused, saves are spaced, and a linked or open mail file is not read',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'smtp-si-')),mail=mkdtempSync(join(tmpdir(),'smtp-ml-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime;
  const slow=smtpHandler(catalog,async request=>request.headers.get('authorization'),signIn,mail,60_000);
  const put=(body:unknown)=>slow(new Request(`http://local/management/v1/environments/${environment}/smtp`,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put({...cloud,base_revision:5})).status).toBe(409);
  expect((await put({...cloud,base_revision:0})).status).toBe(202);
  expect((await put(cloud)).status).toBe(429);
  const file=join(mail,runtime+'-mail.json');
  chmodSync(file,0o644);expect(()=>readMail(mail,runtime)).toThrow(SmtpError);
  chmodSync(file,0o600);rmSync(file);symlinkSync(join(mail,'x'),file);expect(()=>readMail(mail,runtime)).toThrow(SmtpError);
 } finally {catalog.close();}
});
