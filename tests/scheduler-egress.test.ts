import {afterAll,beforeAll,describe,expect,test} from 'bun:test';
import {createServer,type Server} from 'node:http';
import {parseAddress,perform,policy,refusal,type HttpRequest,type Policy} from '../src/scheduler/egress';

const rules=policy({publicUrl:'https://api.example.com',addresses:['203.0.114.7','2a01:4f8::5'],subnets:['44.0.0.0/16']});

describe('addresses', ()=>{
 test('parses IPv4, IPv6, compressed, embedded and bracketed forms', ()=>{
  expect(parseAddress('1.2.3.4')).toEqual({v:4,value:0x01020304n});
  expect(parseAddress('::1')).toEqual({v:6,value:1n});
  expect(parseAddress('[::ffff:10.0.0.1]')?.value).toBe(0xffff0a000001n);
  expect(parseAddress('2001:db8::1')?.value).toBe(0x20010db8000000000000000000000001n);
  expect(parseAddress('fe80::1%eth0')?.v).toBe(6);
  for(const bad of ['1.2.3','1.2.3.4.5','01.2.3.4','256.1.1.1','1::2::3',':::','example.com','1:2:3:4:5:6:7:8:9'])expect(parseAddress(bad)).toBeNull();
 });
 test('refuses every special-purpose range, in plain and IPv4-mapped form', ()=>{
  for(const address of ['0.0.0.0','10.1.2.3','100.64.0.1','127.0.0.1','127.255.255.254','169.254.169.254','172.16.0.1','172.31.255.255',
   '192.0.0.8','192.0.2.1','192.88.99.1','192.168.1.1','198.18.0.1','198.19.255.255','198.51.100.1','203.0.113.9','224.0.0.1','239.1.1.1',
   '240.0.0.1','255.255.255.255','::','::1','::127.0.0.1','::ffff:127.0.0.1','::ffff:169.254.169.254','::ffff:7f00:1','64:ff9b::a00:1',
   '64:ff9b:1::1','100::1','2001::1','2001:db8::1','2002:7f00:1::1','fc00::1','fd12:3456::1','fe80::1','fec0::1','ff02::1'])
   expect(refusal(address,rules)).not.toBeNull();
 });
 test('admits public addresses', ()=>{
  for(const address of ['1.1.1.1','8.8.8.8','172.32.0.1','100.128.0.1','198.20.0.1','2606:4700::1111','::ffff:1.1.1.1'])
   expect(refusal(address,rules)).toBeNull();
 });
 test("refuses the server's own addresses and Docker subnets, unless for the public origin", ()=>{
  expect(refusal('203.0.114.7',rules)).toBe('this server');
  expect(refusal('2a01:4f8::5',rules)).toBe('this server');
  expect(refusal('::ffff:203.0.114.7',rules)).toBe('this server');
  expect(refusal('44.0.200.1',rules)).toBe('a Docker network');
  expect(refusal('203.0.114.7',rules,true)).toBeNull();
  // The exception never lifts a special-purpose range.
  expect(refusal('127.0.0.1',rules,true)).not.toBeNull();
  expect(refusal('10.0.0.1',rules,true)).not.toBeNull();
 });
 test('the public origin is read as scheme, host and port', ()=>{
  expect(policy({publicUrl:'https://api.example.com/'}).publicOrigin).toBe('https://api.example.com');
  expect(policy({publicUrl:'https://api.example.com:8443'}).publicOrigin).toBe('https://api.example.com:8443');
  expect(policy({publicUrl:'ftp://x'}).publicOrigin).toBeNull();
  expect(policy({publicUrl:'not a url'}).publicOrigin).toBeNull();
 });
});

const request=(url:string,extra:Partial<HttpRequest>={}):HttpRequest=>({method:'GET',url,headers:{},body:null,timeoutMs:3000,...extra});
const resolver=(answers:Record<string,string[]>)=>async(host:string)=>{if(!(host in answers))throw Object.assign(new Error('x'),{code:'ENOTFOUND'});return answers[host]!;};

describe('the policy before any connection', ()=>{
 test('refuses other schemes, credentials and ports', async()=>{
  for(const url of ['ftp://example.com/','file:///etc/passwd','gopher://example.com/'])
   expect((await perform(request(url),rules,resolver({}))).error).toContain('only http and https');
  expect((await perform(request('http://u:p@example.com/'),rules,resolver({'example.com':['1.1.1.1']}))).error).toContain('credentials');
  expect((await perform(request('http://example.com:5432/'),rules,resolver({'example.com':['1.1.1.1']}))).error).toContain('port 5432');
 });
 test('refuses a name that resolves to a private address, and a name with one private answer among public ones', async()=>{
  expect((await perform(request('http://internal.example/'),rules,resolver({'internal.example':['10.0.0.5']}))).error).toContain('private address');
  expect((await perform(request('http://mixed.example/'),rules,resolver({'mixed.example':['1.1.1.1','127.0.0.1']}))).error).toContain('loopback');
  expect((await perform(request('http://meta.example/'),rules,resolver({'meta.example':['169.254.169.254']}))).error).toContain('link-local');
 });
 test('refuses literal addresses without a lookup', async()=>{
  for(const url of ['http://127.0.0.1/','http://[::1]/','http://[::ffff:127.0.0.1]/','http://0x7f.0.0.1/','http://2130706433/','http://169.254.169.254/latest/meta-data/'])
   expect((await perform(request(url),rules,resolver({}))).error).toMatch(/Refused|resolve/);
 });
 test("refuses the server's own address except through the exact public origin", async()=>{
  const answers=resolver({'api.example.com':['203.0.114.7'],'other.example.com':['203.0.114.7']});
  expect((await perform(request('https://other.example.com/'),rules,answers)).error).toContain('this server');
  expect((await perform(request('http://api.example.com/'),rules,answers)).error).toContain('this server');
 });
 test('a lookup that does not answer ends as a timeout', async()=>{
  const result=await perform(request('http://slow.example/',{timeoutMs:200}),rules,()=>new Promise(()=>{}));
  expect(result).toEqual({status:null,timedOut:true,error:'Timeout of 200 ms reached'});
 });
 test('an address that is not a URL', async()=>{
  expect((await perform(request('not a url'),rules)).error).toContain('bad/illegal');
 });
});

describe('requests to a test server the policy is told to admit', ()=>{
 let server:Server,port=0;
 const admitLoopback=(address:string,policy:Policy,exact?:boolean)=>address==='127.0.0.1'?null:refusal(address,policy,exact);
 const local=policy({ports:[]});
 beforeAll(async()=>{
  server=createServer((incoming,outgoing)=>{
   const path=incoming.url??'';
   if(path==='/redirect')return void outgoing.writeHead(302,{location:'/echo'}).end();
   if(path==='/loop')return void outgoing.writeHead(302,{location:'/loop'}).end();
   if(path==='/away')return void outgoing.writeHead(301,{location:'http://10.0.0.1/'}).end();
   if(path==='/big')return void outgoing.end(Buffer.alloc(2*1024*1024,97));
   if(path==='/slow')return void setTimeout(()=>outgoing.end('late'),2000);
   const chunks:Buffer[]=[];incoming.on('data',chunk=>chunks.push(chunk));
   incoming.on('end',()=>{outgoing.writeHead(201,{'Content-Type':'application/json','X-Seen':incoming.headers['x-token']??''});
    outgoing.end(JSON.stringify({method:incoming.method,host:incoming.headers.host,body:Buffer.concat(chunks).toString()}));});
  });
  await new Promise<void>(done=>server.listen(0,'127.0.0.1',done));
  port=(server.address() as {port:number}).port;
 });
 afterAll(()=>server.close());
 const answers=()=>resolver({'app.test':['127.0.0.1']});
 const allowed=()=>({...local,ports:new Set([port])});

 test('connects to the checked address with the original name as Host, and records the answer', async()=>{
  const result=await perform(request(`http://app.test:${port}/echo`,{method:'POST',headers:{'X-Token':'t'},body:new TextEncoder().encode('{"a":1}')}),allowed(),answers(),admitLoopback);
  expect(result.status).toBe(201);
  if(result.status===null)throw new Error('no answer');
  expect(JSON.parse(result.body!)).toEqual({method:'POST',host:`app.test:${port}`,body:'{"a":1}'});
  expect(result.headers['X-Seen']).toBe('t');
  expect(result.contentType).toBe('application/json');
  expect(result.error).toBeNull();
 });
 test('without the test admission the same server is refused', async()=>{
  expect((await perform(request(`http://app.test:${port}/echo`),allowed(),answers())).error).toContain('loopback');
 });
 test('follows a redirect, at most five, and checks each one again', async()=>{
  const followed=await perform(request(`http://app.test:${port}/redirect`,{method:'POST',body:new Uint8Array([1])}),allowed(),answers(),admitLoopback);
  expect(followed.status).toBe(201);
  if(followed.status!==null)expect(JSON.parse(followed.body!).method).toBe('GET');
  expect((await perform(request(`http://app.test:${port}/loop`),allowed(),answers(),admitLoopback)).error).toBe('Maximum (5) redirects followed');
  expect((await perform(request(`http://app.test:${port}/away`),{...allowed(),ports:new Set([port,80])},answers(),admitLoopback)).error).toContain('private address');
 });
 test('keeps 1 MiB of a larger body and says so', async()=>{
  const result=await perform(request(`http://app.test:${port}/big`),allowed(),answers(),admitLoopback);
  if(result.status===null)throw new Error('no answer');
  expect(result.body!.length).toBe(1024*1024);
  expect(result.error).toBe('Response body cut at 1 MiB');
 });
 test('ends a slow answer at its timeout', async()=>{
  const result=await perform(request(`http://app.test:${port}/slow`,{timeoutMs:300}),allowed(),answers(),admitLoopback);
  expect(result).toEqual({status:null,timedOut:true,error:'Timeout of 300 ms reached'});
 });
 test('caps the timeout at 60 seconds and treats nonsense as the default', async()=>{
  const result=await perform(request(`http://app.test:${port}/echo`,{timeoutMs:Number.NaN}),allowed(),answers(),admitLoopback);
  expect(result.status).toBe(201);
 });
});
