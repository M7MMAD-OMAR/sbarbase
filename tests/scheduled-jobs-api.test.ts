import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {scheduledJobsHandler} from '../src/control/scheduled-jobs';
import {controlHandler} from '../src/control/handler';
import {KeyStore} from '../src/control/keys';
import {prepare,tag,bounded,literal,integer} from '../src/scheduler/runner';

test('owners and admins turn Scheduled jobs on and off; viewers cannot; one change at a time',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');catalog.setMember('alice',org,'dave','admin');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=scheduledJobsHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor:string,method='GET',body?:unknown,path='scheduled-jobs')=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol')).status).toBe(403);
  expect((await call('carol','PUT',{enabled:true})).status).toBe(403);
  expect((await call('alice','POST',{enabled:true})).status).toBe(405);
  expect((await call('alice','GET',undefined,'scheduled-jobs/x')).status).toBe(404);
  expect(((await (await call('alice')).json()) as {data:{state:string}}).data.state).toBe('off');
  expect((await call('alice','PUT',{enabled:'yes'})).status).toBe(400);
  expect((await call('alice','PUT',{enabled:true,extra:1})).status).toBe(400);
  const on=await call('dave','PUT',{enabled:true});
  expect(on.status).toBe(202);
  expect(((await on.json()) as {data:{state:string;desired:string}}).data).toMatchObject({state:'pending',desired:'on'});
  expect((await call('alice','PUT',{enabled:false})).status).toBe(409);
 } finally {catalog.close();}
});

test('the control handler routes /scheduled-jobs to it',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=controlHandler(catalog,new KeyStore(':memory:'),async request=>request.headers.get('authorization'));
  const response=await handler(new Request(`http://local/management/v1/environments/${environment}/scheduled-jobs`,{headers:{authorization:'alice'}}));
  expect(response.status).toBe(200);
 } finally {catalog.close();}
});

test('a queued row becomes a request only when it is within the limits', ()=>{
 const row=(extra:Record<string,unknown>={})=>({id:1,method:'POST',url:'https://example.com/x',url_size:21,timeout_milliseconds:1000,body_size:2,body:new Uint8Array([123,125]),
  headers_size:30,headers:'{"Content-Type":"application/json"}',...extra});
 expect(prepare(row())).toEqual({method:'POST',url:'https://example.com/x',headers:{'Content-Type':'application/json'},body:new Uint8Array([123,125]),timeoutMs:1000});
 expect(prepare(row({url:null,url_size:9000}))).toBe('URL longer than 8192 bytes');
 expect(prepare(row({body:null,body_size:2_000_000}))).toBe('Request body larger than 1 MiB');
 expect(prepare(row({headers:null,headers_size:70_000}))).toBe('Request headers larger than 64 KiB');
 expect(prepare(row({headers:null,headers_size:null}))).toMatchObject({headers:{}});
 expect(prepare(row({headers:'[1]'}))).toBe('Request headers must be a JSON object');
 expect(prepare(row({headers:'{"Bad Name":"x"}'}))).toBe('Invalid request header');
 expect(prepare(row({headers:'{"X":"a\\r\\nInjected: 1"}'}))).toBe('Invalid request header');
 expect(prepare(row({headers:JSON.stringify(Object.fromEntries(Array.from({length:101},(_,i)=>['h'+i,'v'])))}))).toBe('More than 100 request headers');
 expect(prepare(row({headers:'{"X-Count":5}'}))).toMatchObject({headers:{'X-Count':'5'}});
 expect(prepare(row({method:'PUT'}))).toBe('Unsupported method');
 expect(prepare(row({method:'get'}))).toMatchObject({method:'GET'});
 // The scheduler's own client returns a body as base64 text.
 expect(prepare(row({body:Buffer.from('{"a":1}').toString('base64'),body_size:'7'}))).toMatchObject({body:new Uint8Array(Buffer.from('{"a":1}'))});
});

test('the tag a successful run records, as pg_cron records the last command', ()=>{
 expect(tag('SELECT 1',1,true)).toBe('1 row');
 expect(tag('SELECT 0',0,true)).toBe('0 rows');
 expect(tag('INSERT 0 3',3,true)).toBe('3 rows');
 expect(tag('SHOW',1,true)).toBe('1 row');
 expect(tag('',0,true)).toBe('0 rows');
 expect(tag('SELECT 5',0,false)).toBe('SELECT 5');
 expect(tag('INSERT 0 3',0,false)).toBe('INSERT 0 3');
 expect(tag('VACUUM',0,false)).toBe('VACUUM');
});

test('the per-environment lookup bound', async()=>{
 let active=0,most=0;
 const resolve=bounded(async()=>{active++;most=Math.max(most,active);await Bun.sleep(20);active--;return ['1.1.1.1'];},2);
 await Promise.all(Array.from({length:6},()=>resolve('x')));
 expect(most).toBe(2);
});

test('values go into the bookkeeping SQL as dollar quoted literals under a tag they do not hold, integers as integers', ()=>{
 for(const value of ["it's", '$$', '$q$', 'a\\b', "'; DROP TABLE cron.job; --", '']){
  const quoted=literal(value);
  const tag=quoted.match(/^(\$q[0-9a-f]{16}\$)/)![1]!;
  expect(quoted).toBe(tag+value+tag);
  expect(value.includes(tag)).toBe(false);
 }
 expect(()=>literal('a\u0000b')).toThrow();
 expect(integer(5)).toBe('5');expect(integer('12')).toBe('12');expect(integer(7n)).toBe('7');
 for(const bad of ['1; select 1','1.5',Number.NaN,null,'']) if(bad!=='')expect(()=>integer(bad)).toThrow();
});
