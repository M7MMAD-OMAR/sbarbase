import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,existsSync,writeFileSync as write,chmodSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {mailTemplatesHandler,mailTemplateRoute,templatesFromInput,templatesView,changedTemplateKeys,readTemplates,TemplateError,KINDS} from '../src/control/mail-templates';

const BODY='<h1>Hello {{ .Email }}</h1><a href="{{ .ConfirmationURL }}">go</a>';

test('a body or subject is stored only when it differs from the default, and clearing it removes it',()=>{
 const first=templatesFromInput({templates:{confirmation:{subject:'Welcome',body:BODY}}},null)!;
 expect(first.token).toMatch(/^[a-f0-9]{64}$/);
 expect(first.templates).toEqual({confirmation:{subject:'Welcome',body:BODY}});
 // Omitted kinds and members keep what is saved; the token stays.
 const second=templatesFromInput({templates:{recovery:{subject:'Reset'}}},first)!;
 expect(second.token).toBe(first.token);
 expect(second.templates.confirmation).toEqual(first.templates.confirmation);
 expect(second.templates.recovery).toEqual({subject:'Reset',body:''});
 // The default text itself, an empty text and null all mean "default".
 const view=templatesView(second);
 const confirmation=view.find(item=>item.kind==='confirmation')!;
 const third=templatesFromInput({templates:{confirmation:{subject:null,body:confirmation.default_body}}},second)!;
 expect(third.templates.confirmation).toBeUndefined();
 expect(templatesFromInput({templates:{recovery:{subject:''}}},third)).toBeNull();
 expect(changedTemplateKeys(first,second)).toEqual(['MAILER_SUBJECTS_RECOVERY']);
 expect(changedTemplateKeys(null,first)).toEqual(['MAILER_TEMPLATES_CONFIRMATION','MAILER_SUBJECTS_CONFIRMATION']);
});

test('the view shows the default for what is not customized, flags what is, and has the six messages',()=>{
 expect(templatesView(null).map(item=>item.kind)).toEqual(KINDS);
 expect(templatesView(null).every(item=>!item.customized&&item.subject===item.default_subject&&item.body===item.default_body)).toBe(true);
 const saved=templatesFromInput({templates:{magic_link:{body:BODY}}},null)!;
 const link=templatesView(saved).find(item=>item.kind==='magic_link')!;
 expect(link).toMatchObject({customized:true,body:BODY,subject:link.default_subject});
});

test('input is validated: kinds, members, sizes, line breaks in a subject and unpaired braces',()=>{
 for(const input of [{templates:{nope:{subject:'x'}}},{templates:{confirmation:{title:'x'}}},{templates:{confirmation:{subject:1}}},{templates:{confirmation:{subject:'a\nb'}}},
  {templates:{confirmation:{subject:'x'.repeat(201)}}},{templates:{confirmation:{body:'x'.repeat(32769)}}},{templates:{confirmation:{body:'{{ .Email '}}},{templates:{confirmation:{body:'}} {{'+'}}'}}},
  {templates:{confirmation:{body:'a\x00b'}}},{templates:[]},{templates:{confirmation:'x'}},{extra:1,templates:{}},[],null,'x'])
  expect(()=>templatesFromInput(input,null)).toThrow(TemplateError);
 // Six large bodies together stay under the file's cap.
 const big=Object.fromEntries(KINDS.map(kind=>[kind,{body:'<p>'+'x'.repeat(32000)+'</p>'}]));
 expect(()=>templatesFromInput({templates:big},null)).not.toThrow();
});

test('the management route is read and written by owners and admins only, writes a private file, never returns the token, and audits key names',async()=>{
 const catalog=new Catalog(':memory:');
 const signIn=mkdtempSync(join(tmpdir(),'tpl-si-')),dir=mkdtempSync(join(tmpdir(),'tpl-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const project=catalog.createProject('alice',org,'P');
  const first=catalog.createEnvironment('alice',project,'production'),second=catalog.createEnvironment('alice',project,'staging');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const handler=mailTemplatesHandler(catalog,async request=>request.headers.get('authorization'),signIn,dir,0);
  const url=(environment:string)=>`http://local/management/v1/environments/${environment}/mail-templates`;
  const call=(actor:string,environment:string,method='GET',body?:unknown)=>handler(new Request(url(environment),{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('carol',first)).status).toBe(403);
  expect((await call('bob',first)).status).toBe(403);
  expect((await call('alice',first,'DELETE')).status).toBe(405);
  expect((await call('alice',first,'PUT',{templates:{nope:{}}})).status).toBe(400);
  const before=await (await call('alice',first)).json() as {data:{templates:{kind:string;customized:boolean}[];revision:number}};
  expect(before.data.templates.every(item=>!item.customized)).toBe(true);
  const saved=await call('alice',first,'PUT',{base_revision:before.data.revision,templates:{confirmation:{subject:'Welcome',body:BODY}}});
  expect(saved.status).toBe(202);
  const runtime=catalog.getProvision('alice',first).runtime,other=catalog.getProvision('alice',second).runtime;
  const path=join(dir,runtime+'-templates.json');
  expect(statSync(path).mode&0o777).toBe(0o600);
  const file=JSON.parse(readFileSync(path,'utf8'));
  // The token is in the private file and in no answer.
  const text=JSON.stringify(await saved.json())+JSON.stringify(await (await call('alice',first)).json());
  expect(text).not.toContain(file.token);
  expect(text).toContain('Welcome');
  // Another environment shows its own defaults and has no file.
  const two=await (await call('alice',second)).json() as {data:{templates:{customized:boolean}[]}};
  expect(two.data.templates.some(item=>item.customized)).toBe(false);
  expect(existsSync(join(dir,other+'-templates.json'))).toBe(false);
  // A stale page is refused; the audit has names only.
  expect((await call('alice',first,'PUT',{base_revision:before.data.revision,templates:{recovery:{subject:'x'}}})).status).toBe(409);
  const events=JSON.stringify(catalog.auditEvents('alice',org));
  expect(events).toContain('MAILER_TEMPLATES_CONFIRMATION');expect(events).not.toContain('Welcome');expect(events).not.toContain(file.token);
  // Clearing everything removes the file.
  const now=await (await call('alice',first)).json() as {data:{revision:number}};
  expect((await call('alice',first,'PUT',{base_revision:now.data.revision,templates:{confirmation:{subject:null,body:null}}})).status).toBe(202);
  expect(existsSync(path)).toBe(false);
 } finally {catalog.close();}
});

test('a templates file that is a link, open to others, or malformed is refused, not read',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'tpl-'));
 const runtime='e_'+'a'.repeat(24);
 const path=join(dir,runtime+'-templates.json');
 expect(readTemplates(dir,runtime)).toBeNull();
 write(path,JSON.stringify({version:1,token:'a'.repeat(64),templates:{}}),{mode:0o644});
 expect(()=>readTemplates(dir,runtime)).toThrow(TemplateError);
 chmodSync(path,0o600);expect(readTemplates(dir,runtime)).not.toBeNull();
 write(path,'{"version":2}',{mode:0o600});expect(()=>readTemplates(dir,runtime)).toThrow(TemplateError);
 write(path,JSON.stringify({version:1,token:'short',templates:{}}),{mode:0o600});expect(()=>readTemplates(dir,runtime)).toThrow(TemplateError);
 rmSync(path);symlinkSync('/etc/hostname',path);expect(()=>readTemplates(dir,runtime)).toThrow(TemplateError);
});

test('the internal route serves an environment its own body for its own token and gives every other request the same 404',()=>{
 const dir=mkdtempSync(join(tmpdir(),'tpl-'));
 const a='e_'+'a'.repeat(24),b='e_'+'b'.repeat(24);
 const tokenA='1'.repeat(64),tokenB='2'.repeat(64);
 write(join(dir,a+'-templates.applied.json'),JSON.stringify({version:1,token:tokenA,templates:{confirmation:{subject:'',body:BODY},recovery:{subject:'Only a subject',body:''}}}),{mode:0o600});
 write(join(dir,b+'-templates.applied.json'),JSON.stringify({version:1,token:tokenB,templates:{confirmation:{subject:'',body:'<p>B</p>'}}}),{mode:0o600});
 const route=mailTemplateRoute(dir);
 const get=(path:string,method='GET')=>route(new Request('http://gateway:25432'+path,{method}));
 const ok=get(`/${a}/mail-templates/${tokenA}/confirmation`)!;
 expect(ok.status).toBe(200);expect(ok.headers.get('content-type')).toContain('text/html');expect(ok.headers.get('cache-control')).toBe('no-store');
 // Not this route: left to the next handler.
 expect(get('/'+a+'/auth/v1/health')).toBeNull();
 const miss=[`/${a}/mail-templates/${tokenB}/confirmation`,`/${b}/mail-templates/${tokenA}/confirmation`,`/${a}/mail-templates/${tokenA}/recovery`,`/${a}/mail-templates/${tokenA}/invite`,
  `/${a}/mail-templates/${tokenA}/nope`,`/e_${'c'.repeat(24)}/mail-templates/${tokenA}/confirmation`];
 const answers=miss.map(path=>get(path)!);
 expect(answers.every(answer=>answer.status===404)).toBe(true);
 expect(new Set(answers.map(answer=>answer.headers.get('content-length')??'')).size).toBe(1);
 expect(get(`/${a}/mail-templates/${tokenA}/confirmation`,'POST')!.status).toBe(404);
 // Environment B reads its own.
 return Promise.resolve(get(`/${b}/mail-templates/${tokenB}/confirmation`)!.text()).then(text=>expect(text).toBe('<p>B</p>'));
});

test('braces must open and close in order',()=>{
 expect(()=>templatesFromInput({templates:{confirmation:{body:'Hi }} {{ .Email'}}},null)).toThrow(TemplateError);
 expect(()=>templatesFromInput({templates:{confirmation:{body:'{{ {{ .Email }} }}'}}},null)).toThrow(TemplateError);
 expect(()=>templatesFromInput({templates:{confirmation:{body:'<a href="{{ .ConfirmationURL }}">{{ .Email }}</a>'}}},null)).not.toThrow();
});

test('a lone surrogate, extra members and oversized values in a host edited file are refused on both sides',()=>{
 expect(()=>templatesFromInput({templates:{confirmation:{subject:'\ud800'}}},null)).toThrow(TemplateError);
 expect(()=>templatesFromInput({templates:{confirmation:{body:'a\ud800b'}}},null)).toThrow(TemplateError);
 const dir=mkdtempSync(join(tmpdir(),'tpl-')),runtime='e_'+'a'.repeat(24),path=join(dir,runtime+'-templates.json');
 for(const entry of [{subject:'x',extra:1},{body:'x'.repeat(32769)},{subject:'a\nb'},{subject:'x'.repeat(201)}]){
  write(path,JSON.stringify({version:1,token:'a'.repeat(64),templates:{confirmation:entry}}),{mode:0o600});
  expect(()=>readTemplates(dir,runtime)).toThrow(TemplateError);
 }
 // A subject counts bytes, as Python does: 101 Arabic letters are 202 bytes.
 expect(()=>templatesFromInput({templates:{confirmation:{subject:'ب'.repeat(101)}}},null)).toThrow(TemplateError);
 expect(()=>templatesFromInput({templates:{confirmation:{subject:'ب'.repeat(100)}}},null)).not.toThrow();
});

test('a log line that names a template address does not carry its token',async()=>{
 const {redact}=await import('../src/control/observe');
 const line=`GET http://172.30.0.1:25432/e_${'a'.repeat(24)}/mail-templates/${'c'.repeat(64)}/confirmation 200`;
 expect(redact(line)).toBe(`GET http://172.30.0.1:25432/e_${'a'.repeat(24)}/mail-templates/[redacted]/confirmation 200`);
});
