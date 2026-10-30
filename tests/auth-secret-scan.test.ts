import {test,expect} from 'bun:test';
import {mkdtempSync,readdirSync,readFileSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {signInHandler} from '../src/control/sign-in';
import {smtpHandler} from '../src/control/smtp';
import {smsHandler} from '../src/control/sms';
import {mailTemplatesHandler,mailTemplateRoute} from '../src/control/mail-templates';
import {authAdminHandler} from '../src/control/auth-admin';

/** The Authentication workspace's secret gate (docs/engineering/plans/2026-10-30-auth-providers-workspace.md, Security gates 1 and 2):
 * every secret one environment holds is written to its private files only. No answer of any route, no audit row and no byte of the catalog
 * carries it, and the other environment's answers carry none of the first one's. */
const SECRETS={provider:'PROVIDER-SECRET-VALUE-1',smtp:'SMTP-PASSWORD-VALUE-2',sms:'SMS-AUTH-TOKEN-VALUE-3'};

test('secrets and tokens saved for one environment appear in no answer, audit row or catalog byte, and never in the other environment',async()=>{
 const work=mkdtempSync(join(tmpdir(),'secret-scan-')),secrets=join(work,'secrets');
 const catalog=new Catalog(join(work,'control.sqlite'));
 try {
  const org=catalog.createOrganization('alice','A');
  const project=catalog.createProject('alice',org,'P');
  const first=catalog.createEnvironment('alice',project,'production'),second=catalog.createEnvironment('alice',project,'staging');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const identify=async(request:Request)=>request.headers.get('authorization');
  const admin=async()=>({status:200,body:{items:[]},total:null});
  const routes={'sign-in':signInHandler(catalog,identify,secrets,0),'smtp':smtpHandler(catalog,identify,secrets,secrets,0),'sms':smsHandler(catalog,identify,secrets,secrets,0),
   'mail-templates':mailTemplatesHandler(catalog,identify,secrets,secrets,0),'auth-config':signInHandler(catalog,identify,secrets,0),
   'sso-providers':authAdminHandler(catalog,identify,admin as never),'auth-audit':authAdminHandler(catalog,identify,admin as never)};
  const call=(leaf:keyof typeof routes,environment:string,method='GET',body?:unknown)=>routes[leaf](new Request(`http://local/management/v1/environments/${environment}/${leaf}`,
   {method,headers:{authorization:'alice','content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const seen:string[]=[];
  const record=async(response:Response)=>{seen.push(await response.clone().text());return response;};
  const revision=async()=>((await (await call('sign-in',first)).json()) as {data:{revision:number}}).data.revision;
  expect((await record(await call('sign-in',first,'PUT',{site_url:'https://a.example.com',providers:{github:{enabled:true,client_id:'Iv1.alpha',secret:SECRETS.provider}},config:{SAML_ENABLED:true}}))).status).toBe(202);
  expect((await record(await call('smtp',first,'PUT',{enabled:true,admin_email:'noreply@example.com',sender_name:'A',host:'smtp.example.com',port:587,user:'u',password:SECRETS.smtp,base_revision:await revision()}))).status).toBe(202);
  expect((await record(await call('sms',first,'PUT',{provider:'twilio',credentials:{account_sid:'AC1',auth_token:SECRETS.sms,message_service_sid:'MG1'},base_revision:await revision()}))).status).toBe(202);
  expect((await record(await call('mail-templates',first,'PUT',{templates:{confirmation:{subject:'Hi',body:'<p>{{ .ConfirmationURL }}</p>'}},base_revision:await revision()}))).status).toBe(202);
  // The private files hold them (so the test would notice if nothing were written), under mode 0600.
  const files=readdirSync(secrets).filter(name=>!name.endsWith('.pending'));
  // Only the environment that was saved has files; the other one has none.
  expect(files.every(name=>name.startsWith(catalog.getProvision('alice',first).runtime))).toBe(true);
  const private_=files.map(name=>({name,text:readFileSync(join(secrets,name),'utf8'),mode:statSync(join(secrets,name)).mode&0o777}));
  expect(private_.every(item=>item.mode===0o600)).toBe(true);
  const token=JSON.parse(private_.find(item=>item.name.endsWith('-templates.json'))!.text).token as string;
  const samlKey=JSON.parse(private_.find(item=>item.name.endsWith('-saml.json'))!.text).private_key as string;
  const all=[...Object.values(SECRETS),token,samlKey];
  for(const secret of all)expect(private_.some(item=>item.text.includes(secret))).toBe(true);
  // Every route, for both environments, as an owner reads it.
  for(const environment of [first,second])for(const leaf of Object.keys(routes) as (keyof typeof routes)[])await record(await call(leaf,environment));
  seen.push(JSON.stringify(catalog.auditEvents('alice',org,200)));
  // The internal route serves a body to its own token only: the token is in its URL, not in any answer above.
  const internal=mailTemplateRoute(secrets)(new Request(`http://gateway:25432/${catalog.getProvision('alice',first).runtime}/mail-templates/${token}/confirmation`))!;
  expect(internal.status).toBe(200);
  const everything=seen.join('\n');
  for(const secret of all)expect(everything).not.toContain(secret);
  // Not one byte of the catalog (database, journal or log) holds a secret either.
  catalog.close();
  const bytes=readdirSync(work).filter(name=>name.startsWith('control.sqlite')).map(name=>readFileSync(join(work,name)).toString('latin1')).join('');
  expect(bytes.length).toBeGreaterThan(0);
  for(const secret of all)expect(bytes).not.toContain(secret);
 } finally {try{catalog.close();}catch{}}
});
