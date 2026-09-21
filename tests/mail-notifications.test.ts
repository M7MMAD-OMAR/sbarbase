import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';

const identities:Record<string,string>={owner:'alice',viewer:'carol',outsider:'mallory'};
const identify=async(request:Request)=>identities[request.headers.get('authorization')??'']??null;
// The stub identity reads the credential directly, so the header carries the test token.
const call=(handler:ReturnType<typeof managementHandler>,path:string,token='owner',method='GET')=>
  handler(new Request('http://local'+path,{method,headers:{authorization:token}}));

/** One owner, one viewer, one environment whose runtime identifier is known to the test. */
function fixture() {
  const catalog=new Catalog(':memory:');
  const organization=catalog.createOrganization('alice','A');
  catalog.setMember('alice',organization,'carol','viewer');
  const project=catalog.createProject('alice',organization,'P');
  const environment=catalog.createEnvironment('alice',project,'production');
  const runtime=catalog.getProvision('alice',environment).runtime;
  return {catalog,organization,project,environment,runtime};
}

test('the console mail route answers from the runtime record, never from the catalog table',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-mail-'));
  const {catalog,environment,runtime}=fixture();
  try {
    // The runtime's own summary, in the shape lab/mail_state.py writes it.
    writeFileSync(join(directory,'mail-state.json'),JSON.stringify({[runtime]:{
      state:'applied',credentials:'set',at:1758400000,host:'smtp.example.com',port:587,
      user:'set',pass:'set',admin_email:'noreply@example.com',sender_name:'Example',reply_to:'support@example.com',
      max_frequency:'60s',otp_exp:3600,otp_length:6,secure_email_change:true,autoconfirm:false,
      rate_limit_email_sent:'30',rate_limit_otp:30,rate_limit_verify:30,rate_limit_header:'empty'}}));
    const handler=managementHandler(catalog,identify,directory);
    const response=await call(handler,`/management/v1/environments/${environment}/mail`);
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const body=await response.json() as {data:Record<string,unknown>};
    expect(body.data.state).toBe('applied');
    expect(body.data.credentials).toBe('set');
    expect(body.data.host).toBe('smtp.example.com');
    expect(body.data.admin_email).toBe('noreply@example.com');
    expect(body.data.rate_limit_email_sent).toBe('30');
    // No credential field at all: not the summary's own `pass`/`user` markers, and nothing
    // from a summary that carried a field the runtime does not describe.
    expect(Object.keys(body.data).filter(key=>['pass','password','user','secret','token'].includes(key))).toEqual([]);
    // Reading the state twice must not be needed and must not mutate the record.
    expect((await (await call(handler,`/management/v1/environments/${environment}/mail`)).json() as {data:unknown}).data)
      .toEqual(body.data);
  } finally {catalog.close();rmSync(directory,{recursive:true});}
});

test('an environment with no mail entry is unconfigured and an unknown environment matches the neighbour',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-mail-empty-'));
  const {catalog,environment}=fixture();
  try {
    const handler=managementHandler(catalog,identify,directory);
    const missing=await call(handler,`/management/v1/environments/${environment}/mail`);
    expect(missing.status).toBe(200);
    expect(await missing.json()).toEqual({data:{state:'unconfigured'}});
    // An environment uuid the catalog does not hold: the mail route answers exactly what the
    // neighbouring environment route answers, so the console has one unknown answer to render.
    const unknown='11111111-1111-1111-1111-111111111111';
    const neighbour=await call(handler,`/management/v1/environments/${unknown}/provision`);
    const mail=await call(handler,`/management/v1/environments/${unknown}/mail`);
    expect(mail.status).toBe(neighbour.status);
    expect(mail.status).toBe(403);
    expect(await mail.json()).toEqual(await neighbour.json());
    // A malformed environment uuid never reaches the route at all.
    expect((await call(handler,'/management/v1/environments/not-a-uuid/mail')).status).toBe(404);
    // A file that exists and cannot be parsed is a failure, never a silent unconfigured.
    writeFileSync(join(directory,'mail-state.json'),'not json');
    expect((await call(handler,`/management/v1/environments/${environment}/mail`)).status).toBe(500);
  } finally {catalog.close();rmSync(directory,{recursive:true});}
});

test('the mail route follows the neighbouring routes on method, membership and authentication',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-mail-auth-'));
  const {catalog,environment}=fixture();
  try {
    const handler=managementHandler(catalog,identify,directory);
    const path=`/management/v1/environments/${environment}/mail`;
    expect((await call(handler,path,'owner','POST')).status).toBe(405);
    expect((await call(handler,path,'','GET')).status).toBe(401);
    expect((await call(handler,path,'outsider')).status).toBe(403);
    // A viewer may read what the neighbouring environment routes let a viewer read.
    expect((await call(handler,path,'viewer')).status).toBe(200);
    const unavailable=managementHandler(catalog,async()=>{throw new Error('secret diagnostic');},directory);
    const response=await call(unavailable,path);
    expect(response.status).toBe(503);expect(await response.text()).not.toContain('secret');
  } finally {catalog.close();rmSync(directory,{recursive:true});}
});

test('the notifications route reports the undelivered count from the catalog read the drain uses',async()=>{
  const {catalog,organization}=fixture();
  try {
    const handler=managementHandler(catalog,identify,'.');
    // A membership change is the one operation in this fixture that enqueues an event.
    catalog.setMember('alice',organization,'dave','owner');
    const response=await call(handler,'/management/v1/notifications');
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const body=await response.json() as {data:{undelivered:number;events:{kind:string;severity:string;state:string;id:string}[]}};
    expect(body.data.undelivered).toBe(1);
    expect(body.data.events.length).toBe(2);
    expect(body.data.events.every(event=>event.kind==='membership.owner_changed')).toBe(true);
    expect(body.data.events.some(event=>event.severity==='critical')).toBe(true);
    // The catalog holds no recipient and no rendered body, so neither can appear here.
    const text=JSON.stringify(body);
    expect(text).not.toContain('@');
    for(const row of body.data.events)expect(Object.keys(row).filter(key=>['detail','recipient','to','password','token'].includes(key))).toEqual([]);
    // The count moves with the drain: both channels delivered means nothing is undelivered.
    for(const claim of catalog.claimNotifications())catalog.settleNotification(claim.event,claim.channel,claim.claim,'delivered');
    const after=await (await call(handler,'/management/v1/notifications')).json() as {data:{undelivered:number}};
    expect(after.data.undelivered).toBe(0);
  } finally {catalog.close();}
});

test('the notifications route is owner and admin only, and follows the neighbour error shape',async()=>{
  const {catalog}=fixture();
  try {
    const handler=managementHandler(catalog,identify,'.');
    expect((await call(handler,'/management/v1/notifications','viewer')).status).toBe(403);
    expect((await call(handler,'/management/v1/notifications','outsider')).status).toBe(403);
    expect((await call(handler,'/management/v1/notifications','','GET')).status).toBe(401);
    expect((await call(handler,'/management/v1/notifications','owner','POST')).status).toBe(405);
    const unavailable=managementHandler(catalog,async()=>{throw new Error('secret diagnostic');},'.');
    const response=await call(unavailable,'/management/v1/notifications');
    expect(response.status).toBe(503);expect(await response.text()).not.toContain('secret');
  } finally {catalog.close();}
});

test('the catalog no longer creates the environment_mail table',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-catalog-'));
  const path=join(directory,'control.sqlite');
  const catalog=new Catalog(path);catalog.close();
  const database=new Database(path);
  try {
    expect(database.query<{name:string},[]>(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='environment_mail'").get()).toBeNull();
    // The tables the runtime's own record is served from are still there.
    expect(database.query<{name:string},[]>(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='notification_outbox'").get()).not.toBeNull();
  } finally {database.close();rmSync(directory,{recursive:true});}
});