import {afterEach,describe,expect,setDefaultTimeout,test} from 'bun:test';
import {connect,createServer,type Server} from 'node:net';
import {mkdirSync,mkdtempSync,readFileSync,rmSync,symlinkSync,writeFileSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startGateway,type GatewayApplication,type GatewayOptions} from '../lab/upstream-server';
import {DirectDatabase} from '../src/http/database-proxy';
import {candidateSignals,candidateRequested,processStart,handoverEnabled,libcSteering,listeningInodes,ListenerRecord,namedForHandover,ownDescriptor,procAddress,
 protocolOf,publishPromoted,withPidHeader,type Steering} from '../src/http/handover';
import {invitationAccounts} from '../lab/upstream-app';
import {ensureDatabaseTls} from '../src/http/database-tls';
import {application} from '../src/control/application';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managementToken} from './management-fixture';

// Real listeners and timers on a loaded host: a test may take far longer than Bun's 5 s default.
setDefaultTimeout(60_000);
import {releasePorts,reservePort} from './fixtures/ports';
const folders:string[]=[];
const servers:Server[]=[];
const temporary=()=>{const folder=mkdtempSync(join(tmpdir(),'handover-'));folders.push(folder);return folder;};
afterEach(()=>{releasePorts();for(const server of servers.splice(0))server.close();for(const folder of folders.splice(0))rmSync(folder,{recursive:true,force:true});});

const header='  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n';
const row=(address:string,port:number,state:string,inode:number)=>
 `   0: ${address}:${port.toString(16).toUpperCase().padStart(4,'0')} 00000000:0000 ${state} 00000000:00000000 00:00000000 00000000  1000        0 ${inode} 1 0000000000000000 100 0 0 10 0\n`;

describe('addresses as /proc/net prints them',() => {
 test('IPv4 is each octet reversed',() => {
  expect(procAddress('127.0.0.1')).toEqual({file:'tcp',hex:'0100007F'});
  expect(procAddress('0.0.0.0')).toEqual({file:'tcp',hex:'00000000'});
  expect(procAddress('10.20.30.40')).toEqual({file:'tcp',hex:'281E140A'});
  expect(procAddress('localhost')).toEqual({file:'tcp',hex:'0100007F'});
 });
 test('IPv6 is each 32 bit word reversed in tcp6',() => {
  expect(procAddress('::')).toEqual({file:'tcp6',hex:'00000000000000000000000000000000'});
  expect(procAddress('::1')).toEqual({file:'tcp6',hex:'00000000000000000000000001000000'});
  expect(procAddress('fe80::1:2')).toEqual({file:'tcp6',hex:'000080FE000000000000000002000100'});
 });
 test('anything else is refused',() => {
  expect(() => procAddress('not-an-address')).toThrow();
 });
});

describe('finding this process\'s own descriptor',() => {
  function proc(entries:{fd:number;target:string}[],tcp:string,tcp6='') {
   const folder=temporary();
   mkdirSync(join(folder,'net'));mkdirSync(join(folder,'self/fd'),{recursive:true});
   writeFileSync(join(folder,'net/tcp'),header+tcp);writeFileSync(join(folder,'net/tcp6'),header+tcp6);
   for(const entry of entries)symlinkSync(entry.target,join(folder,'self/fd',String(entry.fd)));
   return folder;
  }
  test('only a socket in state 0A at that address and port counts',() => {
   const folder=proc([{fd:7,target:'socket:[111]'},{fd:8,target:'socket:[222]'}],
    row('0100007F',8080,'0A',111)+row('0100007F',8080,'01',222)+row('0100007F',9090,'0A',333));
   expect(listeningInodes('127.0.0.1',8080,folder)).toEqual([111]);
   expect(ownDescriptor('127.0.0.1',8080,folder)).toBe(7);
  });
  test('a port another address holds does not match',() => {
   const folder=proc([{fd:7,target:'socket:[111]'}],row('0200007F',8080,'0A',111));
   expect(() => ownDescriptor('127.0.0.1',8080,folder)).toThrow('0 descriptors');
  });
  test('a listener of another process does not match',() => {
   const folder=proc([{fd:7,target:'socket:[999]'}],row('0100007F',8080,'0A',111));
   expect(() => ownDescriptor('127.0.0.1',8080,folder)).toThrow('0 descriptors');
  });
  test('two descriptors on the address are refused, not guessed between',() => {
   const folder=proc([{fd:7,target:'socket:[111]'},{fd:9,target:'socket:[111]'}],row('0100007F',8080,'0A',111));
   expect(() => ownDescriptor('127.0.0.1',8080,folder)).toThrow('2 descriptors');
  });
  test('IPv6 binds are found in tcp6',() => {
   const folder=proc([{fd:12,target:'socket:[444]'}],'',row('00000000000000000000000000000000',5432,'0A',444));
   expect(ownDescriptor('::',5432,folder)).toBe(12);
  });
  test('a descriptor that is not a socket is skipped',() => {
   const folder=proc([{fd:3,target:'/dev/null'},{fd:7,target:'socket:[111]'}],row('0100007F',8080,'0A',111));
   expect(ownDescriptor('127.0.0.1',8080,folder)).toBe(7);
  });
  test('a real listener of this process is found by its own address',async() => {
   const server=createServer();servers.push(server);
   await new Promise<void>(resolve => server.listen({port:0,host:'127.0.0.1',reusePort:true},() => resolve()));
   const port=(server.address() as {port:number}).port;
   expect(listeningInodes('127.0.0.1',port).length).toBe(1);
   expect(ownDescriptor('127.0.0.1',port)).toBeGreaterThan(2);
  });
});

describe('the program',() => {
 test('attach and detach return 0 on a real listener, and a second detach is ENOENT',async() => {
  const steering=libcSteering();
  const server=createServer();servers.push(server);
  await new Promise<void>(resolve => server.listen({port:0,host:'127.0.0.1',reusePort:true},() => resolve()));
  const fd=ownDescriptor('127.0.0.1',(server.address() as {port:number}).port);
  expect(steering.attach(fd)).toBe(0);
  expect(steering.detach(fd)).toBe(0);
  // Nothing is attached any more: the kernel says so.
  expect(steering.detach(fd)).toBe(-1);
 });
 test('a descriptor that is not a socket is refused',() => {
  expect(libcSteering().attach(0)).toBe(-1);
 });
});

describe('records, the switch and the pid header',() => {
 test('the switch is exactly 1',() => {
  expect(handoverEnabled({SBARBASE_HANDOVER:'1'})).toBe(true);
  for(const value of [undefined,'','0','true','yes','11'])expect(handoverEnabled({SBARBASE_HANDOVER:value})).toBe(false);
 });
 test('a candidate needs the switch as well as its marker',() => {
  expect(candidateRequested({SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1'})).toBe(true);
  expect(candidateRequested({SBARBASE_CANDIDATE:'1'})).toBe(false);
  expect(candidateRequested({SBARBASE_HANDOVER:'1'})).toBe(false);
 });
 test('PROTOCOL is read from exactly one line of lab/handover.py as text',() => {
  expect(protocolOf('x\nPROTOCOL = 7\ny')).toBe(7);
  expect(() => protocolOf('PROTOCOL = 1\nPROTOCOL = 2')).toThrow();
  expect(() => protocolOf('# PROTOCOL = 1 is...\n')).toThrow();
  expect(() => protocolOf('  PROTOCOL = 1')).toThrow();
  expect(protocolOf(readFileSync('lab/handover.py','utf8'))).toBeGreaterThan(0);
 });
 test('the listener record is the shape the supervisor reads, written whole',() => {
  const folder=temporary();
  const record=new ListenerRecord(folder,4321,1,'candidate');
  record.write(true,[{name:'console',host:'127.0.0.1',port:7000,steered:true},{name:'database',host:'0.0.0.0',port:6543}]);
  expect(JSON.parse(readFileSync(join(folder,'listeners-4321.json'),'utf8'))).toEqual({pid:4321,protocol:1,role:'candidate',frozen:true,
   listeners:[{name:'console',host:'127.0.0.1',port:7000,steered:true},{name:'database',host:'0.0.0.0',port:6543,steered:false}]});
  expect(existsSync(join(folder,'listeners-4321.json.pending'))).toBe(false);
  record.remove();
  expect(existsSync(join(folder,'listeners-4321.json'))).toBe(false);
 });
 test('handover.json names the process it was written for, by pid and by start time',() => {
  const folder=temporary(),pid=process.pid,started=processStart(pid)!;
  expect(started).toBeGreaterThan(0);
  expect(namedForHandover(folder,pid)).toBe(false);
  const write=(value:unknown) => writeFileSync(join(folder,'handover.json'),typeof value==='string'?value:JSON.stringify(value));
  write({pid,started});
  expect(namedForHandover(folder,pid)).toBe(true);
  expect(namedForHandover(folder,pid+1)).toBe(false);
  // A record an earlier run left, naming a pid this process happens to have now, is not for this process.
  write({pid,started:started-1});
  expect(namedForHandover(folder,pid)).toBe(false);
  write({pid});
  expect(namedForHandover(folder,pid)).toBe(false);
  write({pid,started:String(started)});
  expect(namedForHandover(folder,pid)).toBe(false);
  write('not json');
  expect(namedForHandover(folder,pid)).toBe(false);
 });
 test('a process start time is field 22 of its stat line, whatever the command name holds',() => {
  const folder=temporary();
  mkdirSync(join(folder,'77'));
  writeFileSync(join(folder,'77/stat'),'77 (a) b ) S 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 424242 9\n');
  expect(processStart(77,folder)).toBe(424242);
  expect(processStart(78,folder)).toBeUndefined();
  writeFileSync(join(folder,'77/stat'),'garbage');
  expect(processStart(77,folder)).toBeUndefined();
 });
 test('the promoted server.json carries both addresses and appears whole',() => {
  const folder=temporary();
  publishPromoted(folder,'http://127.0.0.1:7000','http://127.0.0.1:41000',1,99);
  expect(JSON.parse(readFileSync(join(folder,'server.json'),'utf8'))).toEqual({url:'http://127.0.0.1:7000',pid:99,protocol:1,
   private:'http://127.0.0.1:41000',promoted:true});
  expect(existsSync(join(folder,'server.json.pending'))).toBe(false);
 });
 test('the pid header goes to a direct caller and to nobody behind the proxy',async() => {
  const answer=() => Response.json({status:'ok'});
  expect(withPidHeader(answer(),new Request('http://127.0.0.1/health'),77).headers.get('x-sbarbase-pid')).toBe('77');
  expect((await withPidHeader(answer(),new Request('http://127.0.0.1/health'),77).json())).toEqual({status:'ok'});
  for(const name of ['x-forwarded-for','x-forwarded-host','forwarded','X-Forwarded-Proto'])
   expect(withPidHeader(answer(),new Request('http://127.0.0.1/health',{headers:{[name]:'1.2.3.4'}}),77).headers.get('x-sbarbase-pid')).toBeNull();
 });
});

describe('the candidate\'s signals',() => {
 function setup(promote:() => Promise<void> = async() => {}) {
  const calls:string[]=[];
  const signal=candidateSignals({promote:async() => {calls.push('promote');await promote();},release:() => calls.push('release'),failed:error => calls.push('failed:'+(error as Error).message)});
  return {calls,signal};
 }
 test('the first promotes, the second releases, later ones are ignored',async() => {
  const {calls,signal}=setup();
  signal();await Bun.sleep(5);signal();signal();signal();
  expect(calls).toEqual(['promote','release']);
 });
 test('a failed promotion is reported and never released',async() => {
  const {calls,signal}=setup(async() => {throw new Error('no bind');});
  signal();await Bun.sleep(5);signal();
  expect(calls).toEqual(['promote','failed:no bind']);
 });
 test('a release asked while the promotion runs waits for it',async() => {
  let finish=() => {};
  const {calls,signal}=setup(() => new Promise<void>(resolve => {finish=resolve;}));
  signal();signal();
  await Bun.sleep(5);
  expect(calls).toEqual(['promote']);
  finish();await Bun.sleep(5);
  expect(calls).toEqual(['promote','release']);
 });
});

describe('the management Auth address',() => {
 test('operator sign-in and the identity check both follow the address the file names now',async() => {
  const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
  const seen:string[]=[];let address='http://a.invalid';
  const handler=application(catalog,keys,{get auth(){return address;},anonymousToken:'anon',publishableKey:'public'},() => undefined,
   (async(input:RequestInfo|URL) => {seen.push(String(input));
    return String(input).endsWith('/user')?Response.json({id:'owner',factors:[]}):Response.json({access_token:managementToken('owner','aal1'),user:{id:'owner'}});}) as unknown as typeof fetch);
  const signIn=() => handler(new Request('http://local/management/auth/v1/token?grant_type=password',{method:'POST',
   headers:{apikey:'public','content-type':'application/json'},body:JSON.stringify({email:'owner@example.com',password:'test-password'})}));
  try{
   expect((await signIn()).status).toBe(200);
   address='http://b.invalid';
   expect((await signIn()).status).toBe(200);
   expect(seen).toEqual(['http://a.invalid/token?grant_type=password','http://a.invalid/user','http://b.invalid/token?grant_type=password','http://b.invalid/user']);
  }finally{catalog.close();keys.close();}
 });
 test('invitations reach the address the file names now, not the one it named at construction',async() => {
  const seen:string[]=[];
  const request=(async(input:string|URL|Request) => {seen.push(String(input instanceof Request?input.url:input));
   return Response.json({message:'x',code:'email_exists'},{status:422});}) as unknown as typeof fetch;
  let address='http://172.18.0.5:9999';
  const accounts=invitationAccounts(() => address,'service-role',request);
  await accounts.create('a@example.com','a-long-password').catch(() => {});
  address='http://172.18.0.9:9999/';
  await accounts.create('b@example.com','a-long-password').catch(() => {});
  expect(seen[0]).toStartWith('http://172.18.0.5:9999/');
  expect(seen[1]).toStartWith('http://172.18.0.9:9999/');
 });
});

const RUNTIME='e_0123456789abcdef01234567';
const functionsEndpoints=(port:number) => ({[RUNTIME]:{auth:'http://127.0.0.1:1',rest:'http://127.0.0.1:2',functions:{storage:{host:'127.0.0.1',port,subnet:'127.0.0.0/8'}}}});
type Opened={app:GatewayApplication;options:GatewayOptions;steering:Steering;calls:string[]};
/** A free port below Linux's ephemeral range (32768 to 60999), where the clients of these tests take their source
 * ports: a port found free in the range can be taken by one of them before it is bound. */
const freePort=reservePort;
function stub(listening:(port:number)=>void=() => {}): GatewayApplication {
 return {handler:async() => new Response('x'),studio:async() => new Response('studio'),upstream:async() => new Response('upstream'),
  realtime:() => ({ok:false,status:404,message:'none'}),database:new DirectDatabase(),listening,
  catalog:{schemaVersion:() => 1,runtimeReady:() => true,externalDatabaseEnabled:() => false},keys:{check(){},confirmationProbe:undefined as never}};
}
async function get(port:number,path='/health'):Promise<Response> {return await fetch(`http://127.0.0.1:${port}${path}`,{headers:{connection:'close'}});}
const refused=(port:number) => new Promise<boolean>(resolve => {const socket=connect(port,'127.0.0.1');socket.on('connect',() => {socket.destroy();resolve(false);});socket.on('error',() => resolve(true));});

describe('a candidate gateway',() => {
 async function candidate(extra:Partial<GatewayOptions>={},steering?:Steering,listening?:(port:number)=>void) {
  const state=temporary();
  const calls:string[]=[];
  const fake:Steering=steering??{attach:fd => {calls.push('attach:'+fd);return 0;},detach:fd => {calls.push('detach:'+fd);return 0;}};
  const consolePort=await freePort(),databasePort=await freePort(),studioPort=await freePort();
  const app=stub(listening);
  const gateway=await startGateway(app,{hold:() => undefined,consolePort,databaseAt:{port:databasePort,host:'127.0.0.1'},
   endpoints:() => ({}),database:() => undefined,studio:() => ({host:'127.0.0.1',port:studioPort}),
   handover:{candidate:true,protocol:1,stateDirectory:state,steering:fake,freezeEveryMs:50},...extra});
  return {state,gateway,calls,consolePort,databasePort,studioPort,app,record:() => JSON.parse(readFileSync(join(state,`listeners-${process.pid}.json`),'utf8'))};
 }
 test('the application is told the private port, before and after the promotion (the MCP endpoint reaches the API there)',async() => {
  const told:number[]=[];
  const c=await candidate({},undefined,port => told.push(port));
  try{
   expect(told).toEqual([c.gateway.port]);
   expect(c.gateway.port).not.toBe(c.consolePort);
   await c.gateway.promote!();
   expect(told).toEqual([c.gateway.port]);
  }finally{c.gateway.stop();}
 });
 test('after the promotion the Studio route and the Functions Storage listeners follow the state again',async() => {
  const first=await freePort(),second=await freePort(),functions=await freePort();
  let studio={host:'127.0.0.1',port:first},endpoints:Record<string,any>={};
  const c=await candidate({studio:() => studio,endpoints:() => endpoints});
  try{
   await c.gateway.promote!();
   expect(await refused(first)).toBe(false);
   // The timers were started with the promotion: the route moves, and a new environment's listener opens.
   studio={host:'127.0.0.1',port:second};
   endpoints=functionsEndpoints(functions);
   for(let i=0;i<1500&&(await refused(second)||await refused(functions)||!await refused(first));i++)await Bun.sleep(10);
   expect(await refused(second)).toBe(false);
   expect(await refused(first)).toBe(true);
   expect(await refused(functions)).toBe(false);
   expect(c.record().listeners.map((item:{name:string}) => item.name)).toContain(`functions-storage:${RUNTIME}`);
  }finally{c.gateway.stop();}
 });
 test('opens no fixed listener until it is promoted, and its private port answers with its pid',async() => {
  const c=await candidate();
  try{
   expect(await refused(c.consolePort)).toBe(true);
   expect(await refused(c.databasePort)).toBe(true);
   expect(await refused(c.studioPort)).toBe(true);
   expect(c.gateway.port).not.toBe(c.consolePort);
   const answer=await get(c.gateway.port);
   expect(answer.status).toBe(200);
   expect(answer.headers.get('x-sbarbase-pid')).toBe(String(process.pid));
   expect(c.record()).toEqual({pid:process.pid,protocol:1,role:'candidate',frozen:false,listeners:[]});
   expect(c.calls).toEqual([]);
  }finally{c.gateway.stop();}
 });
 test('promotion binds every fixed listener, attaches to each descriptor, then records them all steered',async() => {
  const c=await candidate();
  try{
   const shared=await c.gateway.promote!();
   expect(shared).toBe(`http://127.0.0.1:${c.consolePort}`);
   expect((await get(c.consolePort)).status).toBe(200);
   expect(c.calls.length).toBe(3);
   for(const call of c.calls)expect(call).toMatch(/^attach:\d+$/);
   expect(new Set(c.calls).size).toBe(3);
   const names=c.record().listeners.map((item:{name:string}) => item.name);
   expect(names).toEqual(['console','database','studio-internal']);
   expect(c.record().listeners.every((item:{steered:boolean}) => item.steered)).toBe(true);
   expect(await refused(c.databasePort)).toBe(false);
   expect(await refused(c.studioPort)).toBe(false);
  }finally{c.gateway.stop();}
 });
 test('the descriptor attached to is the one that listens on the port',async() => {
  const fds:Record<string,number>={};
  const seen:number[]=[];
  const c=await candidate({},{attach:fd => {seen.push(fd);return 0;},detach:() => 0});
  try{
   await c.gateway.promote!();
   fds.console=ownDescriptor('127.0.0.1',c.consolePort);fds.database=ownDescriptor('127.0.0.1',c.databasePort);fds.studio=ownDescriptor('127.0.0.1',c.studioPort);
   expect(seen).toEqual([fds.console,fds.database,fds.studio]);
  }finally{c.gateway.stop();}
 });
 test('a refused attach fails the promotion and leaves nothing listening on a fixed port',async() => {
  let count=0;
  const c=await candidate({},{attach:() => (++count===2?-1:0),detach:() => 0});
  try{
   await expect(c.gateway.promote!()).rejects.toThrow('could not be attached to database');
   expect(await refused(c.consolePort)).toBe(true);
   expect(await refused(c.databasePort)).toBe(true);
   expect(c.record().listeners).toEqual([]);
  }finally{c.gateway.stop();}
 });
 test('a port that cannot be bound fails the promotion, and what was bound is closed again',async() => {
  const hog=createServer();servers.push(hog);
  const taken=await freePort();
  await new Promise<void>(resolve => hog.listen({port:taken,host:'127.0.0.1'},() => resolve()));
  const c=await candidate({databaseAt:{port:taken,host:'127.0.0.1'}});
  try{
   await expect(c.gateway.promote!()).rejects.toThrow();
   expect(await refused(c.consolePort)).toBe(true);
   // The console was bound and steered before the database failed; it is closed again.
   expect(c.calls.length).toBe(1);
   expect(c.record().listeners).toEqual([]);
  }finally{c.gateway.stop();}
 });
 async function withTls() {
  const directory=join(temporary(),'tls');
  return {tls:ensureDatabaseTls(directory,['127.0.0.1','::1','localhost']),tlsDirectory:directory,expire:() => rmSync(join(directory,'server.pem'),{force:true})};
 }
 const watch=async(done:() => boolean) => {for(let i=0;i<1500&&!done();i++)await Bun.sleep(10);return done();};
 test('the external database listener is bound, steered, recorded and served after promotion',async() => {
  const {tls,tlsDirectory}=await withTls();
  const external=await freePort();
  const c=await candidate({tls,tlsDirectory,tlsCheckMs:50,externalAt:{port:external,host:'127.0.0.1'}});
  try{
   expect(await refused(external)).toBe(true);
   await c.gateway.promote!();
   expect(c.record().listeners.map((item:{name:string}) => item.name)).toEqual(['console','database','database-external','studio-internal']);
   expect(c.record().listeners[2]).toEqual({name:'database-external',host:'127.0.0.1',port:external,steered:true});
   expect(c.calls.length).toBe(4);
   expect(c.calls[2]).toBe('attach:'+ownDescriptor('127.0.0.1',external));
   expect(await refused(external)).toBe(false);
  }finally{c.gateway.stop();}
 });
 test('an external listener that cannot be bound fails the promotion and is not left listening',async() => {
  const {tls,tlsDirectory}=await withTls();
  const taken=await freePort();
  const hog=createServer();servers.push(hog);
  await new Promise<void>(resolve => hog.listen({port:taken,host:'127.0.0.1'},() => resolve()));
  const c=await candidate({tls,tlsDirectory,tlsCheckMs:50,externalAt:{port:taken,host:'127.0.0.1'}});
  try{
   await expect(c.gateway.promote!()).rejects.toThrow();
   expect(await refused(c.consolePort)).toBe(true);
   expect(await refused(c.databasePort)).toBe(true);
   expect(c.record().listeners).toEqual([]);
  }finally{c.gateway.stop();}
 });
 test('the TLS renewal check starts with the promotion and stops with the drain, and not before',async() => {
  const {tls,tlsDirectory,expire}=await withTls();
  const external=await freePort();
  const c=await candidate({tls,tlsDirectory,tlsCheckMs:30,externalAt:{port:external,host:'127.0.0.1'}});
  let reloads=0;
  const reload=c.app.database.reloadTls.bind(c.app.database);
  c.app.database.reloadTls=async renewed => {reloads++;await reload(renewed);};
  try{
   expire();
   await Bun.sleep(300);
   expect(reloads).toBe(0);
   await c.gateway.promote!();
   expire();
   expect(await watch(() => reloads>=1)).toBe(true);
   const draining=c.gateway.drain(2_000,50);
   await draining;
   const seen=reloads;
   expire();
   await Bun.sleep(300);
   expect(reloads).toBe(seen);
  }finally{c.gateway.stop();}
 });
 test('a Functions Storage listener is bound, steered and recorded under its environment',async() => {
  const port=await freePort();
  const c=await candidate({endpoints:() => functionsEndpoints(port)});
  try{
   await c.gateway.promote!();
   expect(c.calls.length).toBe(4);
   expect(c.record().listeners.map((item:{name:string}) => item.name)).toEqual(['console','database','studio-internal',`functions-storage:${RUNTIME}`]);
   expect(c.record().listeners[3]).toEqual({name:`functions-storage:${RUNTIME}`,host:'127.0.0.1',port,steered:true});
   expect(await refused(port)).toBe(false);
  }finally{c.gateway.stop();}
 });
 test('a Functions Storage listener that cannot be bound fails the promotion although its own retry only logs',async() => {
  const taken=await freePort();
  const hog=createServer();servers.push(hog);
  await new Promise<void>(resolve => hog.listen({port:taken,host:'127.0.0.1'},() => resolve()));
  const lines:string[]=[];const error=console.error;console.error=(...args:unknown[]) => {lines.push(args.join(' '));};
  const c=await candidate({endpoints:() => functionsEndpoints(taken)});
  try{
   await expect(c.gateway.promote!()).rejects.toThrow('Functions Storage listener could not be bound');
   expect(lines.some(line => line.includes('Functions Storage listener for'))).toBe(true);
   expect(await refused(c.consolePort)).toBe(true);
   expect(c.record().listeners).toEqual([]);
  }finally{console.error=error;c.gateway.stop();}
 });
 test('a promoted candidate drains its fixed console too: it answers with connection close during the grace',async() => {
  const c=await candidate();
  try{
   await c.gateway.promote!();
   const draining=c.gateway.drain(3_000,600);
   await Bun.sleep(100);
   const answer=await fetch(`http://127.0.0.1:${c.consolePort}/health`);
   await answer.text();
   expect(answer.headers.get('connection')).toBe('close');
   expect(await draining).toBe(true);
   expect(await refused(c.consolePort)).toBe(true);
  }finally{c.gateway.stop();}
 });
 test('the release detaches every steered descriptor once, and the record says so',async() => {
  const c=await candidate();
  try{
   await c.gateway.promote!();
   const attached=c.calls.map(call => call.split(':')[1]);
   c.gateway.release!();c.gateway.release!();
   expect(c.calls.slice(3)).toEqual(attached.map(fd => 'detach:'+fd));
   expect(c.record().listeners.every((item:{steered:boolean}) => !item.steered)).toBe(true);
  }finally{c.gateway.stop();}
 });
 test('after the release the process is the serving one: its record says so and it freezes when named for the next handover',async() => {
  const c=await candidate({},undefined);
  const until=async(done:() => boolean) => {for(let i=0;i<1500&&!done();i++)await Bun.sleep(10);return done();};
  try{
   await c.gateway.promote!();
   // Until the release the record is still the candidate's: the supervisor's checks of the groups read that.
   expect(c.record().role).toBe('candidate');
   c.gateway.release!();
   expect(c.record().role).toBe('serving');
   expect(c.record().frozen).toBe(false);
   writeFileSync(join(c.state,'handover.json'),JSON.stringify({pid:process.pid,started:processStart(process.pid)}));
   expect(await until(() => c.record().frozen===true)).toBe(true);
  }finally{c.gateway.stop();}
 });
 test('a candidate that was not promoted never freezes, whatever handover.json says',async() => {
  const c=await candidate();
  try{
   writeFileSync(join(c.state,'handover.json'),JSON.stringify({pid:process.pid,started:processStart(process.pid)}));
   await Bun.sleep(300);
   expect(c.record().frozen).toBe(false);
  }finally{c.gateway.stop();}
 });
 test('a detach that fails is logged, changes nothing else and leaves that listener marked steered',async() => {
  const lines:string[]=[];const error=console.error;console.error=(...args:unknown[]) => {lines.push(args.join(' '));};
  const c=await candidate({},{attach:() => 0,detach:() => -1});
  try{
   await c.gateway.promote!();
   c.gateway.release!();
   expect(lines.some(line => line.includes('could not be detached from console'))).toBe(true);
   expect(c.record().listeners.every((item:{steered:boolean}) => item.steered)).toBe(true);
   expect((await get(c.consolePort)).status).toBe(200);
  }finally{console.error=error;c.gateway.stop();}
 });
 test('releasing before the promotion does nothing',async() => {
  const c=await candidate();
  try{
   c.gateway.release!();
   expect(c.calls).toEqual([]);
  }finally{c.gateway.stop();}
 });
});

describe('a serving gateway with the switch on',() => {
 test('records its fixed listeners, and freezes when handover.json names it',async() => {
  const state=temporary();
  const consolePort=await freePort(),databasePort=await freePort();
  const studio={host:'127.0.0.1',port:await freePort()};
  let at:{host:string;port:number}|undefined;
  const gateway=await startGateway(stub(),{hold:() => undefined,consolePort,databaseAt:{port:databasePort,host:'127.0.0.1'},
   endpoints:() => ({}),database:() => undefined,studio:() => at,handover:{candidate:false,protocol:1,stateDirectory:state,freezeEveryMs:30}});
  const record=() => JSON.parse(readFileSync(join(state,`listeners-${process.pid}.json`),'utf8'));
  const until=async(done:() => boolean) => {for(let i=0;i<1500&&!done();i++)await Bun.sleep(10);return done();};
  try{
   expect(record()).toEqual({pid:process.pid,protocol:1,role:'serving',frozen:false,listeners:[
    {name:'console',host:'127.0.0.1',port:consolePort,steered:false},{name:'database',host:'127.0.0.1',port:databasePort,steered:false}]});
   at=studio;
   expect(await until(() => record().listeners.some((item:{name:string}) => item.name==='studio-internal'))).toBe(true);
   expect((await get(consolePort)).headers.get('x-sbarbase-pid')).toBe(String(process.pid));
   writeFileSync(join(state,'handover.json'),JSON.stringify({pid:process.pid,started:processStart(process.pid)}));
   expect(await until(() => record().frozen===true)).toBe(true);
   // Frozen: the Studio route is no longer closed or opened, whatever the state says.
   at=undefined;
   await Bun.sleep(300);
   expect(record().listeners.some((item:{name:string}) => item.name==='studio-internal')).toBe(true);
   expect(await refused(studio.port)).toBe(false);
  }finally{gateway.stop();}
 });
 test('a serving gateway lists its Functions Storage listeners as soon as it is up, before any timer tick',async() => {
  const state=temporary(),port=await freePort();
  const gateway=await startGateway(stub(),{hold:() => undefined,consolePort:await freePort(),databaseAt:null,
   endpoints:() => functionsEndpoints(port),database:() => undefined,studio:() => undefined,
   handover:{candidate:false,protocol:1,stateDirectory:state,freezeEveryMs:600_000}});
  try{
   const names=JSON.parse(readFileSync(join(state,`listeners-${process.pid}.json`),'utf8')).listeners.map((item:{name:string}) => item.name);
   expect(names).toEqual(['console',`functions-storage:${RUNTIME}`]);
  }finally{gateway.stop();}
 });
 test('a frozen serving gateway opens no listener when the endpoints gain an environment',async() => {
  const state=temporary(),port=await freePort();
  let endpoints:Record<string,any>={};
  const gateway=await startGateway(stub(),{hold:() => undefined,consolePort:await freePort(),databaseAt:null,
   endpoints:() => endpoints,database:() => undefined,studio:() => undefined,
   handover:{candidate:false,protocol:1,stateDirectory:state,freezeEveryMs:30}});
  const record=() => JSON.parse(readFileSync(join(state,`listeners-${process.pid}.json`),'utf8'));
  try{
   writeFileSync(join(state,'handover.json'),JSON.stringify({pid:process.pid,started:processStart(process.pid)}));
   for(let i=0;i<1500&&record().frozen!==true;i++)await Bun.sleep(10);
   expect(record().frozen).toBe(true);
   endpoints=functionsEndpoints(port);
   await Bun.sleep(400);
   expect(await refused(port)).toBe(true);
   expect(record().listeners.map((item:{name:string}) => item.name)).toEqual(['console']);
  }finally{gateway.stop();}
 });
 test('without the switch no record is written, no header added and the Studio route follows the state',async() => {
  const consolePort=await freePort();
  const studio={host:'127.0.0.1',port:await freePort()};
  let at:{host:string;port:number}|undefined=studio;
  const before=existsSync('.lab/upstream/listeners-'+process.pid+'.json');
  const gateway=await startGateway(stub(),{hold:() => undefined,consolePort,databaseAt:null,endpoints:() => ({}),database:() => undefined,studio:() => at});
  try{
   const answer=await get(consolePort);
   expect(answer.status).toBe(200);
   expect(answer.headers.get('x-sbarbase-pid')).toBeNull();
   expect(existsSync('.lab/upstream/listeners-'+process.pid+'.json')).toBe(before);
   expect(gateway.promote).toBeUndefined();
   expect(gateway.release).toBeUndefined();
   for(let i=0;i<1500&&await refused(studio.port);i++)await Bun.sleep(10);
   expect(await refused(studio.port)).toBe(false);
   at=undefined;
   for(let i=0;i<1500&&!await refused(studio.port);i++)await Bun.sleep(10);
   expect(await refused(studio.port)).toBe(true);
  }finally{gateway.stop();}
 });
});
