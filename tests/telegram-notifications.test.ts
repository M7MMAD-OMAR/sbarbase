import {test,expect,afterAll} from 'bun:test';
import {mkdtempSync,mkdirSync,readFileSync,statSync,existsSync,writeFileSync,symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {TelegramNotifications} from '../src/control/telegram-notifications';

// A local stand-in for the Bot API: nothing here reaches Telegram.
const GOOD='123456789:AAbbccddeeffgghhiijjkkllmmnnoopp_q';
type Call={token:string;method:string;body:any};
const calls:Call[]=[];
let updates:any[]=[];
const fake=Bun.serve({port:0,async fetch(request) {
  const match=new URL(request.url).pathname.match(/^\/bot([^/]+)\/(\w+)$/);
  if(!match)return new Response('no',{status:404});
  const [,token,method]=match as unknown as [string,string,string];
  const body=await request.json().catch(()=>({}));
  calls.push({token,method,body});
  if(token!==GOOD)return Response.json({ok:false,description:'Unauthorized'},{status:401});
  if(method==='getMe')return Response.json({ok:true,result:{id:1,is_bot:true,username:'sbar_alerts_bot'}});
  if(method==='getUpdates')return Response.json({ok:true,result:body.offset?[]:updates});
  if(method==='sendMessage')return Response.json({ok:true,result:{message_id:1}});
  return Response.json({ok:false},{status:400});
}});
afterAll(()=>fake.stop(true));
const apiBase=`http://127.0.0.1:${fake.port}`;

function checkout() {
  const root=mkdtempSync(join(tmpdir(),'sbar-telegram-'));
  mkdirSync(join(root,'.secrets/upstream'),{recursive:true,mode:0o700});
  mkdirSync(join(root,'.lab/upstream'),{recursive:true,mode:0o700});
  return root;
}
const read=(root:string,name:string)=>JSON.parse(readFileSync(join(root,name),'utf8'));
const mode=(root:string,name:string)=>statSync(join(root,name)).mode&0o777;

test('a token is checked, kept private, never returned, and the chat is bound only by its code',async()=>{
  const root=checkout(),setup=new TelegramNotifications({checkout:root,apiBase});
  calls.length=0;
  await expect(setup.connect('not a token')).rejects.toThrow('not a bot token');
  await expect(setup.connect('123456789:AAwrongwrongwrongwrongwrongwrong_x')).rejects.toThrow('refused this token');
  expect(existsSync(join(root,'.secrets/upstream/telegram.json'))).toBe(false);
  const pending=await setup.connect(GOOD);
  expect(pending.state).toBe('pending');
  expect(JSON.stringify(pending)).not.toContain(GOOD.slice(0,-4));
  expect(pending.tokenHint).toBe(GOOD.slice(-4));
  expect(read(root,'.secrets/upstream/telegram.json')).toEqual({schema:1,botToken:GOOD});
  expect(mode(root,'.secrets/upstream/telegram.json')).toBe(0o600);
  const code=pending.link!.split('start=')[1]!;
  expect(pending.link).toBe(`https://t.me/sbar_alerts_bot?start=${code}`);
  expect(code).toMatch(/^[a-f0-9]{32}$/);
  // No Start yet, a stranger's Start and a wrong code are all refused.
  updates=[{update_id:5,message:{text:'/start',chat:{id:666}}},{update_id:6,message:{text:'/start deadbeef',chat:{id:667}}}];
  await expect(setup.complete()).rejects.toThrow('No Start from the link yet');
  expect(existsSync(join(root,'.lab/upstream/notifications.json'))).toBe(false);
  // Read updates were acknowledged.
  expect(calls.some(call=>call.method==='getUpdates'&&call.body.offset===7)).toBe(true);
  updates=[{update_id:8,message:{text:'hello',chat:{id:666}}},{update_id:9,message:{text:`/start ${code}`,chat:{id:424242}}}];
  calls.length=0;
  const linked=await setup.complete();
  expect(linked).toEqual({state:'linked',bot:'sbar_alerts_bot',tokenHint:GOOD.slice(-4),chat:'…4242'});
  expect(read(root,'.lab/upstream/notifications.json')).toEqual({schema:1,telegram:{enabled:true,chatId:'424242',
    tokenFile:'.secrets/upstream/telegram.json',bot:'sbar_alerts_bot',apiBase}});
  expect(mode(root,'.lab/upstream/notifications.json')).toBe(0o600);
  expect(existsSync(join(root,'.lab/upstream/telegram-link.json'))).toBe(false);
  expect(calls.find(call=>call.method==='sendMessage')?.body.chat_id).toBe('424242');
  // A used code cannot link again.
  await expect(setup.complete()).rejects.toThrow('Paste the bot token first');
  calls.length=0;
  await setup.test();
  expect(calls.map(call=>call.method)).toEqual(['sendMessage']);
});

test('other channels are kept, and disconnecting forgets the token',async()=>{
  const root=checkout(),setup=new TelegramNotifications({checkout:root,apiBase});
  const email={enabled:true,host:'smtp.example.invalid',port:587,from:'a@example.invalid',to:'b@example.invalid',tls:'starttls'};
  writeFileSync(join(root,'.lab/upstream/notifications.json'),JSON.stringify({schema:1,email}),{mode:0o600});
  const code=(await setup.connect(GOOD)).link!.split('start=')[1];
  updates=[{update_id:1,message:{text:`/start ${code}`,chat:{id:-100123}}}];
  await setup.complete();
  expect(read(root,'.lab/upstream/notifications.json').email).toEqual(email);
  expect(setup.disconnect()).toEqual({state:'off'});
  expect(read(root,'.lab/upstream/notifications.json')).toEqual({schema:1,email});
  expect(existsSync(join(root,'.secrets/upstream/telegram.json'))).toBe(false);
  // With Telegram the only channel, disconnecting removes the file, so the drain waits.
  const alone=checkout(),only=new TelegramNotifications({checkout:alone,apiBase});
  const second=(await only.connect(GOOD)).link!.split('start=')[1];
  updates=[{update_id:2,message:{text:`/start ${second}`,chat:{id:7}}}];
  await only.complete();
  only.disconnect();
  expect(existsSync(join(alone,'.lab/upstream/notifications.json'))).toBe(false);
});

test('a replaced token turns the old chat off, an expired link is refused, and a planted link is never followed',async()=>{
  const root=checkout();let now=1_000_000;
  const setup=new TelegramNotifications({checkout:root,apiBase,now:()=>now});
  const code=(await setup.connect(GOOD)).link!.split('start=')[1];
  updates=[{update_id:3,message:{text:`/start ${code}`,chat:{id:9}}}];
  await setup.complete();
  expect((await setup.connect(GOOD)).state).toBe('pending');
  expect(existsSync(join(root,'.lab/upstream/notifications.json'))).toBe(false);
  now+=31*60*1000;
  expect(setup.status()).toEqual({state:'off',tokenHint:GOOD.slice(-4)});
  await expect(setup.complete()).rejects.toThrow('Paste the bot token first');
  const planted=checkout();
  writeFileSync(join(planted,'elsewhere.json'),JSON.stringify({schema:1,botToken:GOOD}));
  symlinkSync(join(planted,'elsewhere.json'),join(planted,'.secrets/upstream/telegram.json'));
  expect(()=>new TelegramNotifications({checkout:planted,apiBase}).status()).toThrow('not a private file');
});

test('operators see the channel, only owners change it, and every change is audited',async()=>{
  const root=checkout();
  const previous=process.env.SBARBASE_TELEGRAM_API_BASE;process.env.SBARBASE_TELEGRAM_API_BASE=apiBase;
  const catalog=new Catalog(':memory:');
  try {
    const installation=catalog.initializeInstallation('telegram-fixture','owner','Installation');
    catalog.setMember('owner',installation,'admin','admin');
    catalog.setMember('owner',installation,'viewer','viewer');
    const handler=managementHandler(catalog,async request=>request.headers.get('authorization'),undefined,undefined,undefined,root);
    const call=(actor:string|undefined,method:string,suffix='',body?:unknown)=>handler(new Request(`http://local/management/v1/notifications/telegram${suffix}`,
      {method,headers:{...(actor?{authorization:actor}:{}),...(body?{'content-type':'application/json'}:{})},body:body?JSON.stringify(body):undefined}));
    expect((await call(undefined,'GET')).status).toBe(401);
    expect((await call('viewer','GET')).status).toBe(403);
    const seen=await call('admin','GET');expect(seen.status).toBe(200);expect(await seen.json()).toEqual({data:{state:'off'},owner:false});
    expect((await call('admin','PUT','',{token:GOOD})).status).toBe(403);
    expect((await call('owner','POST')).status).toBe(405);
    expect((await call('owner','PUT','',{token:GOOD,extra:1})).status).toBe(400);
    const bad=await call('owner','PUT','',{token:'123456789:AAwrongwrongwrongwrongwrongwrong_x'});
    expect(bad.status).toBe(400);expect((await bad.json()).message).toContain('refused this token');
    const connected=await call('owner','PUT','',{token:GOOD});expect(connected.status).toBe(200);
    const text=await connected.text();expect(text).not.toContain(GOOD.slice(0,-4));
    const code=JSON.parse(text).data.link.split('start=')[1];
    updates=[{update_id:4,message:{text:`/start ${code}`,chat:{id:55}}}];
    expect((await call('admin','POST','/link')).status).toBe(403);
    expect((await call('owner','POST','/link')).status).toBe(200);
    expect((await call('owner','POST','/test')).status).toBe(200);
    expect((await call('owner','DELETE')).status).toBe(200);
    const actions=(catalog as any).db.query("SELECT actor,action,detail FROM audit_events WHERE action LIKE 'notifications.%' ORDER BY sequence").all();
    expect(actions).toEqual(['connected','linked','tested','disconnected'].map(action=>({actor:'owner',action:`notifications.telegram.${action}`,detail:'{}'})));
  } finally {
    catalog.close();
    if(previous===undefined)delete process.env.SBARBASE_TELEGRAM_API_BASE;else process.env.SBARBASE_TELEGRAM_API_BASE=previous;
  }
});
