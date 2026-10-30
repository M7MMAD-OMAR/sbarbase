import {test,expect} from 'bun:test';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,statSync,symlinkSync,writeFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {Catalog} from '../src/control/catalog';
import {changeSecrets,deploy,deployment,entryOf,functionsHandler,readManifest,remove} from '../src/control/functions';
import {DEFAULT_SECRETS,digestFolder,functionArchive,functionFiles,functionNames,functionSource,redactCallPath,secretDigest,secretRows,zip} from '../src/control/functions-read';
import {observedGateway,RequestLog} from '../src/gateway/observe';

const A='e_'+'a'.repeat(24),B='e_'+'b'.repeat(24);
const temporary=()=>mkdtempSync(join(tmpdir(),'functions-read-'));
const files=(entries:Record<string,string>)=>deployment({files:entries});

test('a deploy keeps when a function was created and counts its deployments',()=>{
 const root=temporary();
 try {
  deploy(root,A,'hello',files({'index.ts':'one'}),1_000);
  deploy(root,A,'hello',files({'index.ts':'two'}),2_000);
  const third=deploy(root,A,'hello',files({'index.ts':'three'}),3_000);
  expect(third).toMatchObject({created_at:1_000,updated_at:3_000,deployments:3});
  deploy(root,A,'other',files({'index.ts':'x'}),4_000);
  expect(readManifest(root,A).functions.other).toMatchObject({created_at:4_000,deployments:1});
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('the file list puts index.ts first, shows shared files as ../_shared and never follows a link',()=>{
 const root=temporary();
 try {
  const entry=deploy(root,A,'hello',deployment({files:{'util.ts':'u','index.ts':'i','lib/deep.ts':'d'},shared:{'cors.ts':'c'}}),1_000);
  const link=join(root,A,entry.version,'hello','link.ts');
  symlinkSync('/etc/hostname',link);
  expect(functionFiles(root,A,entry.version,'hello').map(file=>file.path)).toEqual(['index.ts','lib/deep.ts','util.ts','../_shared/cors.ts']);
  const source=functionSource(root,A,entry.version,'hello');
  expect(source.truncated).toBe(false);
  expect(source.files[0]).toMatchObject({path:'index.ts',content:'i',shared:false});
  expect(source.files.at(-1)).toMatchObject({path:'../_shared/cors.ts',content:'c',shared:true});
  expect(()=>functionFiles(root,'e_x','v1-aaaaaaaa','hello')).toThrow();
  expect(()=>functionFiles(root,A,'../etc','hello')).toThrow();
  expect(()=>functionFiles(root,A,entry.version,'../hello')).toThrow();
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('a file over 256 KiB, or past 2 MiB in all, comes without content and the answer says so',()=>{
 const root=temporary();
 try {
  const big='x'.repeat(300*1024);
  const entry=deploy(root,A,'hello',deployment({files:{'index.ts':'i','big.ts':big}}),1_000);
  const source=functionSource(root,A,entry.version,'hello');
  expect(source.truncated).toBe(true);
  expect(source.files.find(file=>file.path==='big.ts')).toMatchObject({content:null,size:big.length});
  expect(source.files.find(file=>file.path==='index.ts')!.content).toBe('i');
  const many=Object.fromEntries(Array.from({length:10},(_,index)=>[`f${index}.ts`,'y'.repeat(250*1024)]));
  const second=deploy(root,A,'many',deployment({files:{'index.ts':'i',...many}}),2_000);
  const cut=functionSource(root,A,second.version,'many');
  expect(cut.truncated).toBe(true);
  expect(cut.files.reduce((sum,file)=>sum+(file.content?.length??0),0)).toBeLessThanOrEqual(2*1024*1024);
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('the download is a project shaped ZIP that unzip accepts, with no path that escapes',()=>{
 const root=temporary(),out=temporary();
 try {
  const entry=deploy(root,A,'hello',deployment({files:{'index.ts':'import "../_shared/cors.ts"\n','lib/util.ts':'export {}'},shared:{'cors.ts':'export const c=1'}}),Date.UTC(2026,9,30,12,0,0));
  const archive=functionArchive(root,A,entry.version,'hello',entry.updated_at);
  const path=join(out,'hello.zip');writeFileSync(path,archive);
  const test=spawnSync('unzip',['-t',path],{encoding:'utf8'});
  expect(test.status).toBe(0);
  const listing=spawnSync('unzip',['-Z1',path],{encoding:'utf8'}).stdout.trim().split('\n').sort();
  expect(listing).toEqual(['_shared/cors.ts','hello/index.ts','hello/lib/util.ts']);
  for(const name of listing){expect(name).not.toContain('..');expect(name.startsWith('/')).toBe(false);expect(name).not.toContain('\\');}
  expect(spawnSync('unzip',['-p',path,'hello/index.ts'],{encoding:'utf8'}).stdout).toBe('import "../_shared/cors.ts"\n');
  expect(spawnSync('unzip',['-p',path,'_shared/cors.ts'],{encoding:'utf8'}).stdout).toBe('export const c=1');
 } finally {rmSync(root,{recursive:true,force:true});rmSync(out,{recursive:true,force:true});}
});

test('the ZIP writer refuses a name that could escape the folder it is extracted into',()=>{
 for(const name of ['../x','a/../b','/abs','a\\b','','a//b','.hidden/../x','a/./b'])
  expect(()=>zip([{name,data:new Uint8Array([1])}],0)).toThrow();
 expect(zip([],0).length).toBe(22);
});

test('a digest is an HMAC with the environment\'s own key, outside the folder the container mounts',()=>{
 const secrets=temporary();
 try {
  const root=join(secrets,'functions');
  changeSecrets(root,A,{secrets:{STRIPE_KEY:'sk_live_value',OTHER:'sk_live_value'}},5_000);
  changeSecrets(root,B,{secrets:{STRIPE_KEY:'sk_live_value'}},6_000);
  const rows=secretRows(root,A,JSON.parse(readFileSync(join(root,A,'secrets.json'),'utf8')));
  expect(rows.map(row=>row.name)).toEqual(['OTHER','STRIPE_KEY']);
  expect(rows[1]!.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(rows[1]!.digest).toBe(secretDigest(root,A,'STRIPE_KEY','sk_live_value'));
  // The same value under another name, or in another environment, gives another digest.
  expect(rows[0]!.digest).not.toBe(rows[1]!.digest);
  expect(secretDigest(root,B,'STRIPE_KEY','sk_live_value')).not.toBe(rows[1]!.digest);
  expect(secretDigest(root,A,'STRIPE_KEY','sk_live_valuf')).not.toBe(rows[1]!.digest);
  // Not a plain hash of the value.
  expect(rows[1]!.digest).not.toBe(new Bun.CryptoHasher('sha256').update('sk_live_value').digest('hex'));
  expect(JSON.stringify(rows)).not.toContain('sk_live_value');
  expect(rows[1]!.updated_at).toBe(5_000);
  // The key and the times are not under any folder that is mounted into a functions container.
  const folder=digestFolder(root);
  expect(folder.startsWith(join(root,A))).toBe(false);expect(folder.startsWith(join(root,B))).toBe(false);
  expect(existsSync(join(root,A,`${A}.key`))).toBe(false);
  expect(statSync(join(folder,`${A}.key`)).mode&0o077).toBe(0);
  expect(statSync(join(folder,`${A}.json`)).mode&0o077).toBe(0);
  expect(readFileSync(join(root,A,'secrets.json'),'utf8')).not.toContain(readFileSync(join(folder,`${A}.key`),'utf8').trim());
  // Removing a secret forgets its time; a secret from before the times existed has none.
  changeSecrets(root,A,{secrets:{OTHER:null}},7_000);
  mkdirSync(join(root,B),{recursive:true});writeFileSync(join(root,B,'secrets.json'),JSON.stringify({LEGACY:'v'}));
  expect(secretRows(root,B,{LEGACY:'v'})[0]!.updated_at).toBeNull();
  expect(()=>secretDigest(root,'../x','N','v')).toThrow();
 } finally {rmSync(secrets,{recursive:true,force:true});}
});

test('the default secrets name what a function really gets, and mark what it does not',()=>{
 const byName=Object.fromEntries(DEFAULT_SECRETS.map(item=>[item.name,item]));
 for(const name of ['SUPABASE_URL','SUPABASE_ANON_KEY','SUPABASE_SERVICE_ROLE_KEY'])expect(byName[name]!.provided).toBe('yes');
 for(const name of ['SUPABASE_DB_URL','SUPABASE_PUBLISHABLE_KEYS','SUPABASE_SECRET_KEYS','SUPABASE_JWKS'])expect(byName[name]!.provided).toBe('no');
});

test('only a function the environment has deployed is known to the counters',()=>{
 const root=temporary();
 try {
  const known=functionNames(root);
  expect(known(A,'hello')).toBe(false);
  deploy(root,A,'hello',files({'index.ts':'x'}),1_000);
  expect(known(A,'hello')).toBe(true);
  expect(known(A,'other')).toBe(false);
  expect(known(B,'hello')).toBe(false);
  expect(known('../x','hello')).toBe(false);
  deploy(root,A,'other',files({'index.ts':'x'}),2_000);
  expect(known(A,'other')).toBe(true);
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('function counters bucket by minute, split errors, keep ranges apart and forget a deleted function',()=>{
 let now=Date.UTC(2026,9,30,12,0,30);
 const log=new RequestLog(()=>now);
 const minute=60_000;
 log.recordFunction(A,'hello',{method:'POST',path:'/',status:200,ms:10});
 log.recordFunction(A,'hello',{method:'POST',path:'/x',status:404,ms:30});
 now+=minute;
 log.recordFunction(A,'hello',{method:'GET',path:'/',status:500,ms:200});
 log.recordFunction(B,'hello',{method:'GET',path:'/',status:200,ms:5});
 const metrics=log.functionMetrics(A,'hello','15m');
 expect(metrics.points).toHaveLength(15);
 expect(metrics.totals).toEqual({requests:3,clientErrors:1,serverErrors:1,averageMs:80,maxMs:200});
 expect(metrics.points.at(-1)).toMatchObject({requests:1,serverErrors:1,maxMs:200});
 // Another environment's function of the same name is its own.
 expect(log.functionMetrics(B,'hello','1h').totals.requests).toBe(1);
 expect(log.functionMetrics(A,'missing','1h').totals).toEqual({requests:0,clientErrors:0,serverErrors:0,averageMs:null,maxMs:null});
 expect(log.functionCalls(A,'hello').map(call=>call.status)).toEqual([500,404,200]);
 expect(log.functionCalls(A,'hello',{errors:true}).map(call=>call.status)).toEqual([500,404]);
 expect(log.functionCalls(A,'hello',{since:now-10_000}).map(call=>call.status)).toEqual([500]);
 // The 3 hour range is drawn in 5 minute steps, the day in 15 minute steps.
 expect(log.functionMetrics(A,'hello','3h').points).toHaveLength(36);
 expect(log.functionMetrics(A,'hello','1d').points).toHaveLength(96);
 expect(log.functionMetrics(A,'hello','1d').totals.requests).toBe(3);
// Since the deploy: a deploy resets the cumulative numbers; the minute history stays.
 expect(log.functionMetrics(A,'hello','1h').sinceDeploy).toMatchObject({requests:3,serverErrors:1,complete:false});
 log.deployedFunction(A,'hello',now);
 expect(log.functionMetrics(A,'hello','1h').sinceDeploy).toMatchObject({requests:0,complete:true,since:now});
 log.recordFunction(A,'hello',{method:'GET',path:'/',status:200,ms:1});
 expect(log.functionMetrics(A,'hello','1h').sinceDeploy).toMatchObject({requests:1,complete:true});
 expect(log.functionMetrics(A,'hello','1h').totals.requests).toBe(4);
 log.forgetFunction(A,'hello');
 expect(log.functionMetrics(A,'hello','1h').totals.requests).toBe(0);
 expect(log.functionBucketCount()).toBe(1);
});

test('a bucket older than a day is dropped, so a function never holds more than 1440',()=>{
 let now=Date.UTC(2026,9,30,0,0,0);
 const log=new RequestLog(()=>now);
 for(let index=0;index<1600;index++){log.recordFunction(A,'hello',{method:'GET',path:'/',status:200,ms:1});now+=60_000;}
 expect(log.functionBucketCount()).toBeLessThanOrEqual(1440);
 expect(log.functionMetrics(A,'hello','1d').totals.requests).toBeLessThanOrEqual(1440);
});

test('the gateway counts a call per function only for a name the environment has deployed',async()=>{
 const log=new RequestLog();
 const gateway=observedGateway(async()=>new Response('ok',{status:201}),log,runtime=>runtime===A,undefined,(runtime,name)=>runtime===A&&name==='hello');
 for(const path of ['/hello','/hello/a/b?token=secret','/other','/random-1','/random-2'])
  await gateway(new Request(`http://local/${A}/functions/v1${path}`,{method:'POST'}));
 await gateway(new Request(`http://local/${A}/rest/v1/hello`));
 const metrics=log.functionMetrics(A,'hello','15m');
 expect(metrics.totals.requests).toBe(2);
 expect(log.functionCalls(A,'hello').map(call=>call.path)).toEqual(['/a/b','/']);
 expect(JSON.stringify(log.functionCalls(A,'hello'))).not.toContain('secret');
 expect(log.functionMetrics(A,'other','15m').totals.requests).toBe(0);
 expect(log.functionBucketCount()).toBe(1);
});

test('the function pages are for owners and admins of the environment only and never cross environments',async()=>{
 const catalog=new Catalog(':memory:'),code=temporary(),secrets=temporary(),log=new RequestLog();
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const first=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const other=catalog.createOrganization('bob','B');
  const second=catalog.createEnvironment('bob',catalog.createProject('bob',other,'Q'),'production');
  for(const environment of [first,second]){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment??environment,job.claim!,true);}
  const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets,log);
  const call=(actor:string,environment:string,path:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const runtime=(await (await call('alice',first,'functions')).json() as any).data.runtime as string;
  expect((await call('alice',first,'functions/hello','PUT',{files:{'index.ts':'Deno.serve(()=>new Response("SENTINEL_CODE"))'},shared:{'cors.ts':'c'}})).status).toBe(201);
  expect((await call('bob',second,'functions/secretive','PUT',{files:{'index.ts':'BOB_ONLY'}})).status).toBe(201);
  await call('alice',first,'function-secrets','PUT',{secrets:{STRIPE_KEY:'sk_live_SENTINEL'}});
  await call('bob',second,'function-secrets','PUT',{secrets:{BOB_KEY:'bob_SENTINEL'}});
  log.recordFunction(runtime,'hello',{method:'POST',path:'/',status:200,ms:12});
  const paths=['functions/hello/source','functions/hello/download','functions/hello/metrics?range=1h','functions/hello/logs','function-secrets'];
  for(const path of paths){
   // A viewer and a stranger are refused; nobody reads another environment's function by its name.
   expect((await call('carol',first,path)).status).toBe(403);
   expect((await call('bob',first,path)).status).toBe(403);
   expect((await call('alice',second,path)).status).toBe(403);
   expect((await call('alice',first,path)).status).toBe(200);
  }
  expect((await call('alice',first,'functions/secretive/source')).status).toBe(404);
  expect((await call('bob',second,'functions/hello/metrics')).status).toBe(404);
  const source=await (await call('alice',first,'functions/hello/source')).json() as any;
  expect(source.data.files.map((file:any)=>file.path)).toEqual(['index.ts','../_shared/cors.ts']);
  expect(JSON.stringify(source)).not.toContain('BOB_ONLY');
  const download=await call('alice',first,'functions/hello/download');
  expect(download.headers.get('content-type')).toBe('application/zip');
  expect(download.headers.get('content-disposition')).toBe('attachment; filename="hello.zip"');
  const metrics=await (await call('alice',first,'functions/hello/metrics?range=15m')).json() as any;
  expect(metrics.data.totals.requests).toBe(1);
  for(const range of ['9y','constructor','__proto__','toString','hasOwnProperty'])for(const part of ['metrics','logs'])expect((await call('alice',first,`functions/hello/${part}?range=${range}`)).status).toBe(400);
  expect((await call('alice',first,'functions/hello/logs?limit=0')).status).toBe(400);
  const calls=await (await call('alice',first,'functions/hello/logs?limit=5')).json() as any;
  expect(calls.data.calls).toHaveLength(1);
  // The list carries the new fields; the digest answer carries no value and no other environment's names.
  const list=await (await call('alice',first,'functions')).json() as any;
  expect(list.data.functions[0]).toMatchObject({name:'hello',deployments:1});
  expect(list.data.functions[0].created_at).toBe(list.data.functions[0].updated_at);
  const secretText=await (await call('alice',first,'function-secrets')).text();
  const secretBody=JSON.parse(secretText);
  expect(secretBody.data.secrets.map((row:any)=>row.name)).toEqual(['STRIPE_KEY']);
  expect(secretBody.data.defaults.length).toBe(DEFAULT_SECRETS.length);
  for(const text of [secretText,JSON.stringify(source),JSON.stringify(list)]){expect(text).not.toContain('sk_live_SENTINEL');expect(text).not.toContain('bob_SENTINEL');expect(text).not.toContain('BOB_KEY');}
  // The audit rows and the catalog never hold a secret value either.
  expect(JSON.stringify((catalog as any).db.query('SELECT * FROM audit_events').all())).not.toContain('SENTINEL');
  // A viewer cannot write, and the old write routes answer as before.
  expect((await call('carol',first,'function-secrets','PUT',{secrets:{X:'y'}})).status).toBe(403);
  expect((await call('alice',first,'functions/hello/source','PUT',{})).status).toBe(405);
  expect((await call('alice',first,'function-secrets','POST')).status).toBe(405);
  // A deleted function's counters go with it.
  expect((await call('alice',first,'functions/hello','DELETE')).status).toBe(200);
  expect(log.functionMetrics(runtime,'hello','1h').totals.requests).toBe(0);
 } finally {catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}
});

test('an environment holds at most 100 functions, and a refused deploy leaves no version folder behind',()=>{
 const root=temporary();
 try {
  for(let index=0;index<100;index++)deploy(root,A,`f${index}`,files({'index.ts':'x'}),1_000+index);
  const before=new Set(require('node:fs').readdirSync(join(root,A)));
  expect(()=>deploy(root,A,'one-too-many',files({'index.ts':'x'}),9_000)).toThrow(/at most 100/);
  expect(new Set(require('node:fs').readdirSync(join(root,A)))).toEqual(before);
  // A new version of a name already there is not a new function.
  expect(deploy(root,A,'f0',files({'index.ts':'y'}),9_500).deployments).toBe(2);
 } finally {rmSync(root,{recursive:true,force:true});}
});

test('one environment cannot use up the counters of the others, and a full day of 100 functions stays small',()=>{
 let now=Date.UTC(2026,9,30,0,0,0);
 const log=new RequestLog(()=>now);
 const baseline=(()=>{Bun.gc(true);return process.memoryUsage().heapUsed;})();
 for(let minute=0;minute<1440;minute++){
  for(let index=0;index<100;index++)log.recordFunction(A,`f${index}`,{method:'POST',path:'/',status:index%7===0?500:200,ms:index});
  now+=60_000;
 }
 // 144,000 buckets were asked for; the environment's own ceiling keeps 40,000, the newest ones, and every call counts in the totals.
 expect(log.functionBucketCount()).toBe(40_000);
 expect(log.functionMetrics(A,'f1','15m').totals.requests).toBeGreaterThanOrEqual(10);
 const total=log.functionMetrics(A,'f0','1d').sinceDeploy.requests;
 expect(total).toBe(1440);
 log.recordFunction(B,'hello',{method:'GET',path:'/',status:200,ms:1});
 expect(log.functionMetrics(B,'hello','15m').totals.requests).toBe(1);
 Bun.gc(true);
 const used=process.memoryUsage().heapUsed-baseline;
 // Measured at the ceiling; the number is a gate, not a promise about a quiet installation.
 expect(used).toBeLessThan(40*1024*1024);
});

test('a call path is redacted before it leaves the server',async()=>{
 const catalog=new Catalog(':memory:'),code=temporary(),secrets=temporary(),log=new RequestLog();
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets,log);
  const call=(path:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,
   {method,headers:{authorization:'alice','content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  const runtime=(await (await call('functions')).json() as any).data.runtime as string;
  await call('functions/hello','PUT',{files:{'index.ts':'x'}});
  log.recordFunction(runtime,'hello',{method:'GET',path:'/'+'eyJabcde.fghijk1234.lmnopq5678/token=abc123',status:200,ms:3});
  const text=await (await call('functions/hello/logs')).text();
  expect(text).not.toContain('eyJabcde');expect(text).toContain('[redacted]');
  // A search cannot find what the answer hides, and it finds what the answer shows.
  const found=async(q:string)=>((await (await call(`functions/hello/logs?q=${encodeURIComponent(q)}`)).json()) as any).data.calls.length;
  expect(await found('fghijk12')).toBe(0);expect(await found('lmnopq56')).toBe(0);expect(await found('abc123')).toBe(0);expect(await found('token=')).toBe(0);
  expect(await found('redacted')).toBe(1);expect(await found('get')).toBe(1);expect(await found('zzzz')).toBe(0);
 } finally {catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}
});

test('a call path keeps its readable parts and hides what looks like a token',()=>{
 expect(redactCallPath('/orders/17/items')).toBe('/orders/17/items');
 expect(redactCallPath('/orders/0b1c2d3e-0000-4111-8222-333344445555')).toBe('/orders/0b1c2d3e-0000-4111-8222-333344445555');
 expect(redactCallPath(`/hook/${'sk_'+'live_'}abcdefghijklmnopqrstuvwx/run`)).toBe('/hook/[redacted]/run');
 expect(redactCallPath('/eyJabcde.fghijk1234.lmnopq5678')).toBe('/[redacted]');
 expect(redactCallPath('/token=abc123')).toBe('/[redacted]');
 expect(redactCallPath('/')).toBe('/');
});

test('a name that is also a property of every object is an ordinary function name',async()=>{
 const root=temporary();
 try {
  const names=['constructor','toString','valueOf','hasOwnProperty','isPrototypeOf','propertyIsEnumerable','toLocaleString'];
  for(const name of names){
   expect(entryOf(readManifest(root,A),name)).toBeUndefined();
   const entry=deploy(root,A,name,files({'index.ts':'x'}),1_000);
   expect(entry).toMatchObject({deployments:1,created_at:1_000});
   expect(entryOf(readManifest(root,A),name)).toMatchObject({version:entry.version});
  }
  // The cap counts them too, and an absent one is absent.
  for(let index=0;index<93;index++)deploy(root,A,`f${index}`,files({'index.ts':'x'}),2_000+index);
  expect(()=>deploy(root,A,'extra',files({'index.ts':'x'}),9_000)).toThrow(/at most 100/);
  expect(remove(root,B,'toString')).toBe(false);
  expect(remove(root,A,'toString')).toBe(true);
 } finally {rmSync(root,{recursive:true,force:true});}
 const catalog=new Catalog(':memory:'),code=temporary(),secrets=temporary(),log=new RequestLog();
 try {
  const org=catalog.createOrganization('alice','A');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets,log);
  const call=(path:string,method='GET')=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,{method,headers:{authorization:'alice'}}));
  for(const part of ['source','download','metrics','logs'])expect((await call(`functions/constructor/${part}`)).status).toBe(404);
  expect((await call('functions/toString','DELETE')).status).toBe(404);
 } finally {catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}
});

test('a path part with a dot, a colon, a plus or an encoded equals sign is hidden too',()=>{
 for(const part of ['abcdefghijkl.mnopqrstuvwxyz','abcdefghij:klmnopqrstuvwxyz1','abcdefghij+klmnopqrstuvwxyz1','secret%3Dabc123','a=b'])
  expect(redactCallPath('/hook/'+part+'/x')).toBe('/hook/[redacted]/x');
});
