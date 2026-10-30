import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {changeSecrets,deploy,deployment,functionsHandler} from '../src/control/functions';
import {maskValues,usageThrough,usageView,type UsageAnswer} from '../src/control/functions-worker';
import {takeBatch} from '../lab/functions/event-worker/batch';
import {Usage,functionOf,type UsageEvent} from '../lab/functions/main/usage';

const minute=60_000,T=Date.UTC(2026,9,30,12,0,30);
const at=(n:number)=>new Date(T+n*minute).toISOString();
const path=(name:string)=>`/home/deno/functions/v1abc-0123abcd/${name}`;
const shutdown=(name:string,cpu:number,total:number,heap:number,external:number,when=0):UsageEvent=>({timestamp:at(when),event_type:'Shutdown',event:{reason:'EarlyDrop',cpu_time_used:cpu,memory_used:{total,heap,external}},metadata:{service_path:path(name)}});
const boot=(name:string,ms:number,when=0):UsageEvent=>({timestamp:at(when),event_type:'Boot',event:{boot_time:ms},metadata:{service_path:path(name)}});
const log=(name:string,level:string,msg:string,when=0):UsageEvent=>({timestamp:at(when),event_type:'Log',event:{msg,level},metadata:{service_path:path(name)}});

test('a function is named from the worker\'s service path and nothing else is',()=>{
 expect(functionOf('/home/deno/functions/v1abc-0123abcd/hello')).toBe('hello');
 expect(functionOf('/home/deno/functions/v1abc-0123abcd/hello/')).toBe('hello');
 expect(functionOf('/home/deno/functions/v1/my_fn-2')).toBe('my_fn-2');
 for(const bad of [undefined,null,42,'','/home/deno/main','/home/deno/functions/v1','/home/deno/functions/v1/_shared','/home/deno/functions/v1/'+'a'.repeat(65),'/home/deno/functions/v1/../x'])expect(functionOf(bad)).toBeNull();
});

test('shutdown, boot and log events add up per function and per minute, and others are ignored',()=>{
 const usage=new Usage(()=>T);
 usage.add([boot('hello',25),boot('hello',45),shutdown('hello',10,10_000_000,7_000_000,3_000_000),shutdown('hello',30,12_000_000,8_000_000,4_000_000),
  log('hello','Info','hi'),log('hello','Error','boom'),{timestamp:at(0),event_type:'UncaughtException',event:{msg:'late failure'},metadata:{service_path:path('hello')}},
  shutdown('other',5,1_000_000,500_000,500_000),
  {event_type:'Mystery',metadata:{service_path:path('hello')}},{event_type:'Log',event:{msg:'no function'},metadata:{service_path:'/home/deno/main'}},{} as UsageEvent,null as unknown as UsageEvent]);
 const hello=usage.of('hello')!;
 expect(hello.buckets).toHaveLength(1);
 expect(hello.buckets[0]).toMatchObject({boots:2,bootMs:70,workers:2,cpuSum:40,cpuMax:30,memSum:22_000_000,memMax:12_000_000,heapSum:15_000_000,externalSum:7_000_000,errors:2});
 expect(hello.lines.map(line=>[line.level,line.msg])).toEqual([['Info','hi'],['Error','boom'],['Error','late failure']]);
 expect(usage.of('other')!.buckets[0]).toMatchObject({workers:1,cpuSum:5});
 expect(usage.of('missing')).toBeNull();
});

test('events land in the minute they came from; a wrong clock is replaced by now; a day is kept and no more',()=>{
 let now=T;
 const usage=new Usage(()=>now);
 usage.add([shutdown('hello',1,1,1,1,-3),shutdown('hello',1,1,1,1,0),shutdown('hello',1,1,1,1,-5000),{...shutdown('hello',1,1,1,1,0),timestamp:'not a time'},{...shutdown('hello',1,1,1,1),timestamp:at(500)}]);
 const minutes=usage.of('hello')!.buckets.map(row=>row.minute-Math.floor(T/minute)).sort((a,b)=>a-b);
 expect(minutes).toEqual([-3,0]);
 expect(usage.of('hello')!.buckets.find(row=>row.minute===Math.floor(T/minute))!.workers).toBe(4);
 for(let index=0;index<1700;index++){now+=minute;usage.add([shutdown('hello',1,1,1,1,index+1)]);}
 expect(usage.of('hello')!.buckets.length).toBeLessThanOrEqual(1440);
});

test('what an environment keeps is bounded: 100 functions, 200 lines of 1000 characters, a day of minutes',()=>{
 let now=T;
 const usage=new Usage(()=>now);
 for(let index=0;index<150;index++)usage.add([shutdown(`f${index}`,1,1,1,1)]);
 // The 101st name makes room by taking the place of the one heard from longest ago.
 expect(usage.of('f99')).not.toBeNull();expect(usage.of('f100')).not.toBeNull();expect(usage.of('f0')).toBeNull();
 for(let index=0;index<500;index++)usage.add([log('f0','Info',`line ${index} `+'x'.repeat(3000))]);
 const lines=usage.of('f0')!.lines;
 expect(lines).toHaveLength(200);expect(lines[0]!.msg.startsWith('line 300 ')).toBe(true);expect(lines.every(line=>line.msg.length<=1000)).toBe(true);
 const many=new Usage(()=>now);
 for(let index=0;index<100;index++)for(let m=0;m<1440;m+=1){now=T+m*minute;if(index<2||m%120===0)many.add([shutdown(`g${index}`,1,1,1,1)]);}
 expect(many.bucketCount()).toBeLessThanOrEqual(100*1440);
});

test('the numbers of a full day for 100 functions stay under 60 MiB of heap',()=>{
 Bun.gc(true);const before=process.memoryUsage().heapUsed;
 let now=T;const usage=new Usage(()=>now);
 for(let m=0;m<1440;m++){now=T+m*minute;usage.add(Array.from({length:100},(_,index)=>shutdown(`h${index}`,3,10_000_000,7_000_000,3_000_000,m)));}
 expect(usage.bucketCount()).toBe(144_000);
 Bun.gc(true);
 expect(process.memoryUsage().heapUsed-before).toBeLessThan(60*1024*1024);
});

test('the control plane asks the environment\'s main service for one function and never throws',async()=>{
 const seen:{url:string;authorization:string}[]=[];
 const body={known:true,since:T,buckets:[{minute:1,boots:0,bootMs:0,workers:1,cpuSum:3,cpuMax:3,memSum:5,memMax:5,heapSum:4,externalSum:1,errors:0},{minute:'x'},null,{minute:2}],lines:[{at:1,level:'Info',msg:'a'},{at:'x'},null]};
 const ok=(async(url:URL,init:RequestInit)=>{seen.push({url:String(url),authorization:String((init.headers as Record<string,string>).authorization)});return Response.json(body);}) as unknown as typeof fetch;
 const source=usageThrough(runtime=>runtime==='e_a'?{url:'http://10.0.0.7:9000',token:'service.token'}:undefined,ok);
 const answer=await source('e_a','hello') as Extract<UsageAnswer,{state:'ok'}>;
 expect(seen).toEqual([{url:'http://10.0.0.7:9000/_sb_stats/hello',authorization:'Bearer service.token'}]);
 expect(answer.state).toBe('ok');expect(answer.known).toBe(true);expect(answer.buckets).toHaveLength(1);expect(answer.lines).toEqual([{at:1,level:'Info',msg:'a'}]);
 expect(await source('e_b','hello')).toEqual({state:'unavailable'});
 expect(await usageThrough(()=>'maintenance',ok)('e_a','hello')).toEqual({state:'unavailable'});
 expect(await usageThrough(()=>({url:'http://x',token:'t'}),(async()=>new Response('no',{status:401})) as unknown as typeof fetch)('e_a','hello')).toEqual({state:'unavailable'});
 expect(await usageThrough(()=>({url:'http://x',token:'t'}),(async()=>{throw new Error('refused');}) as unknown as typeof fetch)('e_a','hello')).toEqual({state:'unavailable'});
 expect(await usageThrough(()=>({url:'http://x',token:'t'}),(async()=>new Response('not json',{status:200})) as unknown as typeof fetch)('e_a','hello')).toEqual({state:'unavailable'});
 expect(await usageThrough(()=>{throw new Error('placement');},ok)('e_a','hello')).toEqual({state:'unavailable'});
 // The function name is one path part, whatever it holds.
 await source('e_a','a/b');expect(seen.at(-1)!.url).toBe('http://10.0.0.7:9000/_sb_stats/a%2Fb');
});

test('a range becomes chart points and totals: averages per worker, heap and external as a share of memory',()=>{
 const now=Date.UTC(2026,9,30,12,10,0),current=Math.floor(now/minute);
 const row=(back:number,over:Partial<ReturnType<typeof Object>>={})=>({minute:current-back,boots:1,bootMs:40,workers:2,cpuSum:20,cpuMax:15,memSum:20_000,memMax:12_000,heapSum:10_000,externalSum:4_000,errors:1,...over});
 const view=usageView([row(0),row(3),row(40),row(2000)],'15m',now-3_600_000,now);
 expect(view.points).toHaveLength(15);
 expect(view.totals).toEqual({workers:4,boots:2,averageBootMs:40,averageCpuMs:10,maxCpuMs:15,averageMemory:10_000,maxMemory:12_000,heapPercent:50,externalPercent:20,errors:2});
 expect(view.points.at(-1)).toMatchObject({workers:2,averageCpuMs:10,maxCpuMs:15,averageMemory:10_000});
 expect(view.points[0]).toMatchObject({workers:0,averageCpuMs:null,maxCpuMs:null});
 expect(usageView([row(0),row(3),row(40)],'1h',now,now).totals.workers).toBe(6);
 expect(usageView([],'1d',now,now).points).toHaveLength(96);
 expect(usageView([],'1d',now,now).totals).toMatchObject({workers:0,averageCpuMs:null,heapPercent:null,errors:0});
});

async function setup(usage:Parameters<typeof functionsHandler>[6]){
 const catalog=new Catalog(':memory:'),code=mkdtempSync(join(tmpdir(),'functions-worker-')),secrets=mkdtempSync(join(tmpdir(),'functions-worker-'));
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const other=catalog.createOrganization('bob','B');
 const second=catalog.createEnvironment('bob',catalog.createProject('bob',other,'Q'),'production');
 for(const id of [environment,second]){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment??id,job.claim!,true);}
 const handler=functionsHandler(catalog,async request=>request.headers.get('authorization'),code,secrets,undefined,undefined,usage);
 const call=(actor:string,env:string,path:string,method='GET')=>handler(new Request(`http://local/management/v1/environments/${env}/${path}`,{method,headers:{authorization:actor,'content-type':'application/json'}}));
 const runtime=(await (await call('alice',environment,'functions')).json() as any).data.runtime as string;
 deploy(code,runtime,'hello',deployment({files:{'index.ts':'x'}}));
 const turnOn=()=>(catalog as any).db.query("INSERT INTO functions_settings(runtime,desired,state,failure,actor,updated_at) VALUES (?,'on','on',NULL,'alice',1) ON CONFLICT(runtime) DO UPDATE SET state='on',desired='on'").run(runtime);
 return {catalog,call,environment,second,runtime,turnOn,secrets,done:()=>{catalog.close();rmSync(code,{recursive:true,force:true});rmSync(secrets,{recursive:true,force:true});}};
}

test('usage and output are for owners and admins of the environment, and say so when there is nothing to show',async()=>{
 let ask:string[]=[];
 const source:Parameters<typeof functionsHandler>[6]=async(runtime,name)=>{ask=[runtime,name];return {state:'unavailable'};};
 const t=await setup(source);
 try {
  for(const part of ['usage','output']){
   expect((await t.call('carol',t.environment,`functions/hello/${part}`)).status).toBe(403);
   expect((await t.call('bob',t.environment,`functions/hello/${part}`)).status).toBe(403);
   expect((await t.call('alice',t.second,`functions/hello/${part}`)).status).toBe(403);
   expect((await t.call('alice',t.environment,`functions/missing/${part}`)).status).toBe(404);
   expect((await t.call('alice',t.environment,`functions/constructor/${part}`)).status).toBe(404);
   for(const bad of ['?range=9y','?range=constructor','?range=__proto__','?limit=0','?limit=201'])expect((await t.call('alice',t.environment,`functions/hello/${part}${bad}`)).status).toBe(400);
   // Off: the runtime is not asked at all.
   const off=await (await t.call('alice',t.environment,`functions/hello/${part}`)).json() as any;
   expect(off.data).toEqual({available:false,reason:'off'});expect(ask).toEqual([]);
  }
  t.turnOn();
  const down=await (await t.call('alice',t.environment,'functions/hello/usage')).json() as any;
  expect(down.data).toEqual({available:false,reason:'unreachable'});expect(ask).toEqual([t.runtime,'hello']);
  expect((await t.call('alice',t.environment,'functions/hello/usage','POST')).status).toBe(405);
 } finally {t.done();}
});

test('usage and output come from the runtime, with what the function printed redacted before it leaves the server',async()=>{
 const now=Date.now();
 const source:Parameters<typeof functionsHandler>[6]=async()=>({state:'ok',known:true,since:now-1000,
  buckets:[{minute:Math.floor(now/minute),boots:1,bootMs:30,workers:2,cpuSum:20,cpuMax:15,memSum:2000,memMax:1500,heapSum:1000,externalSum:500,errors:1}],
  lines:[{at:now-90*minute,level:'Error',msg:'old error'},{at:now-5000,level:'Info',msg:'user signed in'},{at:now-4000,level:'Error',msg:'Authorization: Bearer eyJabcdefg.hijklmnop1234.qrstuvwxyz5678 failed'},{at:now-3000,level:'Info',msg:'postgres://app:hunter2secret@db:5432/x connected'}]});
 const t=await setup(source);
 try {
  t.turnOn();
  const usage=await (await t.call('alice',t.environment,'functions/hello/usage?range=15m')).json() as any;
  expect(usage.data).toMatchObject({available:true,known:true,range:'15m',totals:{workers:2,averageCpuMs:10,maxCpuMs:15,heapPercent:50,externalPercent:25,errors:1}});
  const output=await (await t.call('alice',t.environment,'functions/hello/output?range=1h')).text();
  expect(output).not.toContain('eyJabcdefg');expect(output).not.toContain('hunter2secret');expect(output).not.toContain('old error');
  const lines=(JSON.parse(output) as any).data.lines as {level:string;msg:string}[];
  expect(lines.map(line=>line.msg)).toEqual(['postgres://app:[redacted]@db:5432/x connected','Authorization: Bearer [redacted] failed','user signed in']);
  const errors=await (await t.call('alice',t.environment,'functions/hello/output?errors=1&range=1d')).json() as any;
  expect(errors.data.lines.map((line:{msg:string})=>line.msg)).toEqual(['Authorization: Bearer [redacted] failed','old error']);
  // The search runs on the text the person sees: a redacted secret cannot be found by it.
  const search=async(q:string)=>((await (await t.call('alice',t.environment,`functions/hello/output?q=${encodeURIComponent(q)}`)).json()) as any).data.lines.length;
  expect(await search('hunter2')).toBe(0);expect(await search('qrstuvwxyz')).toBe(0);expect(await search('signed in')).toBe(1);expect(await search('redacted')).toBe(2);
 } finally {t.done();}
});

test('the event worker and the main service agree on the token and the routes, and keep no secret in the source',()=>{
 const worker=readFileSync('lab/functions/event-worker/index.ts','utf8'),main=readFileSync('lab/functions/main/index.ts','utf8');
 expect(worker).toContain("role: 'sb_events'");expect(worker).toContain("http://127.0.0.1:9000/_sb_events");expect(worker).toContain("Deno.env.get('SB_JWT_SECRET')");
 expect(main).toContain("claims.role !== 'sb_events'");expect(main).toContain("url.pathname === '/_sb_events'");expect(main).toContain('/^\\/_sb_stats\\/([^/]+)$/');
 // A function is never given the secret: the main service passes workers only the variables it lists.
 expect(main).not.toMatch(/\['SB_JWT_SECRET'/);expect(main).toContain("['SUPABASE_URL', SELF + '/_sb']");
 expect(worker).not.toMatch(/console\.(log|error)\(.*SECRET/);
});

test('an event goes into the minute it came from, even when its batch arrives late, and never into the future',()=>{
 const now=T;
 const usage=new Usage(()=>now);
 usage.add([shutdown('hello',1,1,1,1,0)]);
 usage.add([shutdown('hello',2,1,1,1,-5)]);
 usage.add([shutdown('hello',4,1,1,1,-2),shutdown('hello',8,1,1,1,-5)]);
 const rows=usage.of('hello')!.buckets;
 expect(rows.map(row=>row.minute-Math.floor(T/minute))).toEqual([-5,-2,0]);
 expect(rows.map(row=>row.cpuSum)).toEqual([10,4,1]);
 // A time ahead of this clock is now, so it cannot open a bucket that the totals leave out.
 usage.add([shutdown('hello',16,1,1,1,1)]);
 expect(usage.of('hello')!.buckets.map(row=>row.minute-Math.floor(T/minute))).toEqual([-5,-2,0]);
 expect(usage.of('hello')!.buckets[2]!.cpuSum).toBe(17);
});

test('a batch is cut by size as well as by count, so quotes and Arabic text cannot make one the main service refuses',()=>{
 const event=(text:string)=>({event_type:'Log',event:{msg:text,level:'Info'},metadata:{service_path:'/home/deno/functions/v1/x'}});
 const queue:unknown[]=Array.from({length:200},()=>event('"'.repeat(1000)));
 const first=takeBatch(queue,200,256*1024);
 expect(first.length).toBeLessThan(200);expect(first.length).toBeGreaterThan(0);
 expect(JSON.stringify(first).length).toBeLessThanOrEqual(256*1024);
 expect(queue.length+first.length).toBe(200);
 const count=takeBatch(Array.from({length:500},()=>event('x')),200,256*1024);expect(count).toHaveLength(200);
 // An event bigger than the limit still goes, alone, so the queue always moves.
 const huge:unknown[]=[event('y'.repeat(300_000)),event('z')];
 expect(takeBatch(huge,200,256*1024)).toHaveLength(1);expect(huge).toHaveLength(1);
 expect(takeBatch([],200,10)).toEqual([]);
});

test('an environment\'s own secret values are hidden in what a function printed, longest first, and short values are left alone',()=>{
 expect(maskValues('key sk_live_ABCDEF12 used',['sk_live_ABCDEF12'])).toBe('key [redacted] used');
 expect(maskValues('a abcdefgh b abcdefghIJKL c',['abcdefgh','abcdefghIJKL'])).toBe('a [redacted] b [redacted] c');
 expect(maskValues('port 5432 and abc',['5432','abc',''])).toBe('port 5432 and abc');
 expect(maskValues('x',[])).toBe('x');
});

test('the output route hides the environment\'s secret values before a search can see them',async()=>{
 const now=Date.now();
 const source:Parameters<typeof functionsHandler>[6]=async()=>({state:'ok',known:true,since:now,buckets:[],lines:[{at:now-1000,level:'Info',msg:'called stripe with sk_live_ABCDEF12345 ok'}]});
 const t=await setup(source);
 try {
  t.turnOn();
  changeSecrets(t.secrets,t.runtime,{secrets:{STRIPE_KEY:'sk_live_ABCDEF12345'}});
  const text=await (await t.call('alice',t.environment,'functions/hello/output')).text();
  expect(text).not.toContain('sk_live_ABCDEF12345');expect(text).toContain('called stripe with [redacted] ok');
  const search=async(q:string)=>((await (await t.call('alice',t.environment,`functions/hello/output?q=${encodeURIComponent(q)}`)).json()) as any).data.lines.length;
  expect(await search('ABCDEF')).toBe(0);expect(await search('called stripe')).toBe(1);
 } finally {t.done();}
});

test('the event worker signs with a key derived from the secret, the main service checks that key, and the worker drops a batch the service refuses',()=>{
 const worker=readFileSync('lab/functions/event-worker/index.ts','utf8'),main=readFileSync('lab/functions/main/index.ts','utf8');
 expect(worker).toContain("'sbarbase event worker v1'");expect(main).toContain("'sbarbase event worker v1'");
 expect(worker).toContain('await derived()');expect(main).toContain('await eventSecret()');
 expect(worker).not.toMatch(/importKey\('raw', new TextEncoder\(\)\.encode\(JWT_SECRET\)[^;]*\['sign'\]\);\s*return `\$\{head\}/);
 expect(worker).toContain('response.status >= 500 || response.status === 429');
 expect(worker).toContain('takeBatch(queue, BATCH, BATCH_BYTES)');
});
