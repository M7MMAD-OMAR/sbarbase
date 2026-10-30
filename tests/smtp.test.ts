import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,existsSync,writeFileSync as write,chmodSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {smtpHandler,mailFromInput,changedSmtpKeys,SmtpError,readMail,smtpView,sharedFromInput,sharedSettings} from '../src/control/smtp';
import {readSettings,settingsFromInput} from '../src/control/sign-in';

const cloud={enabled:true,admin_email:'noreply@example.com',sender_name:'Example',host:'smtp.example.com',port:587,user:'apikey',password:'THE-SMTP-SECRET'};

test('the console fields become a complete mail file, an empty password keeps the saved one, other keys are kept',()=>{
 const first=mailFromInput(cloud,null);
 expect(first).toMatchObject({host:'smtp.example.com',port:587,user:'apikey',pass:'THE-SMTP-SECRET',admin_email:'noreply@example.com',sender_name:'Example',
  otp_length:6,secure_email_change:true,magic_link_template:''});
 // Confirm email and the limits are sign-in settings: a new file does not carry them.
 for(const key of ['autoconfirm','max_frequency','rate_limit_email_sent','rate_limit_otp','rate_limit_verify'])expect(first).not.toHaveProperty(key);
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

test('Confirm email is the opposite of autoconfirm, kept in the sign-in settings, and the limits are bounded whole numbers',()=>{
 const given=sharedFromInput({confirm_email:false,rate_limit_email_sent:100,rate_limit_otp:20,rate_limit_verify:40,min_interval_seconds:30});
 expect(given).toEqual({MAILER_AUTOCONFIRM:true,RATE_LIMIT_EMAIL_SENT:100,RATE_LIMIT_OTP:20,RATE_LIMIT_VERIFY:40,SMTP_MAX_FREQUENCY:30});
 expect(sharedFromInput({confirm_email:true,rate_limit_otp:null})).toEqual({MAILER_AUTOCONFIRM:false,RATE_LIMIT_OTP:null});
 expect(sharedFromInput({})).toEqual({});
 // Nothing is written to the mail file, and a request that carries them is still accepted by it.
 expect(mailFromInput({...cloud,confirm_email:false,rate_limit_otp:20},null)).not.toHaveProperty('autoconfirm');
 const view=smtpView(mailFromInput(cloud,null),{MAILER_AUTOCONFIRM:true,RATE_LIMIT_EMAIL_SENT:100,SMTP_MAX_FREQUENCY:30});
 expect(view).toMatchObject({enabled:true,confirm_email:false,rate_limit_email_sent:100,rate_limit_otp:30,rate_limit_verify:30,min_interval_seconds:30,confirm_email_chosen:true});
 // Nothing saved and no mail file: this environment signs people up confirmed, with Auth's own limits. A new file with nothing named confirms.
 expect(smtpView(null)).toMatchObject({enabled:false,confirm_email:false,confirm_email_chosen:false,rate_limit_email_sent:30,rate_limit_otp:30,min_interval_seconds:60});
 expect(smtpView(mailFromInput(cloud,null))).toMatchObject({confirm_email:true,confirm_email_chosen:true,rate_limit_email_sent:30,min_interval_seconds:60});
 // The settings show with custom SMTP off.
 expect(smtpView(null,{MAILER_AUTOCONFIRM:false,RATE_LIMIT_VERIFY:9})).toMatchObject({enabled:false,confirm_email:true,confirm_email_chosen:true,rate_limit_verify:9});
 for(const change of [{confirm_email:'no'},{rate_limit_email_sent:0},{rate_limit_otp:1.5},{rate_limit_verify:2_000_000},{min_interval_seconds:0},{min_interval_seconds:90_000},{rate_limit_otp:'5'}])
  expect(()=>sharedFromInput(change)).toThrow(SmtpError);
 // A mail file written before still decides what the sign-in settings do not name. A form the console does not edit shows no number.
 const legacy={...mailFromInput(cloud,null),autoconfirm:true,rate_limit_email_sent:'9',rate_limit_otp:7,max_frequency:'1h30m'};
 expect(smtpView(legacy)).toMatchObject({confirm_email:false,rate_limit_email_sent:9,rate_limit_otp:7,rate_limit_verify:30,min_interval_seconds:null});
 expect(smtpView({...legacy,rate_limit_email_sent:'30/1h'}).rate_limit_email_sent).toBeNull();
 expect(smtpView(legacy,{RATE_LIMIT_OTP:50,SMTP_MAX_FREQUENCY:5})).toMatchObject({confirm_email:false,rate_limit_otp:50,min_interval_seconds:5,rate_limit_email_sent:9});
});

test('a save moves the shared settings into the sign-in settings: named ones are set or cleared, file only ones are carried across, odd forms stay',()=>{
 const file=mailFromInput(cloud,null);
 // Nothing named and no sign-in settings: nothing moves and nothing is made, even though the file holds values.
 const legacy={...file,autoconfirm:true,rate_limit_otp:7};
 let plan=sharedSettings(null,{},legacy,{...legacy});
 expect(plan).toMatchObject({settings:null,keys:[]});expect(plan.after).toMatchObject({autoconfirm:true,rate_limit_otp:7});
 // Named, with no sign-in settings: they are made (the values Auth runs with already) and carry the config; the file gives up what it held.
 plan=sharedSettings(null,{MAILER_AUTOCONFIRM:false,RATE_LIMIT_EMAIL_SENT:40},legacy,{...legacy});
 expect(plan.settings).toMatchObject({site_url:'http://localhost',signup:true,anonymous:false,config:{MAILER_AUTOCONFIRM:false,RATE_LIMIT_EMAIL_SENT:40,RATE_LIMIT_OTP:7}});
 expect(plan.keys.sort()).toEqual(['MAILER_AUTOCONFIRM','RATE_LIMIT_EMAIL_SENT','RATE_LIMIT_OTP']);
 for(const key of ['autoconfirm','rate_limit_otp','rate_limit_email_sent'])expect(plan.after).not.toHaveProperty(key);
 // With sign-in settings saved, a value only the file holds moves across even when the save names nothing; one it cannot hold stays in the file.
 const saved=settingsFromInput({site_url:'https://a.example.com',config:{PASSWORD_MIN_LENGTH:12}},null);
 const odd={...legacy,rate_limit_verify:'30/1h',max_frequency:'1h30m'};
 plan=sharedSettings(saved,{},odd,{...odd});
 expect(plan.settings!.config).toEqual({PASSWORD_MIN_LENGTH:12,MAILER_AUTOCONFIRM:true,RATE_LIMIT_OTP:7});
 expect(plan.after).toMatchObject({rate_limit_verify:'30/1h',max_frequency:'1h30m'});
 expect(plan.after).not.toHaveProperty('autoconfirm');
 // A named null puts the setting back to the default, in the settings and in the file; the settings win over the file for what they hold.
 const held=settingsFromInput({site_url:'https://a.example.com',config:{RATE_LIMIT_OTP:99,MAILER_AUTOCONFIRM:false}},null);
 plan=sharedSettings(held,{RATE_LIMIT_OTP:null},legacy,{...legacy});
 expect(plan.settings!.config).toEqual({MAILER_AUTOCONFIRM:false});expect(plan.after).not.toHaveProperty('rate_limit_otp');
 // Turning custom SMTP off (no file after) keeps what the file held.
 plan=sharedSettings(saved,{},legacy,null);
 expect(plan.after).toBeNull();expect(plan.settings!.config).toMatchObject({MAILER_AUTOCONFIRM:true,RATE_LIMIT_OTP:7});
 // An unchanged result changes nothing.
 plan=sharedSettings(held,{RATE_LIMIT_OTP:99},file,{...file});
 expect(plan).toMatchObject({keys:[],settings:held});
});

test('a limit the host wrote as text shows as the number, a form with a duration shows as unset, and writing the same number is no change',()=>{
 const host={...mailFromInput(cloud,null),rate_limit_email_sent:'100'};
 expect(smtpView(host).rate_limit_email_sent).toBe(100);
 expect(smtpView({...host,rate_limit_email_sent:'30/1h'}).rate_limit_email_sent).toBeNull();
 const same=mailFromInput({...cloud,rate_limit_email_sent:100},host);
 expect(changedSmtpKeys(host,same)).toEqual([]);
 // First enabling with no Confirm email field: confirmation stays required.
 expect(smtpView(mailFromInput(cloud,null)).confirm_email).toBe(true);
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

test('a host that is this machine, link-local or one of this installation containers is refused',()=>{
 const base={admin_email:'a@example.com',host:'smtp.example.com',port:587,user:'u',password:'p'};
 for(const host of ['localhost','mail.localhost','127.0.0.1','169.254.169.254','0.0.0.0','2130706433','0x7f000001','sbarbase-durable-db'])
  expect(()=>mailFromInput({...base,host},null)).toThrow(SmtpError);
 expect(()=>mailFromInput({...base,host:'10.0.0.5'},null)).not.toThrow();
});

test('Confirm email and the limits save with custom SMTP off, an old mail file keeps its values until the first save, and an upgrade is not a change',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'smtp-si-')),mail=mkdtempSync(join(tmpdir(),'smtp-ml-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const [one,two,three]=['P','Q','R'].map(name=>catalog.createEnvironment('alice',catalog.createProject('alice',org,name),'production'));
  for(let i=0;i<3;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const handler=smtpHandler(catalog,async request=>request.headers.get('authorization'),signIn,mail,0);
  const call=(id:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${id}/smtp`,{method,headers:{authorization:'alice','content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  type Answer={data:{smtp:ReturnType<typeof smtpView>;revision:number}};
  const runtimeOf=(id:string)=>catalog.getProvision('alice',id).runtime;
  // 1. Custom SMTP off, nothing saved: Confirm email is off, as this environment runs. Saving it on stores the setting; no mail file appears.
  let answer=await (await call(one)).json() as Answer;
  expect(answer.data.smtp).toMatchObject({enabled:false,confirm_email:false,confirm_email_chosen:false});
  const saved=await call(one,'PUT',{enabled:false,confirm_email:true,rate_limit_otp:12,base_revision:answer.data.revision});
  expect(saved.status).toBe(202);
  answer=await saved.json() as Answer;
  expect(answer.data.smtp).toMatchObject({enabled:false,confirm_email:true,confirm_email_chosen:true,rate_limit_otp:12,rate_limit_verify:30});
  const settings=readSettings(signIn,runtimeOf(one))!;
  expect(settings).toMatchObject({site_url:'http://localhost',revision:answer.data.revision,config:{MAILER_AUTOCONFIRM:false,RATE_LIMIT_OTP:12}});
  expect(existsSync(join(mail,runtimeOf(one)+'-mail.json'))).toBe(false);
  expect(statSync(join(signIn,runtimeOf(one)+'-auth.json')).mode&0o777).toBe(0o600);
  expect(JSON.stringify(catalog.auditEvents('alice',org,50))).toContain('MAILER_AUTOCONFIRM');
  expect((await (await call(one)).json() as Answer).data.smtp).toMatchObject({confirm_email:true,rate_limit_otp:12});
  // Putting a limit back to the default clears it; a bad one is refused before anything is written.
  expect((await call(one,'PUT',{enabled:false,rate_limit_otp:null})).status).toBe(202);
  expect(readSettings(signIn,runtimeOf(one))!.config).toEqual({MAILER_AUTOCONFIRM:false});
  expect((await call(one,'PUT',{enabled:false,rate_limit_otp:0})).status).toBe(400);
  expect((await call(one,'PUT',{enabled:false,confirm_email:'yes'})).status).toBe(400);
  // Turning SMTP on later keeps the saved choice, and the new file does not carry it.
  expect((await call(one,'PUT',cloud)).status).toBe(202);
  expect(readMail(mail,runtimeOf(one))).not.toHaveProperty('autoconfirm');
  expect(readSettings(signIn,runtimeOf(one))!.config).toEqual({MAILER_AUTOCONFIRM:false});
  expect((await (await call(one)).json() as Answer).data.smtp).toMatchObject({enabled:true,confirm_email:true});

  // 2. An environment with a mail file written by an earlier version: the view shows the file's values (nothing changed by upgrading), a save that names
  // nothing moves them across and takes them out of the file, and turning SMTP off afterwards keeps them.
  const old=runtimeOf(two);
  write(join(mail,old+'-mail.json'),JSON.stringify({...mailFromInput(cloud,null),autoconfirm:true,rate_limit_email_sent:'9',rate_limit_otp:7,rate_limit_verify:8,max_frequency:'2m'}),{mode:0o600});
  answer=await (await call(two)).json() as Answer;
  expect(answer.data.smtp).toMatchObject({enabled:true,confirm_email:false,rate_limit_email_sent:9,rate_limit_otp:7,rate_limit_verify:8,min_interval_seconds:null});
  expect(existsSync(join(signIn,old+'-auth.json'))).toBe(false);
  expect((await call(two,'PUT',{...cloud,password:'',host:'two.example.com'})).status).toBe(202);
  // No sign-in settings were saved and none were named: the file is left exactly as it was.
  expect(readMail(mail,old)).toMatchObject({host:'two.example.com',autoconfirm:true,rate_limit_otp:7,max_frequency:'2m'});
  expect(existsSync(join(signIn,old+'-auth.json'))).toBe(false);
  // Once sign-in settings exist, the same save moves the values across; the odd duration stays in the file.
  write(join(signIn,old+'-auth.json'),JSON.stringify(settingsFromInput({site_url:'https://a.example.com'},null)),{mode:0o600});
  expect((await call(two,'PUT',{...cloud,password:'',host:'three.example.com'})).status).toBe(202);
  expect(readSettings(signIn,old)!.config).toEqual({MAILER_AUTOCONFIRM:true,RATE_LIMIT_EMAIL_SENT:9,RATE_LIMIT_OTP:7,RATE_LIMIT_VERIFY:8});
  expect(readMail(mail,old)).toMatchObject({host:'three.example.com',max_frequency:'2m'});
  for(const key of ['autoconfirm','rate_limit_email_sent','rate_limit_otp','rate_limit_verify'])expect(readMail(mail,old)).not.toHaveProperty(key);
  answer=await (await call(two)).json() as Answer;
  expect(answer.data.smtp).toMatchObject({confirm_email:false,rate_limit_email_sent:9,rate_limit_otp:7,rate_limit_verify:8});
  expect((await call(two,'PUT',{enabled:false})).status).toBe(202);
  expect(existsSync(join(mail,old+'-mail.json'))).toBe(false);
  expect((await (await call(two)).json() as Answer).data.smtp).toMatchObject({enabled:false,confirm_email:false,rate_limit_email_sent:9,rate_limit_otp:7});

  // 3. Turning custom SMTP off with an old file and settings present does not reset Confirm email or the limits.
  const third=runtimeOf(three);
  write(join(mail,third+'-mail.json'),JSON.stringify({...mailFromInput(cloud,null),autoconfirm:false,rate_limit_otp:5}),{mode:0o600});
  write(join(signIn,third+'-auth.json'),JSON.stringify(settingsFromInput({site_url:'https://a.example.com'},null)),{mode:0o600});
  expect((await call(three,'PUT',{enabled:false})).status).toBe(202);
  expect(readSettings(signIn,third)!.config).toEqual({MAILER_AUTOCONFIRM:false,RATE_LIMIT_OTP:5});
  expect((await (await call(three)).json() as Answer).data.smtp).toMatchObject({enabled:false,confirm_email:true,rate_limit_otp:5});
 } finally {catalog.close();}
});

test('turning custom SMTP on or off never flips Confirm email: a missing key means confirmation required, and the choice is written down',()=>{
 const file=mailFromInput(cloud,null);
 // On from nothing, nothing named: confirmation is required (what a new file always meant), written into the settings so the view and Auth agree.
 let plan=sharedSettings(null,{},null,file);
 expect(plan.settings!.config).toEqual({MAILER_AUTOCONFIRM:false});
 expect(smtpView(plan.after,plan.settings!.config).confirm_email).toBe(true);
 // On with the choice named (the console always sends it when it was never chosen): the choice wins, including off.
 plan=sharedSettings(null,{MAILER_AUTOCONFIRM:true},null,file);
 expect(plan.settings!.config).toEqual({MAILER_AUTOCONFIRM:true});
 // Off, a file without the keys, no sign-in settings: Confirm email stays on, as it was while the file was there.
 plan=sharedSettings(null,{},file,null);
 expect(plan.settings!.config).toEqual({MAILER_AUTOCONFIRM:false});
 expect(smtpView(null,plan.settings!.config).confirm_email).toBe(true);
 // Off, an old file with values, no sign-in settings: they move, so nothing is reset.
 plan=sharedSettings(null,{},{...file,autoconfirm:false,rate_limit_otp:7},null);
 expect(plan.settings!.config).toEqual({MAILER_AUTOCONFIRM:false,RATE_LIMIT_OTP:7});
 // Off, a file without the keys, sign-in settings present: same.
 const saved=settingsFromInput({site_url:'https://a.example.com'},null);
 expect(sharedSettings(saved,{},file,null).settings!.config).toEqual({MAILER_AUTOCONFIRM:false});
 // An environment that already chose is not touched by either.
 const chose=settingsFromInput({site_url:'https://a.example.com',config:{MAILER_AUTOCONFIRM:true}},null);
 expect(sharedSettings(chose,{},null,file)).toMatchObject({keys:[],settings:chose});
 expect(sharedSettings(chose,{},file,null)).toMatchObject({keys:[],settings:chose});
});

test('unknown members are refused with SMTP off too, and a host written limit the console cannot hold shows as unset, not as the word null',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'smtp-si-')),mail=mkdtempSync(join(tmpdir(),'smtp-ml-'));
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=smtpHandler(catalog,async request=>request.headers.get('authorization'),signIn,mail,0);
  const put=(body:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/smtp`,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(body)}));
  expect((await put({enabled:false,confirmEmail:true})).status).toBe(400);
  expect(existsSync(join(signIn,catalog.getProvision('alice',environment).runtime+'-auth.json'))).toBe(false);
  expect(smtpView({...mailFromInput(cloud,null),rate_limit_otp:5_000_000,rate_limit_verify:'x'})).toMatchObject({rate_limit_otp:null,rate_limit_verify:null});
 } finally {catalog.close();}
});
