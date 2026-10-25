import {describe,expect,test} from 'bun:test';
import {connect,createServer,type Server,type Socket} from 'node:net';
import {mkdtempSync,readdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {type Upgrade,bindWithRetry,drainDeadline,drainListeners,serveLocal,stopOnSignals,DRAIN_MS_DEFAULT,MAX_FRONT_CONNECTIONS,SETTLE_MS_MAX} from '../src/http/local-server';
import {databaseProxy,DirectDatabase} from '../src/http/database-proxy';
import {functionsStorageListeners} from '../src/control/functions-storage';
import {startGateway,type GatewayApplication} from '../lab/upstream-server';
import {ensureDatabaseTls} from '../src/http/database-tls';
import {ExternalGuard} from '../src/http/database-external';
import type {HoldScope} from '../src/gateway/hold';

const RUNTIME='e_0123456789abcdef01234567';

/** A free port, held by a SO_REUSEPORT placeholder until `release`, so listeners can join it.
 * The port is chosen without SO_REUSEPORT first: an ephemeral bind with it may be given a port
 * that another reuseport listener of this user already holds, and so join a parallel test's port. */
async function sharedPort() {
 for(;;){
  const probe=createServer();
  await new Promise<void>(resolve=>probe.listen({port:0,host:'127.0.0.1'},()=>resolve()));
  const port=(probe.address() as {port:number}).port;
  await new Promise<void>(resolve=>probe.close(()=>resolve()));
  const holder=createServer(socket=>socket.destroy());
  const bound=await new Promise<boolean>(resolve=>{holder.once('error',()=>resolve(false));
   holder.listen({port,host:'127.0.0.1',reusePort:true},()=>resolve(true));});
  if(bound)return {port,release:()=>new Promise<void>(resolve=>holder.close(()=>resolve()))};
 }
}

/** One HTTP request on a fresh connection: the raw answer, or the connection error's code. */
function request(port:number,path='/',{method='GET'}:{method?:string}={}):Promise<{raw:string;error?:string}> {
 return new Promise(resolve=>{
  const socket=connect(port,'127.0.0.1');let raw='';
  socket.on('connect',()=>socket.write(`${method} ${path} HTTP/1.1\r\nhost: 127.0.0.1\r\nconnection: close\r\ncontent-length: 0\r\n\r\n`));
  socket.on('data',chunk=>{raw+=chunk;});
  // A hard limit as well: an idle timeout may not cover a connect that waits for its SYN to be
  // answered, which a loaded host can stretch to minutes.
  const timer=setTimeout(()=>{socket.destroy();resolve({raw,error:'timeout'});},10_000);
  socket.on('error',error=>{clearTimeout(timer);resolve({raw,error:(error as NodeJS.ErrnoException).code??'error'});});
  socket.on('close',()=>{clearTimeout(timer);resolve({raw});});
  socket.setTimeout(8_000,()=>socket.destroy());
 });
}
/** The answer's body, chunked or not. */
function body(raw:string):string {
 const at=raw.indexOf('\r\n\r\n');if(at<0)return '';
 let rest=raw.slice(at+4);
 if(!/\r\ntransfer-encoding: *chunked/i.test(raw.slice(0,at)))return rest;
 let out='';
 for(;;){
  const end=rest.indexOf('\r\n');if(end<0)return out;
  const size=parseInt(rest.slice(0,end),16);if(!size)return out;
  out+=rest.slice(end+2,end+2+size);rest=rest.slice(end+2+size+2);
 }
}
const until=async(done:()=>boolean,ms:number)=>{const deadline=Date.now()+ms;while(!done()&&Date.now()<deadline)await Bun.sleep(5);return done();};
/** Fresh connections until one is refused: ECONNREFUSED, or what came back instead. Before it,
 * each is answered in full by this listener, or is one the close caught in the accept queue
 * (reset, or closed with nothing sent: the limit ACCEPT_GRACE_MS describes). */
async function refusedWithin(port:number,ms:number):Promise<string> {
 for(const deadline=Date.now()+ms;Date.now()<deadline;await Bun.sleep(20)){
  const answer=await request(port,'/quick');
  if(answer.error==='ECONNREFUSED')return answer.error;
  if(answer.error||answer.raw==='')continue;
  if(body(answer.raw)!=='quick')return 'answered: '+JSON.stringify(answer.raw);
 }
 return 'still accepting';
}
/** Fresh TCP connections until one is refused; false at `ms`. */
async function connectRefusedWithin(port:number,ms:number):Promise<boolean> {
 for(const deadline=Date.now()+ms;Date.now()<deadline;await Bun.sleep(20)){
  const outcome=await new Promise<string>(resolve=>{const socket=connect(port,'127.0.0.1');
   socket.on('connect',()=>{socket.destroy();resolve('connected');});socket.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));});
  if(outcome==='ECONNREFUSED')return true;
 }
 return false;
}
/** A cleartext startup, which the external listener refuses with a message once it accepted the connection. */
function cleartext(port:number):Promise<string> {
 return new Promise<string>(resolve=>{
  const socket=connect(port,'127.0.0.1');let text='';
  socket.on('connect',()=>socket.write(startup(`${RUNTIME}_developer`,RUNTIME)));
  socket.on('data',chunk=>{text+=chunk.toString('latin1');});
  socket.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));socket.on('close',()=>resolve(text));
  socket.setTimeout(4_000,()=>socket.destroy());
 });
}
/** A promise the test resolves itself, so nothing here waits on a sleep. */
function gate(){let open!:()=>void;const opened=new Promise<void>(resolve=>{open=resolve;});return {opened,open};}

describe('SO_REUSEPORT on fixed listeners',()=>{
 test('two HTTP listeners on one fixed port both receive connections',async()=>{
  const shared=await sharedPort();
  const a=await serveLocal(()=>new Response('A'),shared.port),b=await serveLocal(()=>new Response('B'),shared.port);
  await shared.release();
  try {
   expect([a.port,b.port]).toEqual([shared.port,shared.port]);
   const seen={A:0,B:0} as Record<string,number>;
   for(let i=0;i<40;i++){const answer=await request(shared.port);seen[body(answer.raw)]=(seen[body(answer.raw)]??0)+1;}
   expect(seen.A).toBeGreaterThan(0);expect(seen.B).toBeGreaterThan(0);expect(seen.A!+seen.B!).toBe(40);
  } finally {a.stop(true);b.stop(true);}
 },20_000);

 test('the WebSocket front and the database proxy share their fixed ports too',async()=>{
  const shared=await sharedPort();
  const front={upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false};
  const a=await serveLocal(()=>new Response('A'),shared.port,front),b=await serveLocal(()=>new Response('B'),shared.port,front);
  const database=await sharedPort();
  const logs={a:0,b:0};
  const proxyA=await databaseProxy({port:database.port,host:'127.0.0.1',target:()=>undefined,log:()=>{logs.a++;}});
  const proxyB=await databaseProxy({port:database.port,host:'127.0.0.1',target:()=>undefined,log:()=>{logs.b++;}});
  await shared.release();await database.release();
  try {
   const seen=new Set<string>();
   for(let i=0;i<40;i++)seen.add(body((await request(shared.port)).raw));
   expect([...seen].sort()).toEqual(['A','B']);
   // A refused login is logged by whichever proxy took the connection.
   for(let i=0;i<40;i++)await new Promise<void>(resolve=>{
    const socket=connect(database.port,'127.0.0.1',()=>socket.write(startup('postgres','postgres')));
    socket.on('data',()=>{});socket.on('error',()=>resolve());socket.on('close',()=>resolve());
   });
   expect(logs.a).toBeGreaterThan(0);expect(logs.b).toBeGreaterThan(0);expect(logs.a+logs.b).toBe(40);
  } finally {a.stop(true);b.stop(true);proxyA.stop();proxyB.stop();}
 },20_000);
});

test('a source-checked listener (the Functions\' Storage) shares its fixed port, and the front still checks the source',async()=>{
 const shared=await sharedPort();
 const loopback=(source:string)=>source.replace(/^::ffff:/,'').startsWith('127.');
 const a=await serveLocal(()=>new Response('A'),shared.port,{admit:loopback}),b=await serveLocal(()=>new Response('B'),shared.port,{admit:loopback});
 const refusing=await sharedPort();let handled=0;
 const closed=await serveLocal(()=>{handled++;return new Response('no');},refusing.port,{admit:()=>false});
 await shared.release();await refusing.release();
 try {
  const seen=new Set<string>();
  for(let i=0;i<40;i++)seen.add(body((await request(shared.port)).raw));
  expect([...seen].sort()).toEqual(['A','B']);
  const answer=await request(refusing.port);
  expect(answer.raw).toBe('');
  expect(handled).toBe(0);
 } finally {a.stop(true);b.stop(true);closed.stop(true);}
},20_000);

describe('graceful HTTP drain',()=>{
 for(const [name,options] of [['plain, ephemeral port',undefined],['fixed port',{}],
  ['behind the WebSocket front',{upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false}]] as const)
 test(`${name}: kept-alive connections stay through the grace and are answered with connection: close, idle ones close at its end, nothing new is taken after it, and the in-flight request finishes`,async()=>{
  const shared=options?await sharedPort():undefined;
  const release=gate();let entered=0;
  const server=await serveLocal(async request=>{
   if(new URL(request.url).pathname==='/slow'){entered++;await release.opened;return new Response('finished');}
   return new Response('quick');
  },shared?.port??0,options??{});
  await shared?.release();
  try {
   // Two kept-alive connections that have answered once: one stays idle, one is used again.
   const kept=async()=>{
    const socket=connect(server.port,'127.0.0.1');const state={raw:'',closed:false};
    socket.on('data',chunk=>{state.raw+=chunk;});socket.on('close',()=>{state.closed=true;});socket.on('error',()=>{});
    socket.on('connect',()=>socket.write('GET /quick HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n'));
    expect(await until(()=>state.raw.includes('quick'),4_000)).toBe(true);
    return {socket,state};
   };
   const idle=await kept(),reused=await kept();
   const slow=request(server.port,'/slow');
   expect(await until(()=>entered===1,4_000)).toBe(true);
   let drained=false;
   // A long grace, so what is checked during it cannot miss it on a loaded host.
   const draining=server.drain(3_000).then(()=>{drained=true;});
   // Nothing kept alive is closed at the signal: a request may already be on its way.
   await Bun.sleep(300);
   expect([idle.state.closed,reused.state.closed]).toEqual([false,false]);
   // A request on a kept-alive connection during the grace is answered, then the connection closes.
   reused.state.raw='';
   reused.socket.write('GET /quick HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   expect(await until(()=>reused.state.closed,4_000)).toBe(true);
   expect(body(reused.state.raw)).toBe('quick');
   expect(reused.state.raw.toLowerCase()).toContain('connection: close');
   // During the grace a new connection is still accepted, answered, and told to close.
   const during=await request(server.port,'/quick');
   expect(body(during.raw)).toBe('quick');
   expect(during.raw.toLowerCase()).toContain('connection: close');
   // After it, nothing new is accepted, and the idle connection has closed.
   expect(await refusedWithin(server.port,8_000)).toBe('ECONNREFUSED');
   expect(await until(()=>idle.state.closed,2_000)).toBe(true);
   expect(drained).toBe(false);
   release.open();
   const answer=await slow;
   expect(answer.raw.startsWith('HTTP/1.1 200')).toBe(true);
   expect(body(answer.raw)).toBe('finished');
   expect(answer.raw.toLowerCase()).toContain('connection: close');
   await draining;
   expect(server.connections()).toBe(0);
  } finally {server.stop(true);}
 },20_000);

 for(const [name,options] of [['plain, ephemeral port',undefined],['fixed port',{}],
  ['behind the WebSocket front',{upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false}]] as const)
 test(`${name}: a connection that never sends a request does not hold the drain open`,async()=>{
  const shared=options?await sharedPort():undefined;
  const server=await serveLocal(()=>new Response('quick'),shared?.port??0,options??{});
  await shared?.release();
  const quiet=connect(server.port,'127.0.0.1');let closed=false;
  quiet.on('close',()=>{closed=true;});quiet.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>quiet.once('connect',()=>resolve()));
   // Behind a front the connection is counted at once; Bun's node:http counts one only once it sends.
   if(options)expect(await until(()=>server.connections()>0,4_000)).toBe(true);
   const started=performance.now();
   await server.drain(50);
   expect(performance.now()-started).toBeLessThan(4_000);
   // Without a front, Bun's node:http leaves such a connection to stop(true); the drain does not wait for it.
   if(options)expect(await until(()=>closed,4_000)).toBe(true);
  } finally {quiet.destroy();server.stop(true);}
 },20_000);

 test('a request after one process drains is answered by the other process on the same port',async()=>{
  const shared=await sharedPort();
  const old=await serveLocal(()=>new Response('old'),shared.port),next=await serveLocal(()=>new Response('new'),shared.port);
  await shared.release();
  try {
   await old.drain(0);
   for(let i=0;i<20;i++){
    const answer=await request(shared.port);
    expect(answer.error).toBeUndefined();
    expect(body(answer.raw)).toBe('new');
   }
  } finally {old.stop(true);next.stop(true);}
 },20_000);

 test('the deadline closes what is still in flight and reports it',async()=>{
  const never=gate();let entered=false;
  const server=await serveLocal(async()=>{entered=true;await never.opened;return new Response('late');},0);
  const slow=request(server.port,'/slow');
  try {
   expect(await until(()=>entered,4_000)).toBe(true);
   const started=performance.now();
   expect(await drainListeners([server],300,50)).toBe(false);
   const took=performance.now()-started;
   expect(took).toBeGreaterThanOrEqual(295);expect(took).toBeLessThan(3_000);
   const answer=await slow;
   expect(answer.raw.includes('late')).toBe(false);
  } finally {never.open();server.stop(true);}
 },20_000);

 test('everything finished before the deadline reports so, without waiting for it',async()=>{
  const server=await serveLocal(()=>new Response('ok'),0);
  const started=performance.now();
  expect(await drainListeners([server,undefined],10_000,0)).toBe(true);
  expect(performance.now()-started).toBeLessThan(5_000);
 });
});

function startup(user:string,database:string) {
 const fields=Buffer.from(`user\0${user}\0database\0${database}\0\0`);
 const message=Buffer.alloc(8+fields.length);
 message.writeInt32BE(message.length,0);message.writeInt32BE(196608,4);fields.copy(message,8);
 return message;
}

/** A stand-in PostgreSQL that echoes whatever follows the startup message. */
async function echoDatabase() {
 const sockets=new Set<Socket>();
 const server:Server=createServer(socket=>{sockets.add(socket);socket.once('close',()=>sockets.delete(socket));socket.on('error',()=>{});socket.pipe(socket);});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',()=>resolve()));
 return {port:(server.address() as {port:number}).port,stop(){for(const socket of sockets)socket.destroy();server.close();}};
}

async function session(port:number) {
 const socket=connect(port,'127.0.0.1');let received='',closed=false;
 socket.on('data',chunk=>{received+=chunk.toString('latin1');});socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
 await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
 socket.write(startup(`${RUNTIME}_developer`,RUNTIME));
 return {socket,received:()=>received,closed:()=>closed};
}

describe('graceful database proxy drain',()=>{
 test('DirectDatabase drains its listener, and a drain before start or after stop settles at once',async()=>{
  const direct=new DirectDatabase();
  await direct.drain();
  await direct.start({port:0,host:'127.0.0.1',target:()=>undefined});
  await direct.drain();
  direct.stop();
  await direct.drain();
 });

 test('an open session keeps working until it ends; a new connection is refused',async()=>{
  const upstream=await echoDatabase();
  const handle=await databaseProxy({port:0,host:'127.0.0.1',target:()=>({host:'127.0.0.1',port:upstream.port})});
  try {
   const open=await session(handle.port);
   expect(await until(()=>open.received().length>0,4_000)).toBe(true);
   let drained=false;
   const draining=handle.drain(0).then(()=>{drained=true;});
   // Once the grace (zero here) is over, a new connection is refused.
   let refused='';
   for(const deadline=Date.now()+8_000;refused!=='ECONNREFUSED'&&Date.now()<deadline;await Bun.sleep(20)){
    const late=connect(handle.port,'127.0.0.1');
    refused=await new Promise<string>(resolve=>{late.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));late.on('connect',()=>resolve('connected'));});
    late.destroy();
   }
   expect(refused).toBe('ECONNREFUSED');
   open.socket.write('select 1');
   expect(await until(()=>open.received().endsWith('select 1'),4_000)).toBe(true);
   expect(drained).toBe(false);
   open.socket.end();
   await draining;
   expect(drained).toBe(true);
  } finally {handle.stop();upstream.stop();}
 },20_000);

 test('the deadline closes a session that does not end',async()=>{
  const upstream=await echoDatabase();
  const handle=await databaseProxy({port:0,host:'127.0.0.1',target:()=>({host:'127.0.0.1',port:upstream.port})});
  try {
   const open=await session(handle.port);
   expect(await until(()=>open.received().length>0,4_000)).toBe(true);
   const started=performance.now();
   expect(await drainListeners([handle],250,0)).toBe(false);
   expect(performance.now()-started).toBeGreaterThanOrEqual(245);
   expect(await until(()=>open.closed(),4_000)).toBe(true);
  } finally {handle.stop();upstream.stop();}
 },20_000);
});

test('Functions Storage listeners drain together, and a drained set opens nothing more',async()=>{
 const drained:string[]=[],opened:string[]=[];
 const endpoints:Record<string,any>={[RUNTIME]:{functions:{storage:{host:'172.30.4.1',port:5001,subnet:'172.30.4.0/24'}}}};
 const set=functionsStorageListeners({read:()=>endpoints,open:async runtime=>{opened.push(runtime);
  return {stop(){},async drain(){drained.push(runtime);}};}});
 await set.reconcile();
 expect(opened).toEqual([RUNTIME]);
 await set.drain();
 expect(drained).toEqual([RUNTIME]);
 endpoints['e_00000000000000000000000a']={functions:{storage:{host:'172.30.5.1',port:5001,subnet:'172.30.5.0/24'}}};
 await set.reconcile();
 expect(opened).toEqual([RUNTIME]);
 set.stop();
});

// Two real processes, as an upgrade's old and new version: every kind of fixed listener the
// console binds shares its port across processes.
test('a second process joins every fixed listener\'s port and both processes receive connections',async()=>{
 const ports={console:await sharedPort(),plain:await sharedPort(),admit:await sharedPort(),database:await sharedPort()};
 const place=mkdtempSync(join(tmpdir(),'sbarbase-reuseport-'));
 const script=join(place,'listeners.ts');
 const source=(tag:string)=>`
import {serveLocal} from ${JSON.stringify(join(import.meta.dir,'../src/http/local-server.ts'))};
import {databaseProxy} from ${JSON.stringify(join(import.meta.dir,'../src/http/database-proxy.ts'))};
const [console_,plain,admit,database]=process.argv.slice(2).map(Number);
const answer=()=>new Response(${JSON.stringify(tag)});
await serveLocal(answer,console_,{upgrade:()=>({ok:false,status:404,message:'Unknown route'}),isUpgrade:()=>false});
await serveLocal(answer,plain);
await serveLocal(answer,admit,{admit:source=>source.replace(/^::ffff:/,'').startsWith('127.')});
await databaseProxy({port:database,host:'127.0.0.1',target:()=>undefined,log:()=>console.log(${JSON.stringify(tag)})});
console.log('ready');
`;
 writeFileSync(script,source('child'));
 const parentScript=join(place,'parent.ts');writeFileSync(parentScript,source('parent'));
 const args=[ports.console.port,ports.plain.port,ports.admit.port,ports.database.port].map(String);
 const spawn=(file:string)=>{
  const spawned=Bun.spawn(['bun',file,...args],{stdout:'pipe',stderr:'inherit'});let output='';
  void (async()=>{for await(const chunk of spawned.stdout)output+=new TextDecoder().decode(chunk);})();
  return {spawned,output:()=>output};
 };
 const first=spawn(parentScript),second=spawn(script);
 try {
  expect(await until(()=>first.output().includes('ready')&&second.output().includes('ready'),15_000)).toBe(true);
  for(const holder of Object.values(ports))await holder.release();
  for(const port of [ports.console.port,ports.plain.port,ports.admit.port]){
   const seen=new Set<string>();
   for(let i=0;i<40&&seen.size<2;i++)seen.add(body((await request(port)).raw));
   expect([...seen].sort()).toEqual(['child','parent']);
  }
  for(let i=0;i<40&&!(first.output().includes('parent\n')&&second.output().includes('child\n'));i++)
   await new Promise<void>(resolve=>{
    const socket=connect(ports.database.port,'127.0.0.1',()=>socket.write(startup('postgres','postgres')));
    socket.on('data',()=>{});socket.on('error',()=>resolve());socket.on('close',()=>resolve());
   });
  expect(first.output()).toContain('parent\n');expect(second.output()).toContain('child\n');
 } finally {first.spawned.kill('SIGKILL');second.spawned.kill('SIGKILL');rmSync(place,{recursive:true,force:true});}
},30_000);

describe('stop on signals',()=>{
 test('the first signal drains then finishes; a second one finishes at once, and finish runs once',async()=>{
  const handlers:Record<string,()=>void>={};
  const steps:string[]=[];const hold=gate();
  stopOnSignals(async()=>{steps.push('drain');await hold.opened;steps.push('drained');},()=>steps.push('finish'),
   (signal,handler)=>{handlers[signal]=handler;});
  handlers.SIGTERM!();
  expect(steps).toEqual(['drain']);
  handlers.SIGINT!();
  expect(steps).toEqual(['drain','finish']);
  hold.open();await Bun.sleep(0);await hold.opened;await Bun.sleep(0);
  expect(steps).toEqual(['drain','finish','drained']);
 });

 test('a failing drain still finishes',async()=>{
  const handlers:Record<string,()=>void>={};let finished=0;
  stopOnSignals(async()=>{throw new Error('broken');},()=>{finished++;},(signal,handler)=>{handlers[signal]=handler;});
  handlers.SIGINT!();
  expect(await until(()=>finished===1,2_000)).toBe(true);
 });

 test('the deadline: unset is the default, a valid value is taken, anything else is refused',()=>{
  expect(drainDeadline(undefined)).toBe(DRAIN_MS_DEFAULT);
  expect(drainDeadline('')).toBe(DRAIN_MS_DEFAULT);
  expect(DRAIN_MS_DEFAULT).toBeLessThan(20_000);
  expect(drainDeadline('0')).toBe(0);
  expect(drainDeadline('19000')).toBe(19_000);
  for(const value of ['19001','20000','-1','1.5','15s',' 15000','100000'])expect(()=>drainDeadline(value)).toThrow('SBARBASE_DRAIN_MS');
 });

 // A real process and a real SIGTERM, the way lab/dev.py and systemd stop the console.
 const child=(source:string)=>{
  const place=mkdtempSync(join(tmpdir(),'sbarbase-graceful-'));
  const script=join(place,'server.ts');
  writeFileSync(script,source.replaceAll('LOCAL_SERVER',JSON.stringify(join(import.meta.dir,'../src/http/local-server.ts'))));
  const spawned=Bun.spawn(['bun',script],{env:{...process.env,DEADLINE:'10000'},stdin:'pipe',stdout:'pipe',stderr:'inherit'});
  let output='';
  void (async()=>{for await(const chunk of spawned.stdout)output+=new TextDecoder().decode(chunk);})();
  return {process:spawned,output:()=>output,done(){spawned.kill('SIGKILL');rmSync(place,{recursive:true,force:true});}};
 };
 const SERVER=`
import {serveLocal,drainListeners,stopOnSignals} from LOCAL_SERVER;
let release=()=>{};const released=new Promise<void>(resolve=>{release=resolve;});
let entered=false;
const server=await serveLocal(async request=>{
 if(new URL(request.url).pathname==='/slow'){entered=true;console.log('entered');await released;return new Response('finished');}
 return new Response('quick');
},0);
void (async()=>{for await(const _ of Bun.stdin.stream()){release();}})();
stopOnSignals(async()=>{console.log('draining');console.log('drained '+await drainListeners([server],Number(process.env.DEADLINE)));},
 ()=>{console.log('finish');process.exit(0);});
console.log('port '+server.port);
`;

 test('SIGTERM lets the in-flight request finish, refuses new connections, then runs the exit steps',async()=>{
  const run=child(SERVER);
  try {
   expect(await until(()=>/port \d+/.test(run.output()),10_000)).toBe(true);
   const port=Number(run.output().match(/port (\d+)/)![1]);
   const slow=request(port,'/slow');
   expect(await until(()=>run.output().includes('entered'),5_000)).toBe(true);
   run.process.kill('SIGTERM');
   expect(await until(()=>run.output().includes('draining'),5_000)).toBe(true);
   // After the grace (ACCEPT_GRACE_MS) nothing new is accepted.
   expect(await refusedWithin(port,8_000)).toBe('ECONNREFUSED');
   run.process.stdin.write('go\n');run.process.stdin.flush();
   const answer=await slow;
   expect(body(answer.raw)).toBe('finished');
   expect(await run.process.exited).toBe(0);
   expect(run.output()).toContain('drained true\nfinish\n');
  } finally {run.done();}
 },30_000);

 test('a second SIGTERM ends the drain at once, and the exit steps still run',async()=>{
  const run=child(SERVER);
  try {
   expect(await until(()=>/port \d+/.test(run.output()),10_000)).toBe(true);
   const port=Number(run.output().match(/port (\d+)/)![1]);
   const slow=request(port,'/slow');
   expect(await until(()=>run.output().includes('entered'),5_000)).toBe(true);
   run.process.kill('SIGTERM');
   expect(await until(()=>run.output().includes('draining'),5_000)).toBe(true);
   run.process.kill('SIGTERM');
   expect(await run.process.exited).toBe(0);
   expect(run.output()).toContain('finish\n');
   expect(run.output()).not.toContain('drained');
   expect(body((await slow).raw)).not.toBe('finished');
  } finally {run.done();}
 },30_000);
});

/** A stand-in Realtime upstream: answers any upgrade head with 101 and keeps the socket open. */
async function socketUpstream() {
 const sockets=new Set<Socket>();
 const server=createServer(socket=>{sockets.add(socket);socket.once('close',()=>sockets.delete(socket));socket.on('error',()=>{});
  socket.once('data',()=>socket.write('HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\nconnection: upgrade\r\n\r\n'));});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',()=>resolve()));
 return {port:(server.address() as {port:number}).port,stop(){for(const socket of sockets)socket.destroy();server.close();}};
}
/** A Realtime socket upgrade on a fresh connection; `status()` is the first status line's code. */
function upgradeSocket(port:number) {
 const socket=connect(port,'127.0.0.1');let raw='',closed=false;
 socket.on('data',chunk=>{raw+=chunk;});socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
 socket.on('connect',()=>socket.write(`GET /${RUNTIME}/realtime/v1/websocket HTTP/1.1\r\nhost: 127.0.0.1\r\nupgrade: websocket\r\nconnection: upgrade\r\n\r\n`));
 return {socket,status:()=>Number(raw.match(/^HTTP\/1\.1 (\d{3})/)?.[1]??0),closed:()=>closed};
}
const relay={upgrade:(port:number)=>()=>({ok:true as const,host:'127.0.0.1',port,head:'GET /socket HTTP/1.1\r\n\r\n'}),isUpgrade:()=>true};

describe('Realtime sockets during a drain',()=>{
 test('an open socket closes at the signal, and a new upgrade during the grace gets 503',async()=>{
  const upstream=await socketUpstream();
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('plain'),shared.port,{upgrade:relay.upgrade(upstream.port),isUpgrade:relay.isUpgrade});
  await shared.release();
  try {
   const open=upgradeSocket(server.port);
   expect(await until(()=>open.status()===101,4_000)).toBe(true);
   let drained=false;
   const draining=server.drain(3_000).then(()=>{drained=true;});
   // Closed at once, long before the grace ends: a socket never finishes on its own.
   expect(await until(()=>open.closed(),1_500)).toBe(true);
   expect(drained).toBe(false);
   const late=upgradeSocket(server.port);
   expect(await until(()=>late.status()!==0||late.closed(),2_000)).toBe(true);
   expect(late.status()).toBe(503);
   await draining;
  } finally {server.stop(true);upstream.stop();}
 },20_000);
});

/** A connection from a chosen loopback source address, made by Python since Bun's node:net
 * ignores `localAddress`. With `send`, one request and the raw answer; without, 'closed' when
 * the listener closes the connection within four seconds of the connect, else 'open'. */
async function from(source:string,port:number,send:boolean,sourcePort=0):Promise<string> {
 const script=`import socket,sys
s=socket.socket();s.bind((sys.argv[1],int(sys.argv[4])));s.settimeout(4)
out=b''
try:
 s.connect(('127.0.0.1',int(sys.argv[2])))
 if sys.argv[3]=='send':s.sendall(b'GET / HTTP/1.1\\r\\nhost: 127.0.0.1\\r\\nconnection: close\\r\\n\\r\\n')
 while True:
  part=s.recv(65536)
  if not part:break
  out+=part
 print(out.decode('latin1') if sys.argv[3]=='send' else ('closed' if not out else 'data'),end='')
except (socket.timeout,TimeoutError):print(out.decode('latin1') if sys.argv[3]=='send' else 'open',end='')
except (ConnectionResetError,BrokenPipeError,ConnectionRefusedError):print(out.decode('latin1') if sys.argv[3]=='send' else 'closed',end='')`;
 const child=Bun.spawn(['/usr/bin/python3','-I','-c',script,source,String(port),send?'send':'quiet',String(sourcePort)],{stdout:'pipe',stderr:'inherit'});
 const output=await new Response(child.stdout).text();await child.exited;
 return output;
}

describe('source checks on a fixed port',()=>{
 // The client connects from 127.0.0.3; the front's own hop to the inner server comes from
 // 127.0.0.1. An admit that takes only 127.0.0.3 tells the two apart.
 const CLIENT='127.0.0.3',OTHER='127.0.0.4';
 test('the client\'s own address is checked: an admitted client passes through the hop, another is closed before it sends anything',async()=>{
  const shared=await sharedPort();const seen:string[]=[];let handled=0;
  const server=await serveLocal(()=>{handled++;return new Response('admitted');},shared.port,{admit:source=>{seen.push(source);return source===CLIENT;}});
  await shared.release();
  try {
   expect(body(await from(CLIENT,server.port,true))).toBe('admitted');
   expect(handled).toBe(1);
   // Every check, at the front and at the inner server, saw the client, never the hop.
   expect(seen.length).toBeGreaterThanOrEqual(2);
   expect(new Set(seen)).toEqual(new Set([CLIENT]));
   // The front closes a refused source as it accepts, before the client sends a byte.
   expect(await from(OTHER,server.port,false)).toBe('closed');
   expect(seen).toContain(OTHER);
   expect(await from(OTHER,server.port,true)).toBe('');
   expect(handled).toBe(1);
  } finally {server.stop(true);}
 },20_000);

 test('the inner server listens on loopback only and refuses a connection that did not come through the front',async()=>{
  const shared=await sharedPort();let handled=0;
  const server=await serveLocal(()=>{handled++;return new Response('admitted');},shared.port,{admit:source=>source===CLIENT});
  await shared.release();
  try {
   const inner=(server as {innerPort?:number}).innerPort!;
   expect(inner).toBeGreaterThan(0);
   // From an admitted source, straight to the inner port: no hop entry, so refused.
   expect(await from(CLIENT,inner,true)).toBe('');
   expect(handled).toBe(0);
   // Not reachable on another address.
   const elsewhere=await new Promise<string>(resolve=>{const socket=connect(inner,'127.0.0.2');
    socket.on('connect',()=>{socket.destroy();resolve('connected');});socket.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));});
   expect(elsewhere).toBe('ECONNREFUSED');
  } finally {server.stop(true);}
 },20_000);
});

describe('the client address record behind a front',()=>{
 test('entries go when their connections close, so the record does not grow',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port,{admit:source=>source.replace(/^::ffff:/,'').startsWith('127.')});
  await shared.release();
  const hops=()=>(server as {admittedHops?:()=>string[]}).admittedHops!();
  try {
   for(let round=0;round<10;round++)await Promise.all(Array.from({length:20},()=>request(server.port,'/')));
   // Connections closed at once, before or while the hop connects.
   await Promise.all(Array.from({length:50},()=>new Promise<void>(resolve=>{
    const socket=connect(server.port,'127.0.0.1');socket.on('connect',()=>{socket.destroy();resolve();});socket.on('error',()=>resolve());})));
   expect(await until(()=>hops().length===0,4_000)).toBe(true);
  } finally {server.stop(true);}
 },30_000);

 test('an entry matches the hop\'s full address: the same port from another loopback address is refused',async()=>{
  const shared=await sharedPort();let handled=0;
  const server=await serveLocal(()=>{handled++;return new Response('ok');},shared.port,{admit:source=>source==='127.0.0.1'});
  await shared.release();
  const hops=()=>(server as {admittedHops?:()=>string[]}).admittedHops!();
  const kept=connect(server.port,'127.0.0.1');let raw='';kept.on('data',chunk=>{raw+=chunk;});kept.on('error',()=>{});
  try {
   kept.on('connect',()=>kept.write('GET / HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n'));
   expect(await until(()=>raw.includes('ok'),4_000)).toBe(true);
   const [hop]=hops();
   expect(hop).toMatch(/^127\.0\.0\.1:\d+$/);
   const hopPort=Number(hop!.split(':')[1]);
   // From 127.0.0.2, on the live hop's own port, straight to the inner server.
   expect(await from('127.0.0.2',(server as {innerPort?:number}).innerPort!,true,hopPort)).toBe('');
   expect(handled).toBe(1);
  } finally {kept.destroy();server.stop(true);}
 },20_000);
});

describe('quiet and partial connections at the end of the grace',()=>{
 test('a fixed port: an answer still passing through the front when the grace ends reaches the client whole',async()=>{
  const SIZE=4194304;
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('x'.repeat(SIZE)),shared.port);
  await shared.release();
  const socket=connect(server.port,'127.0.0.1');let received=0,closed=false;
  socket.on('data',chunk=>{received+=chunk.length;});socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
   // The client stops reading, so most of the answer waits in the front and its buffers.
   socket.write('GET /big HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   socket.pause();
   await Bun.sleep(300);
   // The grace ends while the client still is not reading; it reads again 300 ms later.
   const draining=server.drain(100);
   await Bun.sleep(400);
   socket.resume();
   expect(await until(()=>closed,8_000)).toBe(true);
   expect(received).toBeGreaterThan(SIZE);
   await draining;
  } finally {socket.destroy();server.stop(true);}
 },20_000);

 for(const [name,options] of [['fixed port',{}],
  ['behind the WebSocket front',{upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false}]] as const)
 test(`${name}: a request head still arriving when the grace ends is closed and does not hold the drain`,async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('quick'),shared.port,options);
  await shared.release();
  try {
   const started=performance.now();
   const draining=server.drain(300);
   const partial=connect(server.port,'127.0.0.1');let closed=false;
   partial.on('close',()=>{closed=true;});partial.on('error',()=>{});
   partial.on('connect',()=>partial.write('GET / HTTP/1.1\r\nhost: 127'));
   await draining;
   expect(performance.now()-started).toBeLessThan(4_000);
   expect(await until(()=>closed,2_000)).toBe(true);
  } finally {server.stop(true);}
 },20_000);

 test('without a front, a partial head does not hold the drain either',async()=>{
  const server=await serveLocal(()=>new Response('quick'),0);
  const partial=connect(server.port,'127.0.0.1');partial.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>partial.once('connect',()=>resolve()));
   partial.write('GET / HTTP/1.1\r\nhost: 127');
   const started=performance.now();
   await server.drain(50);
   expect(performance.now()-started).toBeLessThan(4_000);
  } finally {partial.destroy();server.stop(true);}
 },20_000);
});

test('the database proxy keeps accepting through its grace and forwards what it accepts',async()=>{
 const upstream=await echoDatabase();
 const handle=await databaseProxy({port:0,host:'127.0.0.1',target:()=>({host:'127.0.0.1',port:upstream.port})});
 try {
  let drained=false;
  const draining=handle.drain(2_000).then(()=>{drained=true;});
  const during=await session(handle.port);
  expect(await until(()=>during.received().includes(`${RUNTIME}_developer`),1_500)).toBe(true);
  during.socket.end();
  expect(drained).toBe(false);
  await draining;
 } finally {handle.stop();upstream.stop();}
},20_000);

describe('bounded bind retries',()=>{
 test('a bind that fails twice and then succeeds is retried, with each retry logged',async()=>{
  let calls=0;const lines:string[]=[];
  const value=await bindWithRetry(async()=>{if(++calls<3)throw new Error('EADDRINUSE');return 'bound';},{attempts:5,delayMs:1,log:line=>lines.push(line)});
  expect([value,calls,lines.length]).toEqual(['bound',3,2]);
  expect(lines[0]).toContain('EADDRINUSE');
 });
 test('after its attempts it throws the last error',async()=>{
  let calls=0;
  await expect(bindWithRetry(async()=>{calls++;throw new Error('attempt '+calls);},{attempts:4,delayMs:1})).rejects.toThrow('attempt 4');
  expect(calls).toBe(4);
 });
});

test('a Functions Storage listener whose bind finishes after the drain began is closed at once',async()=>{
 const bound=gate();let stopped=0;
 const set=functionsStorageListeners({read:()=>({[RUNTIME]:{functions:{storage:{host:'172.30.4.1',port:5001,subnet:'172.30.4.0/24'}}}}),
  open:async()=>{await bound.opened;return {stop(){stopped++;},async drain(){}};}});
 const reconciling=set.reconcile();
 await set.drain();
 bound.open();await reconciling;
 expect(stopped).toBe(1);
 expect(set.runtimes()).toEqual([]);
});

// lab/upstream-server.ts composes the listeners and the hold for the running version.
describe('the console process under a hold and on a stop (startGateway)',()=>{
 const PROBE='p'.repeat(43);
 /** A port held by a listener without SO_REUSEPORT, which no other listener can join, until `release`. */
 async function exclusivePort() {
  const free=await sharedPort();await free.release();
  const holder=createServer(socket=>socket.destroy());
  await new Promise<void>(resolve=>holder.listen(free.port,'127.0.0.1',()=>resolve()));
  return {port:free.port,release:()=>new Promise<void>(resolve=>holder.close(()=>resolve()))};
 }
 async function gateway({blocked,freeAfterMs=0,onStudio}:{blocked?:'console'|'database';freeAfterMs?:number;
  /** Called on each Studio tick before it binds, with the running gateway. */
  onStudio?:(running:Awaited<ReturnType<typeof startGateway>>)=>void}={}) {
  const realtime=await socketUpstream(),database=await echoDatabase();
  const storage=Bun.serve({port:0,hostname:'127.0.0.1',fetch:()=>new Response('storage')});
  const ports={console:blocked==='console'?await exclusivePort():await sharedPort(),storage:await sharedPort(),
   database:blocked==='database'?await exclusivePort():await sharedPort(),studio:await sharedPort(),external:await sharedPort()};
  const files=mkdtempSync(join(tmpdir(),'sbarbase-gateway-'));const probeToken=join(files,'probe-token');
  writeFileSync(probeToken,PROBE+'\n');
  // The database TLS material, checked for renewal every 50 ms (watchDatabaseTls).
  const tlsDirectory=join(files,'tls'),names=['127.0.0.1','::1','localhost'];
  const tls=ensureDatabaseTls(tlsDirectory,names);
  if(blocked)setTimeout(()=>void ports[blocked].release(),freeAfterMs);
  let scope:HoldScope|undefined;const studioRelease=gate();let studioEntered=false;
  const app={
   handler:async(request:Request)=>new Response('app '+new URL(request.url).pathname),
   studio:async()=>new Response('studio'),
   // Studio's own server-side calls; /slow waits for the test.
   upstream:async(request:Request)=>{if(new URL(request.url).pathname==='/slow'){studioEntered=true;await studioRelease.opened;}return new Response('upstream');},
   realtime:()=>({ok:true as const,host:'127.0.0.1',port:realtime.port,head:'GET /socket HTTP/1.1\r\n\r\n'}),
   database:new DirectDatabase(),listening(){},
   catalog:{schemaVersion:()=>1,runtimeReady:()=>true,externalDatabaseEnabled:()=>true},keys:{check(){},confirmationProbe:undefined},
  } as unknown as GatewayApplication;
  let reloads=0;
  const reload=app.database.reloadTls.bind(app.database);
  app.database.reloadTls=async renewed=>{reloads++;await reload(renewed);};
  let running:Awaited<ReturnType<typeof startGateway>>|undefined;
  running=await startGateway(app,{hold:()=>scope,consolePort:ports.console.port,
   databaseAt:{port:ports.database.port,host:'127.0.0.1'},
   endpoints:()=>({[RUNTIME]:{functions:{storage:{host:'127.0.0.1',port:ports.storage.port,subnet:'127.0.0.0/8'}},
    storage:{url:`http://127.0.0.1:${storage.port}`,tenantHost:RUNTIME+'.storage.internal'}}}),
   database:()=>({host:'127.0.0.1',port:database.port}),
   studio:()=>{if(onStudio&&running)onStudio(running);return {host:'127.0.0.1',port:ports.studio.port};},
   probeToken,bindAttempts:40,bindDelayMs:50,
   tls,externalAt:{port:ports.external.port,host:'127.0.0.1'},tlsDirectory,tlsCheckMs:50});
  // The Studio port stays held (SO_REUSEPORT, so its listener can join) until the listener is up:
  // released early, another process could take it before the first two-second tick.
  for(const [name,holder] of Object.entries(ports))if(name!==blocked&&name!=='studio')await holder.release();
  return {running:running!,ports,app,reloads:()=>reloads,
   /** Removes the server certificate, so the next renewal check makes a new one. */
   expire:()=>rmSync(join(tlsDirectory,'server.pem'),{force:true}),studio:{release:studioRelease.open,entered:()=>studioEntered},set:(value:HoldScope|undefined)=>{scope=value;},
   done(){running!.stop();app.database.stop();realtime.stop();database.stop();storage.stop(true);void ports.studio.release();rmSync(files,{recursive:true,force:true});}};
 }
 const developer=async(port:number)=>{
  const open=await session(port);
  await until(()=>open.received().length>0||open.closed(),4_000);
  open.socket.destroy();
  return open.received().includes(`${RUNTIME}_developer`)?'forwarded':open.received().includes('not running')?'refused':'other: '+open.received();
 };

 test('a management hold lets the tenant data plane flow and holds the rest; a full hold holds it all',async()=>{
  const place=await gateway();
  try {
   const {ports}=place;
   for(const [scope,tenant] of [[undefined,true],['management',true],['all',false]] as const){
    place.set(scope);
    const route=await request(ports.console.port,`/${RUNTIME}/rest/v1/items`,{method:'POST'});
    expect(tenant?body(route.raw):route.raw.slice(9,12)).toBe(tenant?`app /${RUNTIME}/rest/v1/items`:'503');
    const v1=await request(ports.console.port,'/v1/projects',{method:'POST'});
    expect(v1.raw.slice(9,12)).toBe(scope?'503':'200');
    const health=JSON.parse(body((await request(ports.console.port,'/health')).raw));
    expect(health.held).toBe(!tenant);
    const socket=upgradeSocket(ports.console.port);
    expect(await until(()=>socket.status()!==0,4_000)).toBe(true);
    expect(socket.status()).toBe(tenant?101:503);
    socket.socket.destroy();
    expect(await developer(ports.database.port)).toBe(tenant?'forwarded':'refused');
    const stored=await request(ports.storage.port,'/object/x');
    expect(tenant?body(stored.raw):stored.raw.slice(9,12)).toBe(tenant?'storage':'503');
    // The supervisor's probe token stands in for an API key under either hold, and only then.
    expect(place.app.keys.confirmationProbe!(RUNTIME,PROBE)).toBe(scope!==undefined);
   }
  } finally {place.done();}
 },30_000);

 test('a stop drains every listener: the console, the Functions Storage and the direct database all stop accepting',async()=>{
  const place=await gateway();
  try {
   const {ports}=place;
   const open=await session(ports.database.port);
   expect(await until(()=>open.received().length>0,4_000)).toBe(true);
   // The Studio internal listener opens on the first two-second tick; a call on it is in flight.
   const studioUp=async()=>{for(const deadline=Date.now()+8_000;Date.now()<deadline;await Bun.sleep(50)){
    const answer=await request(ports.studio.port,'/ready');if(body(answer.raw)==='upstream')return true;}return false;};
   expect(await studioUp()).toBe(true);
   await ports.studio.release();
   const studioCall=request(ports.studio.port,'/slow');
   expect(await until(()=>place.studio.entered(),4_000)).toBe(true);
   // The external listener is up (it answers a cleartext startup) before the drain.
   expect(await cleartext(ports.external.port)).toContain('requires TLS');
   let finished:boolean|undefined;
   const draining=place.running.drain(10_000,0).then(value=>{finished=value;});
   for(const port of [ports.console.port,ports.storage.port,ports.database.port,ports.studio.port,ports.external.port])
    expect(await connectRefusedWithin(port,8_000)).toBe(true);
   // The open database session and the Studio call hold the drain until they end.
   expect(finished).toBeUndefined();
   place.studio.release();
   expect(body((await studioCall).raw)).toBe('upstream');
   open.socket.end();
   await draining;
   expect(finished).toBe(true);
  } finally {place.studio.release();place.done();}
 },30_000);

 test('a Studio listener whose bind finishes after the drain began closes at once instead of accepting through it',async()=>{
  let draining:Promise<boolean>|undefined;
  // The stop arrives while the first Studio tick is binding its listener.
  const place=await gateway({onStudio:running=>{if(!draining)queueMicrotask(()=>{draining=running.drain(4_000,0);});}});
  try {
   expect(await until(()=>!!draining,8_000)).toBe(true);
   expect(await draining!).toBe(true);
   await place.ports.studio.release();
   expect(await connectRefusedWithin(place.ports.studio.port,4_000)).toBe(true);
  } finally {place.done();}
 },30_000);

 test('the TLS renewal check reloads the listeners while running and never after a drain began',async()=>{
  const place=await gateway();
  try {
   place.expire();
   expect(await until(()=>place.reloads()>0,8_000)).toBe(true);
   await place.running.drain(5_000,0);
   const before=place.reloads();
   place.expire();
   await Bun.sleep(500);
   expect(place.reloads()).toBe(before);
  } finally {place.done();}
 },30_000);

 for(const blocked of ['console','database'] as const)
 test(`a fixed ${blocked} port still held by another process is retried until it frees`,async()=>{
  const started=performance.now();
  // The holder, which shares nothing, lets go after 300 ms: several retries later.
  const place=await gateway({blocked,freeAfterMs:300});
  try {
   expect(performance.now()-started).toBeGreaterThanOrEqual(290);
   const port=blocked==='console'?place.ports.console.port:place.ports.database.port;
   expect(await new Promise<string>(resolve=>{const socket=connect(port,'127.0.0.1');
    socket.on('connect',()=>{socket.destroy();resolve('connected');});socket.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));})).toBe('connected');
  } finally {place.done();}
 },30_000);
});

// A kept-alive connection's next request, already sent when the stop arrives, is answered.
describe('requests already sent on kept-alive connections when the stop arrives',()=>{
 const WORKER=`
import {serveLocal,drainListeners,stopOnSignals} from LOCAL_SERVER;
const busy=(ms:number)=>{const until=performance.now()+ms;while(performance.now()<until){}};
let stop=()=>{};
const server=await serveLocal(async request=>{
 const path=new URL(request.url).pathname;
 // Keeps the event loop busy, so what arrives meanwhile waits unread.
 if(path==='/block'){busy(600);return new Response('blocked');}
 // Starts the stop, then keeps the loop busy well past the end of the grace.
 if(path==='/stop-and-block'){stop();busy(3_000);return new Response('stopping');}
 if(request.method==='POST')return new Response(process.env.TAG+' wrote '+(await request.arrayBuffer()).byteLength);
 return new Response(process.env.TAG+' ok');
},Number(process.argv[2]));
stop=stopOnSignals(async()=>{console.log('draining');await drainListeners([server],10_000);},()=>{console.log('finish');process.exit(0);});
console.log('port '+server.port);
`;
 const place=()=>{
  const directory=mkdtempSync(join(tmpdir(),'sbarbase-kept-'));
  const script=join(directory,'worker.ts');
  writeFileSync(script,WORKER.replaceAll('LOCAL_SERVER',JSON.stringify(join(import.meta.dir,'../src/http/local-server.ts'))));
  return {script,done(){rmSync(directory,{recursive:true,force:true});}};
 };
 const start=(script:string,port:number,tag:string)=>{
  const spawned=Bun.spawn(['bun',script,String(port)],{env:{...process.env,TAG:tag},stdout:'pipe',stderr:'inherit'});let output='';
  void (async()=>{for await(const chunk of spawned.stdout)output+=new TextDecoder().decode(chunk);})();
  return {spawned,output:()=>output,port:async()=>{await until(()=>/port \d+/.test(output),10_000);return Number(output.match(/port (\d+)/)?.[1]);}};
 };
 /** A kept-alive client connection that parses answers one at a time. */
 const client=(port:number)=>{
  const socket=connect(port,'127.0.0.1');let raw='',closed=false;
  socket.on('data',chunk=>{raw+=chunk.toString('latin1');});socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
  const connected=new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
  /** The next whole answer: its body and whether it closes the connection, or undefined once closed without one. */
  const answer=async():Promise<{body:string;close:boolean}|undefined>=>{
   // An answer that has not come in 10 s counts as none, rather than hanging the test.
   for(const deadline=performance.now()+10_000;performance.now()<deadline;){
    const end=raw.indexOf('\r\n\r\n');
    if(end>=0){
     const head=raw.slice(0,end).toLowerCase();const rest=raw.slice(end+4);
     const length=head.match(/\r\ncontent-length: *(\d+)/)?.[1];
     let size=-1,text='';
     if(length!==undefined&&rest.length>=Number(length)){size=Number(length);text=rest.slice(0,size);}
     else if(/\r\ntransfer-encoding: *chunked/.test(head)&&rest.includes('0\r\n\r\n')){size=rest.indexOf('0\r\n\r\n')+5;text=body(raw.slice(0,end+4+size));}
     if(size>=0){raw=rest.slice(size);return {body:text,close:/\r\nconnection: *close/.test(head)};}
    }
    if(closed)return undefined;
    await Bun.sleep(1);
   }
   return undefined;
  };
  return {socket,connected,answer,closed:()=>closed};
 };
 const post=(body:string)=>`POST /write HTTP/1.1\r\nhost: 127.0.0.1\r\ncontent-type: text/plain\r\ncontent-length: ${body.length}\r\n\r\n${body}`;

 for(const [name,fixed] of [['fixed port',true],['ephemeral port',false]] as const)
 test(`${name}: a POST sent while the event loop is busy, just before SIGTERM, gets its answer`,async()=>{
  const files=place();const shared=fixed?await sharedPort():undefined;
  const worker=start(files.script,shared?.port??0,'A');
  try {
   const port=await worker.port();await shared?.release();
   const kept=client(port);await kept.connected;
   kept.socket.write('GET / HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   expect((await kept.answer())?.body).toBe('A ok');
   // Another client keeps the worker busy for 600 ms; the POST and then SIGTERM arrive meanwhile.
   const blocking=request(port,'/block');
   await Bun.sleep(100);
   kept.socket.write(post('hello'));
   await Bun.sleep(100);
   worker.spawned.kill('SIGTERM');
   const answer=await kept.answer();
   expect(answer?.body).toBe('A wrote 5');
   expect(answer?.close).toBe(true);
   expect(body((await blocking).raw)).toBe('blocked');
   expect(await worker.spawned.exited).toBe(0);
  } finally {worker.spawned.kill('SIGKILL');files.done();}
 },30_000);

 for(const [name,fixed] of [['fixed port',true],['ephemeral port',false]] as const)
 test(`${name}: a POST that arrives while the event loop is busy across the end of the grace gets its answer`,async()=>{
  const files=place();const shared=fixed?await sharedPort():undefined;
  const worker=start(files.script,shared?.port??0,'A');
  try {
   const port=await worker.port();await shared?.release();
   const kept=client(port);await kept.connected;
   kept.socket.write('GET / HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   expect((await kept.answer())?.body).toBe('A ok');
   // The stop starts inside a request that then holds the loop for 3 s, past the 300 ms grace;
   // the POST arrives meanwhile and is read only after the grace's timer has fired.
   const blocking=request(port,'/stop-and-block');
   await Bun.sleep(100);
   kept.socket.write(post('hello'));
   const answer=await kept.answer();
   expect(answer?.body).toBe('A wrote 5');
   expect(answer?.close).toBe(true);
   await blocking;
   expect(await worker.spawned.exited).toBe(0);
  } finally {worker.spawned.kill('SIGKILL');files.done();}
 },30_000);

 test('under load on kept-alive connections, a stop with a second process on the port loses no request on a reused connection',async()=>{
  const files=place();const shared=await sharedPort();
  const first=start(files.script,shared.port,'A'),second=start(files.script,shared.port,'B');
  const counts={answered:0,reusedLost:0,freshFailed:0,byA:0,byB:0};
  try {
   await first.port();await second.port();await shared.release();
   const stopAt=performance.now()+2_500;
   const worker=async()=>{
    let open:ReturnType<typeof client>|undefined,served=0;
    while(performance.now()<stopAt){
     if(!open||open.closed()){open=client(shared.port);served=0;
      if(await Promise.race([open.connected.then(()=>true),until(()=>open!.closed(),3_000).then(()=>false)])===false){counts.freshFailed++;open=undefined;continue;}}
     open.socket.write(post('x'.repeat(64)));
     const answer=await open.answer();
     if(!answer){if(served)counts.reusedLost++;else counts.freshFailed++;open=undefined;continue;}
     counts.answered++;served++;
     if(answer.body.startsWith('A '))counts.byA++;else counts.byB++;
     if(answer.close){open.socket.destroy();open=undefined;}
    }
    open?.socket.destroy();
   };
   const workers=Array.from({length:20},worker);
   await Bun.sleep(800);
   first.spawned.kill('SIGTERM');
   await Promise.all(workers);
   expect(await first.spawned.exited).toBe(0);
   expect(counts.reusedLost).toBe(0);
   expect(counts.byA).toBeGreaterThan(0);expect(counts.byB).toBeGreaterThan(0);
   expect(counts.answered).toBeGreaterThan(100);
  } finally {first.spawned.kill('SIGKILL');second.spawned.kill('SIGKILL');files.done();}
 },60_000);
});

// A loop that stays busy after the grace: the listener still stops accepting within SETTLE_MS_MAX.
describe('a saturated event loop at the end of the grace',()=>{
 test('the listener stops accepting within the bound, however late the loop\'s timers run',async()=>{
  // Grace 100 ms plus SETTLE_MS_MAX plus a turn or two of the saturated loop. The best of three
  // attempts is taken, so a loaded host delaying one of them does not fail the test, while a
  // bound much above SETTLE_MS_MAX fails every attempt.
  const attempt=async():Promise<number> => {
   const shared=await sharedPort();
   const server=await serveLocal(()=>new Response('ok'),shared.port);
   await shared.release();
   // Another process probes the port until it is refused and prints the wall clock time.
   const probe=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,sys,time
end=time.time()+20
while time.time()<end:
 s=socket.socket()
 try:
  s.connect(('127.0.0.1',int(sys.argv[1])));s.close()
 except ConnectionRefusedError:
  print(int(time.time()*1000));sys.exit(0)
 except OSError:
  pass
 time.sleep(0.02)
print('never')`,String(server.port)],{stdout:'pipe',stderr:'inherit'});
   let saturating=true;
   try {
    await Bun.sleep(200);
    // Every turn of the loop now holds it for 100 ms, so no 10 ms wait ever ends on time.
    const spin=()=>{if(!saturating)return;const until=performance.now()+100;while(performance.now()<until){}setTimeout(spin,0);};
    const started=Date.now();spin();
    await server.drain(100);
    saturating=false;
    const output=(await new Response(probe.stdout).text()).trim();
    expect(output).not.toBe('never');
    return Number(output)-started;
   } finally {saturating=false;probe.kill();server.stop(true);}
  };
  const times:number[]=[];
  for(let i=0;i<3;i++){times.push(await attempt());if(Math.min(...times)<1_800)break;}
  const best=Math.min(...times);
  expect(best).toBeGreaterThanOrEqual(90);
  // 1.8 s: without the bound it would be 50 rounds of at least 100 ms, 5 s or more; with a bound
  // of 3 s it would be about 3.2 s.
  expect(best).toBeLessThan(1_800);
  expect(SETTLE_MS_MAX).toBeLessThanOrEqual(1_000);
 },60_000);

 const WORKER=`
import {serveLocal,drainListeners,stopOnSignals} from LOCAL_SERVER;
let handled=0;
const server=await serveLocal(async()=>{handled++;const until=performance.now()+Number(process.env.BUSY);while(performance.now()<until){}return new Response(process.env.TAG!);},Number(process.argv[2]));
stopOnSignals(async()=>{const drained=await drainListeners([server],Number(process.env.DEADLINE));console.log(JSON.stringify({handled,drained}));},
 ()=>process.exit(0));
console.log('ready');
`;
 test('under a load that keeps the loop busy, the stop ends before its deadline and every request it took is answered',async()=>{
  const place=mkdtempSync(join(tmpdir(),'sbarbase-saturated-'));
  const script=join(place,'worker.ts');
  writeFileSync(script,WORKER.replaceAll('LOCAL_SERVER',JSON.stringify(join(import.meta.dir,'../src/http/local-server.ts'))));
  const shared=await sharedPort();
  const start=(tag:string,busy:number)=>{
   const spawned=Bun.spawn(['bun',script,String(shared.port)],{env:{...process.env,TAG:tag,BUSY:String(busy),DEADLINE:'8000'},stdout:'pipe',stderr:'inherit'});
   let output='';void (async()=>{for await(const chunk of spawned.stdout)output+=new TextDecoder().decode(chunk);})();
   return {spawned,output:()=>output};
  };
  const first=start('A',100),second=start('B',5);
  const answers={A:0,B:0,failed:0} as Record<string,number>;
  try {
   expect(await until(()=>first.output().includes('ready')&&second.output().includes('ready'),10_000)).toBe(true);
   await shared.release();
   // Eight clients keep the first process's loop busy until it has exited, at most 11 s: longer
   // than its 8 s deadline if it kept accepting.
   let stopAt=performance.now()+11_000;
   const client=async()=>{while(performance.now()<stopAt){
    const answer=await request(shared.port,'/');const tag=body(answer.raw);
    if(tag==='A'||tag==='B')answers[tag]!++;else answers.failed!++;
   }};
   const clients=Array.from({length:8},client);
   await Bun.sleep(1_000);
   const signalled=performance.now();
   first.spawned.kill('SIGTERM');
   expect(await first.spawned.exited).toBe(0);
   // It stopped before its 8 s deadline, while the clients were still sending.
   expect(performance.now()-signalled).toBeLessThan(7_000);
   stopAt=Math.min(stopAt,performance.now()+500);
   await Promise.all(clients);
   const report=JSON.parse(first.output().split('\n').find(line=>line.startsWith('{'))!);
   expect(report.drained).toBe(true);
   // Nothing the first process took was cut: each request it handled reached its client.
   expect(answers.A).toBe(report.handled);
   expect(answers.B).toBeGreaterThan(0);
  } finally {first.spawned.kill('SIGKILL');second.spawned.kill('SIGKILL');rmSync(place,{recursive:true,force:true});}
 },60_000);
});

describe('the external (TLS only) database listener',()=>{
 const tlsFor=()=>{const directory=mkdtempSync(join(tmpdir(),'sbarbase-external-'));
  return {tls:ensureDatabaseTls(join(directory,'tls'),['127.0.0.1','::1','localhost']),done:()=>rmSync(directory,{recursive:true,force:true})};};
 const external=async(direct:DirectDatabase,port:number,tls:ReturnType<typeof ensureDatabaseTls>,log:(line:string)=>void)=>
  direct.startExternal({port,host:'127.0.0.1',target:()=>undefined,log,tls,guard:new ExternalGuard(()=>true,()=>{})},tls.ca);

 test('two listeners share its fixed port, and both take connections',async()=>{
  const material=tlsFor();const shared=await sharedPort();
  const first=new DirectDatabase(),second=new DirectDatabase();const seen={first:0,second:0};
  try {
   await external(first,shared.port,material.tls,()=>{seen.first++;});
   await external(second,shared.port,material.tls,()=>{seen.second++;});
   await shared.release();
   for(let i=0;i<40&&!(seen.first&&seen.second);i++)expect(await cleartext(shared.port)).toContain('requires TLS');
   expect(seen.first).toBeGreaterThan(0);expect(seen.second).toBeGreaterThan(0);
  } finally {first.stop();second.stop();material.done();}
 },20_000);

 test('a connection accepted during the grace completes its TLS handshake after the grace ended',async()=>{
  const material=tlsFor();const shared=await sharedPort();const direct=new DirectDatabase();
  try {
   await external(direct,shared.port,material.tls,()=>{});
   await shared.release();
   const draining=direct.drain(300);
   // Accepted now, during the grace; it starts TLS only once the listener no longer accepts.
   const socket=connect(shared.port,'127.0.0.1');socket.on('error',()=>{});
   await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
   expect(await connectRefusedWithin(shared.port,4_000)).toBe(true);
   const sslRequest=Buffer.alloc(8);sslRequest.writeInt32BE(8,0);sslRequest.writeInt32BE(80877103,4);
   socket.write(sslRequest);
   const answer=await new Promise<string>(resolve=>socket.once('data',chunk=>resolve(chunk.toString('latin1'))));
   expect(answer).toBe('S');
   const {connect:tlsConnect}=await import('node:tls');
   const secure=await new Promise<string>(resolve=>{
    const tls=tlsConnect({socket,ca:material.tls.ca,servername:'localhost'});
    tls.once('secureConnect',()=>{resolve(tls.authorized?'secure':'unauthorized');tls.destroy();});
    tls.once('error',error=>resolve('error: '+error.message));
   });
   expect(secure).toBe('secure');
   await draining;
  } finally {direct.stop();material.done();}
 },20_000);

 test('it drains with the grace: still accepting during it, refusing after it',async()=>{
  const material=tlsFor();const shared=await sharedPort();const direct=new DirectDatabase();
  try {
   await external(direct,shared.port,material.tls,()=>{});
   await shared.release();
   let drained=false;
   const draining=direct.drain(1_500).then(()=>{drained=true;});
   expect(await cleartext(shared.port)).toContain('requires TLS');
   expect(await connectRefusedWithin(shared.port,8_000)).toBe(true);
   await draining;
   expect(drained).toBe(true);
  } finally {direct.stop();material.done();}
 },20_000);
});

describe('connections whose clients hang up mid-request',()=>{
 for(const [name,fixed] of [['behind the front, with a source check',true],['without a front',false]] as const)
 test(`${name}: nothing is kept for them, and a drain after them settles at once`,async()=>{
  const shared=fixed?await sharedPort():undefined;
  const never=gate();let entered=0;
  const server=await serveLocal(async()=>{entered++;await never.opened;return new Response('late');},shared?.port??0,
   fixed?{admit:source=>source.replace(/^::ffff:/,'').startsWith('127.')}:{});
  await shared?.release();
  const handle=server as {inFlight:()=>number;admittedHops?:()=>string[]};
  try {
   // 200 requests, each abandoned by its client once the handler is running.
   for(let round=0;round<10;round++)await Promise.all(Array.from({length:20},async()=>{
    const socket=connect(server.port,'127.0.0.1');socket.on('error',()=>{});
    const before=entered;
    socket.on('connect',()=>socket.write('GET /slow HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n'));
    await until(()=>entered>before,2_000);
    socket.destroy();
   }));
   expect(entered).toBeGreaterThanOrEqual(200);
   expect(await until(()=>handle.inFlight()===0&&(handle.admittedHops?.().length??0)===0&&server.connections()===0,5_000)).toBe(true);
   const started=performance.now();
   await server.drain(0);
   expect(performance.now()-started).toBeLessThan(1_500);
  } finally {never.open();server.stop(true);}
 },30_000);
});

describe('the grace on an idle loop',()=>{
 for(const [name,fixed] of [['fixed port',true],['ephemeral port',false]] as const)
 test(`${name}: the listener stops accepting soon after a short grace`,async()=>{
  const shared=fixed?await sharedPort():undefined;
  const server=await serveLocal(()=>new Response('ok'),shared?.port??0);
  await shared?.release();
  try {
   const started=performance.now();
   const draining=server.drain(100);
   expect(await connectRefusedWithin(server.port,4_000)).toBe(true);
   // The grace (100 ms) plus one settle round on an idle loop; a full SETTLE_MS_MAX would be 1.1 s.
   expect(performance.now()-started).toBeLessThan(800);
   await draining;
  } finally {server.stop(true);}
 },20_000);
});

test('a client that stops reading is closed within the flush bound once the other end closed',async()=>{
 const shared=await sharedPort();
 const server=await serveLocal(()=>new Response('x'.repeat(4_194_304)),shared.port,{flushMs:300});
 await shared.release();
 const socket=connect(server.port,'127.0.0.1');let closed=false;
 socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
 try {
  await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
  socket.write('GET /big HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
  // It never reads again; the answer fills the buffers, the inner end closes at the end of the grace.
  socket.pause();
  await Bun.sleep(300);
  const started=performance.now();
  await server.drain(0);
  // The front gave up on the client within the flush bound (300 ms) plus the inner 1 s backstop.
  expect(performance.now()-started).toBeLessThan(3_000);
  expect(server.connections()).toBe(0);
 } finally {socket.destroy();server.stop(true);}
},20_000);

describe('a client that takes nothing of its answer, in normal running',()=>{
 test('its connection closes within the stall bound, with its hop, and nothing is left',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('x'.repeat(16*1_048_576)),shared.port,{stallMs:300});
  await shared.release();
  const handle=server as {inFlight:()=>number};
  const socket=connect(server.port,'127.0.0.1');let closed=false;
  socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
   socket.write('GET /big HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   socket.pause();
   const started=performance.now();
   // The answer waits at the inner server, held back by the front, not buffered whole in the front.
   await Bun.sleep(150);
   expect(handle.inFlight()).toBe(1);
   // No drain: only the stall check closes it.
   expect(await until(()=>server.connections()===0&&handle.inFlight()===0,5_000)).toBe(true);
   expect(performance.now()-started).toBeGreaterThanOrEqual(250);
   socket.resume();
   expect(await until(()=>closed,4_000)).toBe(true);
  } finally {socket.destroy();server.stop(true);}
 },20_000);

 test('the client cut off with data still queued sees a reset, not an orderly end',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('x'.repeat(16*1_048_576)),shared.port,{stallMs:300});
  await shared.release();
  try {
   // It reads nothing for 1.5 s, five times the stall bound, then reads on.
   const read=await pythonRead(server.port,closeRequest,{rcvbuf:4096,delayMs:1_500,timeoutMs:10_000});
   expect(read.how).toBe('reset');
   expect(read.total).toBeLessThan(16*1_048_576);
  } finally {server.stop(true);}
 },30_000);

 test('a slow client that keeps reading gets the whole of a large answer',async()=>{
  const SIZE=12*1_048_576;
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('y'.repeat(SIZE)),shared.port,{stallMs:300});
  await shared.release();
  try {
   // A small receive buffer read 32 KiB at most every 15 ms: far slower than the server, and more
   // than the kernel buffers hold, so what waits in the front is taken bit by bit for seconds.
   const reader=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,sys,time
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,16384)
s.connect(('127.0.0.1',int(sys.argv[1])))
s.sendall(b'GET /big HTTP/1.1\\r\\nhost: 127.0.0.1\\r\\nconnection: close\\r\\n\\r\\n')
total=0
while True:
 part=s.recv(32768)
 if not part:break
 total+=len(part);time.sleep(0.015)
print(total)`,String(server.port)],{stdout:'pipe',stderr:'inherit'});
   const total=Number((await new Response(reader.stdout).text()).trim());
   expect(total).toBeGreaterThan(SIZE);
  } finally {server.stop(true);}
 },60_000);
});

/** A Python client: sends `request`, waits `delayMs`, then reads until the connection ends
 * (`rcvbuf` sizes its receive buffer). The bytes it got and how it ended: eof, reset or timeout. */
async function pythonRead(port:number,request:string,{rcvbuf=4096,delayMs=0,timeoutMs=10_000}:{rcvbuf?:number;delayMs?:number;timeoutMs?:number}={}) {
 const script=`import socket,sys,time
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,int(sys.argv[3]))
s.settimeout(float(sys.argv[5]));total=0;how='eof'
try:
 s.connect(('127.0.0.1',int(sys.argv[1])));s.sendall(sys.argv[2].encode('latin1'))
 time.sleep(float(sys.argv[4]))
 while True:
  part=s.recv(65536)
  if not part:break
  total+=len(part)
except (socket.timeout,TimeoutError):how='timeout'
except (ConnectionResetError,BrokenPipeError):how='reset'
print(total,how)`;
 const child=Bun.spawn(['/usr/bin/python3','-I','-c',script,String(port),request,String(rcvbuf),String(delayMs/1000),String(timeoutMs/1000)],{stdout:'pipe',stderr:'inherit'});
 const [total,how]=(await new Response(child.stdout).text()).trim().split(' ');
 return {total:Number(total),how};
}
const closeRequest='GET /big HTTP/1.1\r\nhost: 127.0.0.1\r\nconnection: close\r\n\r\n';

describe('a front whose port cannot be taken',()=>{
 const fds=()=>readdirSync('/proc/self/fd').length;
 test('leaves nothing behind, for a port held without SO_REUSEPORT and for an address not on this host',async()=>{
  const free=await sharedPort();await free.release();
  const holder=createServer();
  await new Promise<void>(resolve=>holder.listen(free.port,'127.0.0.1',()=>resolve()));
  try {
   const before=fds();
   for(let i=0;i<20;i++){
    await expect(serveLocal(()=>new Response('x'),free.port)).rejects.toThrow();
    await expect(serveLocal(()=>new Response('x'),free.port,{host:'192.0.2.1'})).rejects.toThrow();
   }
   await Bun.sleep(100);
   // Forty failed binds; each would leave an inner listener and its descriptor.
   expect(fds()-before).toBeLessThan(5);
  } finally {holder.close();}
 },30_000);
});

describe('the front in normal running',()=>{
 test('an answer whose connection then closes reaches a client that reads late, whole, with no reset',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('w'.repeat(1_000_000)),shared.port);
  await shared.release();
  try {
   const read=await pythonRead(server.port,closeRequest,{rcvbuf:4096,delayMs:500});
   expect(read.how).toBe('eof');
   expect(read.total).toBeGreaterThan(1_000_000);
  } finally {server.stop(true);}
 },20_000);

 test('during a drain too, such an answer reaches a late reader whole',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(async()=>{await Bun.sleep(100);return new Response('w'.repeat(1_000_000));},shared.port);
  await shared.release();
  try {
   const reading=pythonRead(server.port,closeRequest,{rcvbuf:4096,delayMs:600});
   await Bun.sleep(50);
   const draining=server.drain(200);
   const read=await reading;
   expect(read.how).toBe('eof');
   expect(read.total).toBeGreaterThan(1_000_000);
   await draining;
  } finally {server.stop(true);}
 },20_000);

 test('a client that never reads an answer whose connection then closes is closed by the stall bound, with no drain',async()=>{
  const shared=await sharedPort();
  // More than the kernel buffers take for a client that does not read, so part waits in the front.
  const server=await serveLocal(()=>new Response('v'.repeat(1_900_000)),shared.port,{stallMs:300});
  await shared.release();
  const client=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,sys,time
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
s.connect(('127.0.0.1',int(sys.argv[1])));s.sendall(sys.argv[2].encode('latin1'));time.sleep(15)`,String(server.port),closeRequest],{stderr:'inherit'});
  try {
   expect(await until(()=>server.connections()===1,4_000)).toBe(true);
   const started=performance.now();
   // No drain: the inner end closed after its answer, and the stall rule ends the front's end.
   expect(await until(()=>server.connections()===0,6_000)).toBe(true);
   expect(performance.now()-started).toBeLessThan(3_000);
  } finally {client.kill();server.stop(true);}
 },20_000);

 test('a large download to a reader that keeps up is not slowed by the front',async()=>{
  const SIZE=16*1_048_576;
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response(new Uint8Array(SIZE)),shared.port);
  await shared.release();
  try {
   // A small receive buffer read as fast as it fills: the kernel's space opens in small steps,
   // each answered by Bun's drain. Best of three, so a loaded host delaying one run does not
   // fail it; without the drain callback every run takes seconds.
   const times:number[]=[];
   for(let i=0;i<3;i++){
    const started=performance.now();
    const read=await pythonRead(server.port,closeRequest,{rcvbuf:4096,timeoutMs:20_000});
    expect(read.total).toBeGreaterThan(SIZE);
    times.push(performance.now()-started);
    if(times[i]!<2_500)break;
   }
   // Measured: 64 MiB in about 200 ms; flushing only on the stall check took 25 s.
   expect(Math.min(...times)).toBeLessThan(2_500);
  } finally {server.stop(true);}
 },60_000);

 test('a handler slower than the stall bound, then a large answer to a client that waits a moment, arrives whole',async()=>{
  const SIZE=12*1_048_576;
  const shared=await sharedPort();
  const server=await serveLocal(async()=>{await Bun.sleep(1_000);return new Response('y'.repeat(SIZE));},shared.port,{stallMs:300});
  await shared.release();
  try {
   // The answer starts 1 s in, the client reads from 1.15 s: the stall bound counts from the
   // answer's first byte, not from the connection's start.
   const read=await pythonRead(server.port,closeRequest,{rcvbuf:16384,delayMs:1_150,timeoutMs:20_000});
   expect(read.total).toBeGreaterThan(SIZE);
  } finally {server.stop(true);}
 },30_000);

 test(`at most ${MAX_FRONT_CONNECTIONS} connections are held; the next ones close`,async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port);
  await shared.release();
  const sockets:Socket[]=[];let closed=0;
  try {
   for(let i=0;i<MAX_FRONT_CONNECTIONS+20;i++){
    const socket=connect(server.port,'127.0.0.1');socket.on('error',()=>{});socket.on('close',()=>{closed++;});sockets.push(socket);
    if(i%100===99)await Bun.sleep(20);
   }
   expect(await until(()=>closed>=20,8_000)).toBe(true);
   await Bun.sleep(200);
   expect(server.connections()).toBe(MAX_FRONT_CONNECTIONS);
   expect(closed).toBe(20);
  } finally {for(const socket of sockets)socket.destroy();server.stop(true);}
 },30_000);

 test('the inner server listens on 127.0.0.1 also when the front binds another address',async()=>{
  const free=await sharedPort();await free.release();
  const server=await serveLocal(()=>new Response('ok'),free.port,{host:'127.0.0.2'});
  const inner=(server as {innerPort?:number}).innerPort!;
  const reach=(host:string)=>new Promise<string>(resolve=>{const socket=connect(inner,host);
   socket.on('connect',()=>{socket.destroy();resolve('connected');});socket.on('error',error=>resolve((error as NodeJS.ErrnoException).code??'error'));});
  try {
   expect(await reach('127.0.0.1')).toBe('connected');
   expect(await reach('127.0.0.2')).toBe('ECONNREFUSED');
  } finally {server.stop(true);}
 });
});

describe('the drain and requests in flight',()=>{
 for(const [name,options] of [['fixed port',{}],['behind the WebSocket front',{upgrade:()=>({ok:false as const,status:404,message:'Unknown route'}),isUpgrade:()=>false}]] as const)
 test(`${name}: a pipelined request still in flight at the grace end, longer than the flush bound, is answered`,async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(async request=>{
   if(new URL(request.url).pathname==='/slow'){await Bun.sleep(2_500);return new Response('slow');}
   return new Response('fast');
  },shared.port,{...options,flushMs:300});
  await shared.release();
  const socket=connect(server.port,'127.0.0.1');let raw='';
  socket.on('data',chunk=>{raw+=chunk;});socket.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
   // Both in one write: /fast is answered first, then /slow runs across the end of the grace.
   socket.write('GET /fast HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\nGET /slow HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n');
   expect(await until(()=>raw.includes('fast'),4_000)).toBe(true);
   await Bun.sleep(200);
   await server.drain(100);
   expect(raw).toContain('slow');
  } finally {socket.destroy();server.stop(true);}
 },20_000);

 test('connections that arrive while the loop is busy across the end of the grace are all answered',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port);
  await shared.release();
  // Clients in another process connect only while the loop is busy, from 100 to 400 ms after
  // the drain starts, and report how each ended.
  const started=Date.now();
  const clients=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,sys,threading,time
port=int(sys.argv[1]);begin=float(sys.argv[2])+0.1;end=float(sys.argv[2])+0.4;counts={'ok':0,'refused':0,'lost':0};lock=threading.Lock()
def run():
 while time.time()<begin:time.sleep(0.002)
 while time.time()<end:
  s=socket.socket();s.settimeout(4)
  try:
   s.connect(('127.0.0.1',port))
  except (ConnectionRefusedError,socket.timeout,TimeoutError):
   with lock:counts['refused']+=1
   continue
  try:
   s.sendall(b'GET / HTTP/1.1\\r\\nhost: 127.0.0.1\\r\\nconnection: close\\r\\n\\r\\n');data=b''
   while True:
    part=s.recv(4096)
    if not part:break
    data+=part
   key='ok' if b'ok' in data else 'lost'
  except Exception:key='lost'
  with lock:counts[key]+=1
  s.close()
ts=[threading.Thread(target=run) for _ in range(16)]
[t.start() for t in ts];[t.join() for t in ts]
print(counts['ok'],counts['refused'],counts['lost'])`,String(server.port),String(started/1000+0.5)],{stdout:'pipe',stderr:'inherit'});
  try {
   await Bun.sleep(Math.max(0,started+500-Date.now()));
   const draining=server.drain(100);
   // The loop is held in 40 ms slices with 1 ms gaps from just before the grace ends until
   // after the clients stopped, then is free. Each 10 ms wait of settled() then ends late, so it
   // waits again; the listener closes only once a wait ends on time, when the loop has polled
   // and taken what queued during the last slice.
   await Bun.sleep(80);
   for(let slice=0;slice<13;slice++){const until=performance.now()+40;while(performance.now()<until){}await Bun.sleep(1);}
   await draining;
   const [ok,refused,lost]=(await new Response(clients.stdout).text()).trim().split(' ').map(Number);
   expect(ok).toBeGreaterThan(0);
   // It kept accepting all the while the loop was busy, and answered everything it took.
   expect(refused).toBe(0);
   expect(lost).toBe(0);
  } finally {clients.kill();server.stop(true);}
 },30_000);
});

/** Established TCP connections to a local port, from the kernel's table (both ends count). */
function establishedTo(port:number):number {
 const hex=port.toString(16).toUpperCase().padStart(4,'0');
 return readFileSync('/proc/net/tcp','utf8').split('\n').slice(1).filter(line=>{
  const columns=line.trim().split(/\s+/);
  return columns[1]?.endsWith(':'+hex)&&columns[3]==='01';
 }).length;
}

describe('the front\'s own tables and timers',()=>{
 test('every connection\'s hop entry goes when the connection closes',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port);
  await shared.release();
  const handle=server as {trackedHops:()=>number};
  try {
   for(let round=0;round<5;round++)await Promise.all(Array.from({length:40},()=>request(server.port,'/')));
   expect(await until(()=>handle.trackedHops()===0&&server.connections()===0,5_000)).toBe(true);
  } finally {server.stop(true);}
 },20_000);

 test('stopping a front leaves none of its timers running',async()=>{
  const live=new Set<unknown>();
  const {setInterval:realSet,clearInterval:realClear}=globalThis;
  globalThis.setInterval=((handler:() => void,ms?:number)=>{const id=realSet(handler,ms);live.add(id);return id;}) as unknown as typeof setInterval;
  globalThis.clearInterval=((id?:Parameters<typeof clearInterval>[0])=>{live.delete(id);return realClear(id);}) as unknown as typeof clearInterval;
  try {
   const before=live.size;
   for(let i=0;i<5;i++){
    const shared=await sharedPort();
    const server=await serveLocal(()=>new Response('ok'),shared.port);
    await shared.release();
    server.stop(true);
   }
   await Bun.sleep(50);
   expect(live.size-before).toBe(0);
  } finally {globalThis.setInterval=realSet;globalThis.clearInterval=realClear;}
 },20_000);

 test('clients that connect and reset at once leave no connection to the inner server',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port);
  await shared.release();
  const inner=(server as {innerPort?:number}).innerPort!;
  const established=()=>establishedTo(inner);
  try {
   // 2000 connections that reset at once, from four processes: some are reset before their hop
   // to the inner server has finished connecting, which is the case under test.
   const bursts=Array.from({length:4},()=>Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,struct,sys
for _ in range(500):
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
 s.connect(('127.0.0.1',int(sys.argv[1])));s.close()`,String(server.port)],{stderr:'inherit'}));
   await Promise.all(bursts.map(burst=>burst.exited));
   // Closed within a second; left alone they would last until the inner server's 10 s head timeout.
   expect(await until(()=>established()===0,2_000)).toBe(true);
   expect(await until(()=>server.connections()===0,2_000)).toBe(true);
  } finally {server.stop(true);}
 },30_000);
});

describe('the front with Realtime upgrades',()=>{
 const relay={upgrade:()=>({ok:true as const,host:'127.0.0.1',port:1,head:'x'}),isUpgrade:()=>true};
 test('an upgrade for a host that is not allowed gets 403 before anything is decided',async()=>{
  let decided=0;
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('plain'),shared.port,{upgrade:()=>{decided++;return relay.upgrade();},isUpgrade:relay.isUpgrade});
  await shared.release();
  try {
   const socket=connect(server.port,'127.0.0.1');let raw='';
   socket.on('data',chunk=>{raw+=chunk;});socket.on('error',()=>{});
   socket.on('connect',()=>socket.write(`GET /${RUNTIME}/realtime/v1/websocket HTTP/1.1\r\nhost: evil.example\r\nupgrade: websocket\r\nconnection: upgrade\r\n\r\n`));
   expect(await until(()=>raw.includes('\r\n\r\n'),4_000)).toBe(true);
   expect(raw.startsWith('HTTP/1.1 403')).toBe(true);
   expect(decided).toBe(0);
   socket.destroy();
  } finally {server.stop(true);}
 },20_000);

 test('a connection that sends no request head is closed at the head bound',async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('plain'),shared.port,{upgrade:relay.upgrade,isUpgrade:relay.isUpgrade,headMs:300});
  await shared.release();
  const socket=connect(server.port,'127.0.0.1');let closed=false;
  socket.on('close',()=>{closed=true;});socket.on('error',()=>{});
  try {
   await new Promise<void>(resolve=>socket.once('connect',()=>resolve()));
   const started=performance.now();
   // 300 ms bound; a loaded host may be late, but the mutant without the timer never closes.
   expect(await until(()=>closed,9_000)).toBe(true);
   expect(performance.now()-started).toBeGreaterThanOrEqual(250);
   expect(server.connections()).toBe(0);
  } finally {socket.destroy();server.stop(true);}
 },20_000);
});

describe('connections that reset at once, on the front with Realtime upgrades',()=>{
 const upgradeOptions=(upgrade:Upgrade)=>({upgrade,isUpgrade:(path:string)=>/\/realtime\/v1\/websocket/.test(path)});
 const plainBurst=(port:number,tail:string)=>Promise.all(Array.from({length:4},()=>Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,struct,sys
for _ in range(500):
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
 s.connect(('127.0.0.1',int(sys.argv[1])))
 if sys.argv[2]:s.sendall(sys.argv[2].encode('latin1'))
 s.close()`,String(port),tail],{stderr:'inherit'}).exited));
 const head='GET /x HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n';
 const upgradeHead=`GET /${RUNTIME}/realtime/v1/websocket HTTP/1.1\r\nhost: 127.0.0.1\r\nupgrade: websocket\r\nconnection: upgrade\r\n\r\n`;
 for(const [name,tail] of [['nothing sent',''],['a full request head sent',head]] as const)
 test(`2000 clients with ${name} that reset at once leave no connection to the inner server`,async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port,upgradeOptions(()=>({ok:false,status:404,message:'x'})));
  await shared.release();
  const inner=(server as {innerPort?:number}).innerPort!;
  try {
   await plainBurst(server.port,tail);
   expect(await until(()=>establishedTo(inner)===0,2_000)).toBe(true);
   expect(await until(()=>server.connections()===0,2_000)).toBe(true);
  } finally {server.stop(true);}
 },60_000);

 test('2000 clients whose upgrade request resets before the decision is made leave nothing behind',async()=>{
  const shared=await sharedPort();
  const upstreamSockets=new Set<Socket>();
  const upstream=createServer(socket=>{upstreamSockets.add(socket);socket.on('error',()=>{});socket.once('close',()=>upstreamSockets.delete(socket));});
  await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',()=>resolve()));
  const upstreamPort=(upstream.address() as {port:number}).port;
  const decide=gate();
  const server=await serveLocal(()=>new Response('ok'),shared.port,upgradeOptions(async()=>{
   await decide.opened;return {ok:true as const,host:'127.0.0.1',port:upstreamPort,head:'GET /s HTTP/1.1\r\n\r\n'};}));
  await shared.release();
  const inner=(server as {innerPort?:number}).innerPort!;
  try {
   await plainBurst(server.port,upgradeHead);
   // Every client is gone before any decision; now the decisions come.
   expect(await until(()=>server.connections()===0,4_000)).toBe(true);
   decide.open();
   await Bun.sleep(300);
   expect(upstreamSockets.size).toBe(0);
   expect(establishedTo(upstreamPort)).toBe(0);
   expect(establishedTo(inner)).toBe(0);
  } finally {decide.open();server.stop(true);for(const socket of upstreamSockets)socket.destroy();upstream.close();}
 },60_000);

 test('a Realtime upstream that refuses the connection gets the client a 503, not a hang',async()=>{
  // A port nothing listens on.
  const free=await sharedPort();await free.release();
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port,{
   upgrade:async()=>({ok:true as const,host:'127.0.0.1',port:free.port,head:'GET /s HTTP/1.1\r\n\r\n'}),
   isUpgrade:path=>/websocket/.test(path)});
  await shared.release();
  try {
   const socket=connect(server.port,'127.0.0.1');let raw='';
   socket.on('data',chunk=>{raw+=chunk;});socket.on('error',()=>{});
   socket.on('connect',()=>socket.write(`GET /${RUNTIME}/realtime/v1/websocket HTTP/1.1\r\nhost: 127.0.0.1\r\nupgrade: websocket\r\nconnection: upgrade\r\n\r\n`));
   expect(await until(()=>raw.includes('\r\n\r\n'),4_000)).toBe(true);
   expect(raw.startsWith('HTTP/1.1 503')).toBe(true);
   expect(raw).toContain('Realtime unavailable');
   socket.destroy();
  } finally {server.stop(true);}
 },20_000);

 test('a client that resets while the front is still connecting to the Realtime upstream does not leave the connect pending',async()=>{
  // An upstream with a backlog of 0 that never accepts, filled so that further SYNs are dropped:
  // the front\'s connect to it stays pending (SYN_SENT in the kernel\'s table) until it is given up.
  const holder=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,time
s=socket.socket();s.bind(('127.0.0.1',0));s.listen(0);print(s.getsockname()[1],flush=True)
time.sleep(60)`],{stdout:'pipe'});
  const filled:Socket[]=[];
  const synSent=(port:number)=>{
   const hex=port.toString(16).toUpperCase().padStart(4,'0');
   return readFileSync('/proc/net/tcp','utf8').split('\n').slice(1).filter(line=>{
    const columns=line.trim().split(/\s+/);return columns[2]?.endsWith(':'+hex)&&columns[3]==='02';
   }).length;
  };
  let client:ReturnType<typeof Bun.spawn>|undefined,server:Awaited<ReturnType<typeof serveLocal>>|undefined;
  try {
   const upstreamPort=Number(new TextDecoder().decode((await holder.stdout.getReader().read()).value).trim());
   for(let i=0;i<4;i++){const socket=connect(upstreamPort,'127.0.0.1');socket.on('error',()=>{});filled.push(socket);}
   await Bun.sleep(200);
   server=await serveLocal(()=>new Response('ok'),0,{
    upgrade:async()=>({ok:true as const,host:'127.0.0.1',port:upstreamPort,head:'GET /s HTTP/1.1\r\n\r\n'}),
    isUpgrade:path=>/websocket/.test(path)});
   const before=synSent(upstreamPort);
   // The client sends a whole upgrade request, waits 1.5 s, and resets with the connect pending.
   client=Bun.spawn(['/usr/bin/python3','-I','-c',`import socket,struct,sys,time
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
s.connect(('127.0.0.1',int(sys.argv[1])))
s.sendall(b'GET /realtime/v1/websocket HTTP/1.1\\r\\nhost: 127.0.0.1\\r\\nupgrade: websocket\\r\\nconnection: upgrade\\r\\n\\r\\n')
time.sleep(1.5);s.close()`,String(server.port)],{stderr:'inherit'});
   expect(await until(()=>synSent(upstreamPort)>before,4_000)).toBe(true);
   await client.exited;
   // Given up with the client: without the front closing it, the connect stays pending for the kernel\'s retries.
   expect(await until(()=>synSent(upstreamPort)<=before,4_000)).toBe(true);
  } finally {
   client?.kill();
   server?.stop(true);
   for(const socket of filled)socket.destroy();
   holder.kill();
  }
 },60_000);

 for(const [name,options] of [['without Realtime upgrades',{}],['with Realtime upgrades',upgradeOptions(()=>({ok:false,status:404,message:'x'}))]] as const)
 test(`${name}: a hop that cannot connect closes its client and leaves nothing, with no uncaught error`,async()=>{
  const shared=await sharedPort();
  const server=await serveLocal(()=>new Response('ok'),shared.port,options);
  await shared.release();
  const handle=server as {closeInnerForTest:()=>void};
  const errors:unknown[]=[];
  const onError=(error:unknown)=>{errors.push(error);};
  process.on('uncaughtException',onError);
  try {
   handle.closeInnerForTest();
   await Bun.sleep(50);
   const answers=await Promise.all(Array.from({length:20},()=>request(server.port,'/x')));
   for(const answer of answers)expect(answer.raw).toBe('');
   expect(await until(()=>server.connections()===0,3_000)).toBe(true);
   await Bun.sleep(100);
   expect(errors).toEqual([]);
  } finally {process.off('uncaughtException',onError);server.stop(true);}
 },30_000);
});
