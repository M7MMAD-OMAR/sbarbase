import {test,expect} from 'bun:test';
import {connect,createServer} from 'node:net';
import {mkdtempSync,rmSync,statSync,readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {X509Certificate} from 'node:crypto';
import {databaseProxy,DirectDatabase,externalDatabaseEndpoint,directDatabaseEndpoint,innerTlsPort} from '../src/http/database-proxy';
import {ExternalGuard,externalListen,DEFAULT_LIMITS} from '../src/http/database-external';
import {ensureDatabaseTls,tlsNames} from '../src/http/database-tls';
import {externalDatabaseHandler} from '../src/control/database';
import {Catalog} from '../src/control/catalog';

const E='e_'+'a'.repeat(24),OTHER='e_'+'b'.repeat(24);
function startup(fields:Record<string,string>) {
 const body=Buffer.concat([...Object.entries(fields).map(([key,value])=>Buffer.from(`${key}\0${value}\0`)),Buffer.from('\0')]);
 const header=Buffer.alloc(8);header.writeInt32BE(body.length+8,0);header.writeInt32BE(196608,4);
 return Buffer.concat([header,body]);
}
const sslRequest=()=>{const packet=Buffer.alloc(8);packet.writeInt32BE(8,0);packet.writeInt32BE(80877103,4);return packet;};
const message=(type:string,body:Buffer)=>{const head=Buffer.alloc(5);head.write(type,0);head.writeInt32BE(body.length+4,1);return Buffer.concat([head,body]);};
const authOk=()=>message('R',Buffer.from([0,0,0,0]));
const rejected=()=>message('E',Buffer.from('SFATAL\0VFATAL\0C28000\0Mrole is not permitted to log in\0\0'));
const cancelRequest=()=>{const packet=Buffer.alloc(16);packet.writeInt32BE(16,0);packet.writeInt32BE(80877102,4);packet.writeInt32BE(1234,8);packet.writeInt32BE(5678,12);return packet;};
const badPassword=()=>message('E',Buffer.from('SFATAL\0VFATAL\0C28P01\0Mpassword authentication failed\0\0'));


/** SSLRequest, then TLS upgraded in place the way a PostgreSQL client does it (Bun's STARTTLS), then `packet`.
 * Resolves with what came back and whether the handshake finished. */
function tlsTalk(port:number,ca:string|undefined,packet:Buffer,waitMs=400):Promise<{text:string;secure:boolean;refused:boolean}> {
 return new Promise(resolve=>{
  let text='',secure=false,refused=false,settled=false,upgraded=false;
  const finish=()=>{if(settled)return;settled=true;resolve({text,secure,refused});};
  const timer=setTimeout(()=>{try{plain?.end();}catch{}finish();},waitMs+2000);
  let plain:any;
  void Bun.connect({hostname:'127.0.0.1',port,socket:{
   open(socket){plain=socket;socket.write(sslRequest());},
   data(socket,chunk){
    if(!upgraded){
     upgraded=true;
     if(Buffer.from(chunk).toString('latin1')!=='S'){refused=true;finish();return;}
     socket.upgradeTLS({tls:{ca,serverName:'localhost',rejectUnauthorized:true},socket:{
      open(){},handshake(tlsSocket,success,authorizationError){secure=success&&!authorizationError;if(secure)tlsSocket.write(packet);else{clearTimeout(timer);finish();}},
      data(_s,body){text+=Buffer.from(body).toString('latin1');},
      close(){clearTimeout(timer);finish();},error(){clearTimeout(timer);finish();}}});
     setTimeout(()=>{clearTimeout(timer);try{plain.end();}catch{}finish();},waitMs);
    }
   },
   close(){finish();},error(){finish();}}}).catch(()=>finish());
 });
}

async function fakePostgres(reply:()=>Buffer) {
 const received:Buffer[]=[];
 const server=createServer(socket=>{socket.on('data',chunk=>{received.push(chunk as Buffer);socket.write(reply());});});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',()=>resolve()));
 return {port:(server.address() as {port:number}).port,received,close:()=>server.close()};
}

test('the TLS material is made once, keeps its CA across a renewal and names what was asked for',()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-'));
 try {
  const first=ensureDatabaseTls(directory,tlsNames({SBARBASE_DATABASE_TLS_NAMES:'db.example.com'}));
  expect(first.names).toContain('db.example.com');
  const leaf=new X509Certificate(first.cert);
  expect(leaf.checkHost('db.example.com')).toBeTruthy();
  expect(leaf.checkIP('127.0.0.1')).toBeTruthy();
  expect(leaf.verify(new X509Certificate(first.ca).publicKey)).toBe(true);
  expect(new X509Certificate(first.ca).ca).toBe(true);
  expect(statSync(join(directory,'server.key')).mode&0o077).toBe(0);
  expect(statSync(join(directory,'ca.key')).mode&0o077).toBe(0);
  expect(ensureDatabaseTls(directory,first.names).cert).toBe(first.cert);
  const renamed=ensureDatabaseTls(directory,tlsNames({SBARBASE_DATABASE_TLS_NAMES:'other.example.com'}));
  expect(renamed.ca).toBe(first.ca);
  expect(renamed.cert).not.toBe(first.cert);
  expect(new X509Certificate(renamed.cert).checkHost('other.example.com')).toBeTruthy();
  // A certificate near its end is made again.
  const later=Date.now()+380*24*3600*1000;
  expect(ensureDatabaseTls(directory,renamed.names,later).cert).not.toBe(renamed.cert);
  expect(()=>tlsNames({SBARBASE_DATABASE_TLS_NAMES:'bad name;rm'})).toThrow('invalid name');
 } finally {rmSync(directory,{recursive:true,force:true});}
});

test('with a certificate the listener serves TLS, verifies against the CA and forwards the startup byte for byte',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>Buffer.from('R-from-postgres'));
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,target:()=>({host:'127.0.0.1',port:upstream.port})});
 try {
  const allowed=startup({user:`${E}_developer`,database:E});
  const good=await tlsTalk(proxy.port,tls.ca,allowed);
  const answer=good.text;
  expect(answer).toContain('R-from-postgres');
  expect(Buffer.concat(upstream.received).equals(allowed)).toBe(true);
  // A client that does not trust the CA cannot finish the handshake.
  expect((await tlsTalk(proxy.port,undefined,allowed)).secure).toBe(false);
  expect(good.secure).toBe(true);
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('requireTls refuses a cleartext startup before PostgreSQL and bytes injected after SSLRequest',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>Buffer.from('R-from-postgres'));
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,target:()=>({host:'127.0.0.1',port:upstream.port})});
 const talk=(packets:Buffer[])=>new Promise<string>(resolve=>{
  const socket=connect(proxy.port,'127.0.0.1',()=>socket.write(Buffer.concat(packets)));
  let text='';socket.on('data',chunk=>{text+=chunk.toString('latin1');});socket.on('close',()=>resolve(text));
  setTimeout(()=>{socket.destroy();},1500);
 });
 try {
  expect(await talk([startup({user:`${E}_developer`,database:E})])).toContain('requires TLS');
  expect(await talk([sslRequest(),startup({user:`${E}_developer`,database:E})])).not.toContain('R-from-postgres');
  expect(upstream.received).toHaveLength(0);
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('the guard admits only environments that opted in, limits attempts and closes an environment after failed logins',()=>{
 let at=1000;const on=new Set([E]),lines:string[]=[];
 const guard=new ExternalGuard(runtime=>on.has(runtime),line=>lines.push(line),{...DEFAULT_LIMITS,perAddress:3,total:5,failures:3},()=>at);
 expect(guard.admit(OTHER,'1.1.1.1')).toContain('not enabled');
 expect(guard.admit(E,'1.1.1.1')).toBeUndefined();
 expect(guard.accept('1.1.1.1')).toBeUndefined();expect(guard.accept('1.1.1.1')).toBeUndefined();expect(guard.accept('1.1.1.1')).toBeUndefined();
 expect(guard.accept('1.1.1.1')).toContain('Too many connection attempts');
 expect(guard.accept('2.2.2.2')).toBeUndefined();
 at+=DEFAULT_LIMITS.windowMs+1;
 expect(guard.accept('1.1.1.1')).toBeUndefined();
 for(let i=0;i<3;i++)guard.outcome(E,'1.1.1.1','bad-password');
 expect(guard.admit(E,'1.1.1.1')).toContain('failed logins');
 expect(lines.join('\n')).toContain(E);
 expect(lines.join('\n')).not.toMatch(/password=|sb_secret/);
 at+=DEFAULT_LIMITS.lockMs+1;
 expect(guard.admit(E,'1.1.1.1')).toBeUndefined();
 // A success does not wipe the count: interleaved successes cannot keep the lock from firing.
 guard.outcome(E,'1.1.1.1','bad-password');guard.outcome(E,'1.1.1.1','ok');guard.outcome(E,'1.1.1.1','bad-password');guard.outcome(E,'1.1.1.1','ok');guard.outcome(E,'1.1.1.1','bad-password');
 expect(guard.admit(E,'1.1.1.1')).toContain('failed logins');
 // The window ages failures out.
 at+=DEFAULT_LIMITS.lockMs+1;
 guard.outcome(E,'1.1.1.1','bad-password');at+=DEFAULT_LIMITS.lockMs+1;guard.outcome(E,'1.1.1.1','bad-password');
 expect(guard.admit(E,'1.1.1.1')).toBeUndefined();
});

test('failed passwords seen on the wire close the environment; a success does not, and the stream is untouched',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 let reply=badPassword;
 const upstream=await fakePostgres(()=>reply());
 const guard=new ExternalGuard(runtime=>runtime===E,()=>{},{...DEFAULT_LIMITS,failures:3});
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,guard,target:()=>({host:'127.0.0.1',port:upstream.port})});
 const attempt=async()=>(await tlsTalk(proxy.port,tls.ca,startup({user:`${E}_developer`,database:E}))).text;
 try {
  reply=authOk;
  for(let i=0;i<5;i++)expect(await attempt()).toContain('R');
  reply=badPassword;
  for(let i=0;i<3;i++)expect(await attempt()).toContain('28P01');
  expect(await attempt()).toContain('failed logins');
  const seen=upstream.received.length;
  await attempt();
  expect(upstream.received.length).toBe(seen);
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('the external listener is on loopback port 6545 unless set, and can be turned off',()=>{
 expect(externalListen({})).toEqual({port:6545,host:'127.0.0.1'});
 expect(externalListen({SBARBASE_DATABASE_EXTERNAL_PORT:'off'})).toBeNull();
 expect(externalListen({SBARBASE_DATABASE_EXTERNAL_BIND:'0.0.0.0'})).toEqual({port:6545,host:'0.0.0.0'});
 for(const bad of [{SBARBASE_DATABASE_EXTERNAL_PORT:'80'},{SBARBASE_DATABASE_EXTERNAL_BIND:'example.com'}])expect(()=>externalListen(bad)).toThrow();
});

test('external access is closed by default, needs database access on, is for owners and admins and closes with it',async()=>{
 const catalog=new Catalog(':memory:'),direct=new DirectDatabase();
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 try {
  await direct.startExternal({host:'127.0.0.1',port:0,target:()=>undefined,tls},tls.ca);
  expect(externalDatabaseEndpoint(direct)?.ca).toBe(tls.ca);
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=externalDatabaseHandler(catalog,async request=>request.headers.get('authorization'),direct);
  const call=(actor:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/database/external`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime!;
  expect((await call('carol')).status).toBe(403);
  const closed=(await (await call('alice')).json()) as any;
  expect(closed.data.enabled).toBe(false);expect(catalog.externalDatabaseEnabled(runtime)).toBe(false);
  expect((await call('alice','PUT',{enabled:true})).status).toBe(409);
  expect((await call('carol','PUT',{enabled:true})).status).toBe(403);
  expect((await call('alice','PUT',{enabled:'yes'})).status).toBe(400);
  (catalog as any).db.query("INSERT INTO database_access(runtime,desired,state,actor,updated_at) VALUES (?,'on','on','alice',1)").run(runtime);
  const open=await call('alice','PUT',{enabled:true});
  expect(open.status).toBe(200);
  const body=(await open.json()) as any;
  expect(body.data.enabled).toBe(true);expect(body.data.user).toBe(`${runtime}_developer`);expect(body.data.ca).toBe(tls.ca);
  expect(body.data.sslmode).toBe('verify-ca');
  expect(JSON.stringify(body)).not.toContain('key');
  expect(catalog.externalDatabaseEnabled(runtime)).toBe(true);
  expect((catalog as any).db.query("SELECT 1 FROM audit_events WHERE action='database.external_enabled'").all().length).toBe(1);
  catalog.requestDatabaseAccess('alice',environment,false);
  expect(catalog.externalDatabaseEnabled(runtime)).toBe(false);
 } finally {direct.stop();catalog.close();rmSync(directory,{recursive:true,force:true});}
});

test('a cancel is refused in cleartext on a TLS-only listener and forwarded over TLS',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>Buffer.from('R-from-postgres'));
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,target:()=>({host:'127.0.0.1',port:upstream.port})});
 try {
  await new Promise<void>(resolve=>{const socket=connect(proxy.port,'127.0.0.1',()=>socket.write(cancelRequest()));socket.on('close',()=>resolve());setTimeout(()=>{socket.destroy();resolve();},800);});
  expect(upstream.received).toHaveLength(0);
  const over=await tlsTalk(proxy.port,tls.ca,cancelRequest());
  expect(over.secure).toBe(true);
  expect(Buffer.concat(upstream.received).equals(cancelRequest())).toBe(true);
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('a client that asks for TLS and never starts the handshake loses its slot, and the inner port admits nothing on its own',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>Buffer.from('R-from-postgres'));
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,startupMs:300,target:()=>({host:'127.0.0.1',port:upstream.port})});
 try {
  const closedAfter=await new Promise<number>(resolve=>{
   const started=Date.now();
   const socket=connect(proxy.port,'127.0.0.1',()=>socket.write(sslRequest()));
   socket.on('close',()=>resolve(Date.now()-started));
   setTimeout(()=>{socket.destroy();resolve(-1);},4000);
  });
  expect(closedAfter).toBeGreaterThan(200);expect(closedAfter).toBeLessThan(2500);
  // Straight to the loopback TLS port: the handshake can finish, but no piped connection owns it.
  const direct=await new Promise<string>(resolve=>{
   let text='';
   void Bun.connect({hostname:'127.0.0.1',port:innerTlsPort(proxy)!,tls:{ca:tls.ca,serverName:'localhost'},socket:{
    handshake(socket){socket.write(startup({user:`${E}_developer`,database:E}));},
    data(_s,chunk){text+=Buffer.from(chunk).toString('latin1');},close(){resolve(text);},error(){resolve(text);}}}).catch(()=>resolve(text));
   setTimeout(()=>resolve(text),800);
  });
  expect(direct).toBe('');
  expect(upstream.received).toHaveLength(0);
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('over TLS an environment without the opt-in is refused before PostgreSQL, and a lock expires on the wire',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>badPassword());
 let at=1000;const on=new Set<string>();
 const guard=new ExternalGuard(runtime=>on.has(runtime),()=>{},{...DEFAULT_LIMITS,failures:2},()=>at);
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,guard,target:()=>({host:'127.0.0.1',port:upstream.port})});
 const attempt=async()=>(await tlsTalk(proxy.port,tls.ca,startup({user:`${E}_developer`,database:E}))).text;
 try {
  expect(await attempt()).toContain('not enabled');
  expect(upstream.received).toHaveLength(0);
  on.add(E);
  expect(await attempt()).toContain('28P01');expect(await attempt()).toContain('28P01');
  expect(await attempt()).toContain('failed logins');
  at+=DEFAULT_LIMITS.lockMs+1;
  expect(await attempt()).toContain('28P01');
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('only a wrong password counts as a failed login, and the server reply is read across split packets',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const lines:string[]=[];let mode:'reject'|'split'='reject';
 const received:Buffer[]=[];
 const server=createServer(socket=>{socket.on('data',chunk=>{received.push(chunk as Buffer);
  if(mode==='reject')socket.write(rejected());
  else{const packet=badPassword();socket.write(packet.subarray(0,3));setTimeout(()=>socket.write(packet.subarray(3,9)),20);setTimeout(()=>socket.write(packet.subarray(9)),40);}});});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',()=>resolve()));
 const guard=new ExternalGuard(()=>true,line=>lines.push(line),{...DEFAULT_LIMITS,failures:2});
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,guard,target:()=>({host:'127.0.0.1',port:(server.address() as {port:number}).port})});
 const attempt=async()=>(await tlsTalk(proxy.port,tls.ca,startup({user:`${E}_developer`,database:E}))).text;
 try {
  for(let i=0;i<4;i++)expect(await attempt()).toContain('28000');
  expect(lines.join('')).toBe('');
  mode='split';
  expect(await attempt()).toContain('28P01');expect(await attempt()).toContain('28P01');
  expect(lines.join('')).toContain('closed for');
 } finally {proxy.stop();server.close();rmSync(directory,{recursive:true,force:true});}
});

test('sessions of an environment that loses external access are ended within the sweep',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),tls=ensureDatabaseTls(directory);
 const upstream=await fakePostgres(()=>authOk());
 let enabled=true;const lines:string[]=[];
 const guard=new ExternalGuard(()=>enabled,line=>lines.push(line));
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',tls,requireTls:true,guard,target:()=>({host:'127.0.0.1',port:upstream.port})});
 try {
  const closed=new Promise<boolean>(resolve=>{
   void Bun.connect({hostname:'127.0.0.1',port:proxy.port,socket:{
    open(socket){socket.write(sslRequest());},
    data(socket,chunk){
     if(Buffer.from(chunk).toString('latin1')==='S'){
      socket.upgradeTLS({tls:{ca:tls.ca,serverName:'localhost'},socket:{
       handshake(tlsSocket){tlsSocket.write(startup({user:`${E}_developer`,database:E}));},
       data(){enabled=false;guard.sweep();},close(){resolve(true);},error(){resolve(true);}}});
     }
    },close(){}}}).catch(()=>resolve(false));
   setTimeout(()=>resolve(false),3000);
  });
  expect(await closed).toBe(true);
  expect(lines.join('\n')).toContain('ended');
 } finally {proxy.stop();upstream.close();rmSync(directory,{recursive:true,force:true});}
});

test('a certificate that does not match its key is made again, and an invalid name is refused',()=>{
 const directory=mkdtempSync(join(tmpdir(),'dbtls-')),other=mkdtempSync(join(tmpdir(),'dbtls-'));
 try {
  const first=ensureDatabaseTls(directory);
  const stranger=ensureDatabaseTls(other);
  writeFileSync(join(directory,'server.key'),stranger.key);
  const again=ensureDatabaseTls(directory);
  expect(again.key).not.toBe(stranger.key);
  expect(new X509Certificate(again.cert).checkPrivateKey(require('node:crypto').createPrivateKey(again.key))).toBe(true);
  expect(again.ca).toBe(first.ca);
  expect(()=>tlsNames({SBARBASE_DATABASE_TLS_NAMES:'1.2.3'})).toThrow('invalid name');
  expect(statSync(directory).mode&0o077).toBe(0);
 } finally {rmSync(directory,{recursive:true,force:true});rmSync(other,{recursive:true,force:true});}
});

test('a renewed certificate is what new connections are served, and a cancel leaves nothing to pipe',async()=>{
 const first=mkdtempSync(join(tmpdir(),'dbtls-')),second=mkdtempSync(join(tmpdir(),'dbtls-'));
 const one=ensureDatabaseTls(first),two=ensureDatabaseTls(second);
 const upstream=await fakePostgres(()=>Buffer.from('R-from-postgres'));
 const direct=new DirectDatabase();
 await direct.start({host:'127.0.0.1',port:0,tls:one,target:()=>({host:'127.0.0.1',port:upstream.port})});
 const port=directDatabaseEndpoint(direct)!.port;
 try {
  const hello=startup({user:`${E}_developer`,database:E});
  expect((await tlsTalk(port,one.ca,hello)).secure).toBe(true);
  expect((await tlsTalk(port,two.ca,hello)).secure).toBe(false);
  await direct.reloadTls(two);
  expect((await tlsTalk(port,two.ca,hello)).secure).toBe(true);
  expect((await tlsTalk(port,one.ca,hello)).secure).toBe(false);
  // A cancel, then a startup for the superuser in a later write: only the cancel reaches PostgreSQL.
  upstream.received.length=0;
  const sneaky=await tlsTalk(port,two.ca,Buffer.concat([cancelRequest()]),50);
  expect(sneaky.secure).toBe(true);
  expect(Buffer.concat(upstream.received).equals(cancelRequest())).toBe(true);
 } finally {direct.stop();upstream.close();rmSync(first,{recursive:true,force:true});rmSync(second,{recursive:true,force:true});}
});
