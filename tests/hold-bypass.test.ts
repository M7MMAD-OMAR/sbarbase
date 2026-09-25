import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,unlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {confirmationProbe,holdExceptProbe,PROBE_HEADER} from '../src/gateway/hold-bypass';
import {createGateway} from '../src/gateway/handler';
import {KeyStore} from '../src/control/keys';
import {healthHandler} from '../src/http/health';

const TOKEN='t'.repeat(43);
const RUNTIME='e_0123456789abcdef01234567';

function place() {
 const path=mkdtempSync(join(tmpdir(),'sbarbase-probe-'));
 const token=join(path,'probe-token');
 writeFileSync(token,TOKEN+'\n');
 return {token,done(){rmSync(path,{recursive:true,force:true});}};
}
const probeRequest=(path:string,headers:Record<string,string>={})=>
 new Request('http://127.0.0.1'+path,{headers:{apikey:TOKEN,[PROBE_HEADER]:TOKEN,...headers}});

test('only the supervisor\'s own probe of REST or Auth passes, only while the hold is on',()=>{
 const files=place();
 try {
  let held=true,clock=0;
  const probe=confirmationProbe(()=>held,files.token,{ttlMs:1000,now:()=>clock});
  expect(probe.matches(probeRequest(`/${RUNTIME}/rest/v1/`))).toBe(true);
  expect(probe.matches(probeRequest(`/${RUNTIME}/auth/v1/health`))).toBe(true);
  // Another token, no token, another route, or anything the TLS proxy forwarded: never the probe.
  expect(probe.matches(probeRequest(`/${RUNTIME}/rest/v1/`,{[PROBE_HEADER]:'u'.repeat(43)}))).toBe(false);
  expect(probe.matches(new Request(`http://127.0.0.1/${RUNTIME}/rest/v1/`))).toBe(false);
  expect(probe.matches(probeRequest(`/${RUNTIME}/functions/v1/hello`))).toBe(false);
  expect(probe.matches(probeRequest(`/${RUNTIME}/storage/v1/bucket`))).toBe(false);
  expect(probe.matches(probeRequest('/management/v1/organizations'))).toBe(false);
  for(const name of ['x-forwarded-proto','x-forwarded-for','forwarded'])
   expect(probe.matches(probeRequest(`/${RUNTIME}/rest/v1/`,{[name]:'https'}))).toBe(false);
  expect(probe.key(TOKEN)).toBe(true);
  expect(probe.key('u'.repeat(43))).toBe(false);
  held=false;
  expect(probe.matches(probeRequest(`/${RUNTIME}/rest/v1/`))).toBe(false);
  expect(probe.key(TOKEN)).toBe(false);
  // A removed token is gone within the cache time.
  held=true;
  unlinkSync(files.token);
  expect(probe.key(TOKEN)).toBe(true);
  clock=1000;
  expect(probe.key(TOKEN)).toBe(false);
  writeFileSync(files.token,'short');
  clock=2000;
  expect(probe.key('short')).toBe(false);
 } finally {files.done();}
});

test('the hold stops every other application request and lets the probe through',async()=>{
 const files=place();
 try {
  const seen:string[]=[];
  const probe=confirmationProbe(()=>true,files.token);
  const handler=holdExceptProbe(async request=>{seen.push(new URL(request.url).pathname);return new Response('ok');},()=>true,probe);
  expect((await handler(probeRequest(`/${RUNTIME}/rest/v1/`))).status).toBe(200);
  expect((await handler(new Request(`http://127.0.0.1/${RUNTIME}/rest/v1/items`,{headers:{apikey:TOKEN}}))).status).toBe(503);
  expect((await handler(probeRequest(`/${RUNTIME}/rest/v1/`,{'x-forwarded-proto':'https'}))).status).toBe(503);
  expect(seen).toEqual([`/${RUNTIME}/rest/v1/`]);
 } finally {files.done();}
});

test('the probe goes through key resolution and the proxy as an application does',async()=>{
 const files=place();
 const keys=new KeyStore(':memory:');
 try {
  let held=true;
  const probe=confirmationProbe(()=>held,files.token,{ttlMs:0});
  const asked:string[]=[];
  keys.confirmationProbe=(_runtime,token)=>probe.key(token);
  const real=keys.issue(RUNTIME).token;
  const lookup=keys.resolve.bind(keys);
  let reads=0;
  keys.resolve=(environment,token)=>{reads+=1;return lookup(environment,token);};
  const transport=(async(input:RequestInfo|URL,init?:RequestInit)=>{
   asked.push(String(input)+' '+new Headers(init?.headers).get('authorization'));
   return new Response('{}',{status:200});
  }) as typeof fetch;
  const gateway=createGateway(new Map([[RUNTIME,{auth:'http://10.0.0.3:9999',rest:'http://10.0.0.4:3000',keys:[],anonymousToken:'anon',enabled:true}]]),
   transport,(environment,key)=>keys.resolve(environment,key)==='publishable');
  expect((await gateway(probeRequest(`/${RUNTIME}/rest/v1/`))).status).toBe(200);
  expect(reads).toBe(1);
  expect(asked).toEqual(['http://10.0.0.4:3000/ Bearer anon']);
  // A real key still works, and once the hold ended the token is no key at all.
  expect(keys.resolve(RUNTIME,real)).toBe('publishable');
  held=false;
  expect((await gateway(probeRequest(`/${RUNTIME}/rest/v1/`))).status).toBe(401);
 } finally {keys.close();files.done();}
});

test('/health fails when the key store cannot be read',()=>{
 const keys=new KeyStore(':memory:');
 const health=healthHandler({schemaVersion(){return 3;}},()=>true,keys);
 expect(health(new Request('http://127.0.0.1/health')).status).toBe(200);
 keys.close();
 expect(health(new Request('http://127.0.0.1/health')).status).toBe(503);
});
