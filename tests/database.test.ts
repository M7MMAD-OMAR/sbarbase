import {test,expect} from 'bun:test';
import {connect,createServer} from 'node:net';
import {mkdtempSync,readFileSync,rmSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {databaseProxy,databaseListen,decide,startupFields} from '../src/http/database-proxy';
import {databaseHandler,connection,connectionString} from '../src/control/database';
import {Catalog} from '../src/control/catalog';

const E='e_'+'a'.repeat(24),OTHER='e_'+'b'.repeat(24);

function startup(fields:Record<string,string>) {
 const body=Buffer.concat([...Object.entries(fields).map(([key,value])=>Buffer.from(`${key}\0${value}\0`)),Buffer.from('\0')]);
 const header=Buffer.alloc(8);header.writeInt32BE(body.length+8,0);header.writeInt32BE(196608,4);
 return Buffer.concat([header,body]);
}
const sslRequest=()=>{const packet=Buffer.alloc(8);packet.writeInt32BE(8,0);packet.writeInt32BE(80877103,4);return packet;};

test('only a developer login for its own database is let through',()=>{
 expect(decide({user:`${E}_developer`,database:E})).toEqual({ok:true,user:`${E}_developer`,database:E});
 for(const fields of [{user:'supabase_admin',database:E},{user:`${E}_auth`,database:E},{user:`${E}_developer`,database:OTHER},
  {user:`${E}_developer`,database:'postgres'},{user:`${E}_developer`},{database:E},{user:`x${E}_developer`,database:E}])
  expect(decide(fields).ok).toBe(false);
 expect(startupFields(startup({user:'u',database:'d',application_name:'psql'}).subarray(8))).toEqual({user:'u',database:'d',application_name:'psql'});
});

test('the listener declines TLS, forwards an allowed startup byte for byte and refuses the rest before PostgreSQL',async()=>{
 const received:Buffer[]=[];
 const upstream=createServer(socket=>{socket.on('data',chunk=>{received.push(chunk);socket.write('R-from-postgres');});});
 await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',()=>resolve()));
 const port=(upstream.address() as {port:number}).port;
 const proxy=await databaseProxy({port:0,host:'127.0.0.1',target:()=>({host:'127.0.0.1',port})});
 const talk=(packets:Buffer[])=>new Promise<string>(resolve=>{
  const socket=connect(proxy.port,'127.0.0.1',()=>{for(const packet of packets)socket.write(packet);});
  let answer='';socket.on('data',chunk=>{answer+=chunk.toString('latin1');if(answer.includes('R-from-postgres')){socket.destroy();resolve(answer);}});
  socket.on('close',()=>resolve(answer));
 });
 try {
  const allowed=startup({user:`${E}_developer`,database:E});
  const answer=await talk([sslRequest(),allowed]);
  expect(answer.startsWith('N')).toBe(true);
  expect(answer).toContain('R-from-postgres');
  expect(Buffer.concat(received).equals(allowed)).toBe(true);
  received.length=0;
  const refused=await talk([startup({user:'supabase_admin',database:E})]);
  expect(refused.startsWith('E')).toBe(true);
  expect(refused).toContain('28000');
  expect(await talk([startup({user:`${E}_developer`,database:OTHER})])).toContain('its own database');
  expect(received).toHaveLength(0);
 } finally {proxy.stop();upstream.close();}
});

test('the listener is on loopback port 6543 unless set, and can be turned off',()=>{
 expect(databaseListen({})).toEqual({port:6543,host:'127.0.0.1'});
 expect(databaseListen({SBARBASE_DATABASE_PORT:'5433',SBARBASE_DATABASE_BIND:'0.0.0.0'})).toEqual({port:5433,host:'0.0.0.0'});
 expect(databaseListen({SBARBASE_DATABASE_PORT:'off'})).toBeNull();
 for(const bad of [{SBARBASE_DATABASE_PORT:'80'},{SBARBASE_DATABASE_PORT:'x'},{SBARBASE_DATABASE_BIND:'example.com'}])
  expect(()=>databaseListen(bad)).toThrow();
 expect(connectionString(connection(E,{}))).toBe(`postgresql://${E}_developer:[YOUR-PASSWORD]@127.0.0.1:6543/${E}`);
});

test('owners and admins turn access on and get the password once; a reset needs access on',async()=>{
 const catalog=new Catalog(':memory:'),directory=mkdtempSync(join(tmpdir(),'database-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=databaseHandler(catalog,async request=>request.headers.get('authorization'),directory);
  const call=(actor:string,path='',method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/database${path}`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime!;
  expect((await call('carol')).status).toBe(403);
  expect((await call('alice','/password','POST')).status).toBe(409);
  const on=await call('alice','','PUT',{enabled:true});
  expect(on.status).toBe(202);
  const body=(await on.json()) as any;
  expect(body.data.password).toMatch(/^[A-Za-z0-9_-]{32}$/);
  expect(body.data.url).toContain(body.data.password);
  const file=join(directory,`${runtime}.json`);
  expect(JSON.parse(readFileSync(file,'utf8')).password).toBe(body.data.password);
  expect(statSync(file).mode&0o077).toBe(0);
  const later=await (await call('alice')).text();
  expect(later).not.toContain(body.data.password);
  expect((await call('alice','','PUT',{enabled:false})).status).toBe(409);
  expect((await call('alice','','PUT',{enabled:'yes'})).status).toBe(400);
 } finally {catalog.close();rmSync(directory,{recursive:true,force:true});}
});
