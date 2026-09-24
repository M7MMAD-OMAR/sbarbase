import {test,expect} from 'bun:test';
import {RequestLog,observedGateway} from '../src/gateway/observe';
import {observeHandler,redact,serviceLines,parseStats,containerName,type ContainerReader} from '../src/control/observe';
import {Catalog} from '../src/control/catalog';

const E='e_'+'a'.repeat(24);

test('the request log keeps the newest requests and per-minute totals for an hour',()=>{
 let now=Date.UTC(2026,8,24,10,0,0);
 const log=new RequestLog(()=>now);
 log.record(E,{method:'GET',service:'rest',path:'/todos',status:200,ms:10});
 log.record(E,{method:'POST',service:'auth',path:'/token',status:400,ms:30});
 now+=60_000;
 log.record(E,{method:'GET',service:'rest',path:'/todos',status:503,ms:50});
 const metrics=log.metrics(E);
 expect(metrics.window).toMatchObject({requests:3,clientErrors:1,serverErrors:1,p50:30,p95:50,services:{rest:2,auth:1}});
 expect(metrics.perMinute).toHaveLength(60);
 expect(metrics.perMinute.at(-1)).toMatchObject({requests:1,serverErrors:1,averageMs:50});
 expect(metrics.perMinute.at(-2)).toMatchObject({requests:2,clientErrors:1,averageMs:20});
 expect(log.recent(E).map(row=>row.status)).toEqual([503,400,200]);
 expect(log.recent(E,{errors:true}).map(row=>row.status)).toEqual([503,400]);
 expect(log.recent(E,{service:'auth'})).toHaveLength(1);
 // An hour later the totals are gone; the last requests stay until newer ones push them out.
 now+=61*60_000;
 expect(log.metrics(E).window.requests).toBe(0);
 expect(log.recent(E)).toHaveLength(3);
 for(let i=0;i<600;i++)log.record(E,{method:'GET',service:'rest',path:'/x',status:200,ms:1});
 expect(log.recent(E,{limit:1000})).toHaveLength(500);
 expect(log.metrics('e_'+'b'.repeat(24)).window.requests).toBe(0);
});

test('the gateway counts answers for known environments only, without the query string',async()=>{
 const log=new RequestLog();
 const gateway=observedGateway(async request=>new Response(null,{status:new URL(request.url).pathname.includes('missing')?404:200}),
  log,runtime=>runtime===E);
 await gateway(new Request(`http://local/${E}/rest/v1/todos?select=*&apikey=sb_publishable_secret`));
 await gateway(new Request(`http://local/${E}/rest/v1/missing`));
 await gateway(new Request(`http://local/${E}/rest/v1/todos`,{method:'OPTIONS'}));
 await gateway(new Request(`http://local/e_${'c'.repeat(24)}/rest/v1/todos`));
 await gateway(new Request('http://local/anything'));
 const rows=log.recent(E);
 expect(rows.map(row=>[row.method,row.service,row.path,row.status])).toEqual([['GET','rest','/missing',404],['GET','rest','/todos',200]]);
 expect(JSON.stringify(rows)).not.toContain('sb_publishable');
 expect(log.recent('e_'+'c'.repeat(24))).toHaveLength(0);
});

test('secrets never leave the server in a log line',()=>{
 const jwt='eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.c2lnbmF0dXJlMTIz';
 const line=redact(`GET /verify?token=abc123&type=signup&redirect_to=x "refresh_token":"r1" Authorization: Bearer ${jwt} `+
  `apikey=sb_publishable_abc_DEF postgres://user:hunter2@db:5432/x /callback?code=oauth-code&state=s1 "password":"p" "code":"PGRST116"`);
 for(const secret of ['abc123','r1',jwt,'sb_publishable_abc','hunter2','oauth-code','s1','"p"'])expect(line).not.toContain(secret);
 expect(line).toContain('type=signup');
 expect(line).toContain('"code":"PGRST116"');
 expect(line).toContain('postgres://user:[redacted]@db');
});

test('shared Storage lines are kept only when they name this environment',()=>{
 const text=[`{"tenantId":"${E}","msg":"ok"}`,`{"tenantId":"e_${'b'.repeat(24)}","msg":"other"}`,
  `{"host":"${E}.storage.internal","level":50,"msg":"failed"}`,''].join('\n');
 expect(serviceLines(text,'storage',E,{lines:10})).toHaveLength(2);
 expect(serviceLines(text,'storage',E,{lines:10,errors:true})).toEqual([`{"host":"${E}.storage.internal","level":50,"msg":"failed"}`]);
 expect(serviceLines('a\nb\nc','auth',E,{lines:2})).toEqual(['b','c']);
 expect(containerName(E,'storage')).toBe('sbarbase-durable-storage');
 expect(containerName(E,'auth')).toBe(`sbarbase-durable-${E}-auth`);
 expect(()=>containerName('../x','auth')).toThrow();
});

test('docker stats lines become memory and processor use per service',()=>{
 const rows=parseStats([
  JSON.stringify({Name:`sbarbase-durable-${E}-auth`,CPUPerc:'0.52%',MemUsage:'12.5MiB / 128MiB'}),
  JSON.stringify({Name:'someone-else',CPUPerc:'90%',MemUsage:'1GiB / 2GiB'}),'not json'].join('\n'),
  {[`sbarbase-durable-${E}-auth`]:'auth'});
 expect(rows).toEqual([{service:'auth',cpuPercent:0.52,memoryBytes:Math.round(12.5*1024**2),memoryLimitBytes:128*1024**2}]);
});

test('members read metrics; only owners and admins read logs',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const log=new RequestLog();
  const asked:{container:string;lines:number}[]=[];
  const reader:ContainerReader={
   async logs(container,lines){asked.push({container,lines});return `line one\nGET /verify?token=abc123\n`;},
   async stats(){return [{service:'auth',cpuPercent:1,memoryBytes:10,memoryLimitBytes:100}];}};
  const handler=observeHandler(catalog,async request=>request.headers.get('authorization'),log,reader);
  const call=(actor:string,path:string,method='GET')=>handler(new Request(`http://local/management/v1/environments/${environment}/${path}`,
   {method,headers:{authorization:actor}}));
  expect((await call('alice','metrics')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  const runtime=catalog.getProvision('alice',environment).runtime!;
  log.record(runtime,{method:'GET',service:'rest',path:'/todos',status:200,ms:5});
  const metrics=await call('carol','metrics');
  expect(metrics.status).toBe(200);
  expect(((await metrics.json()) as any).data).toMatchObject({window:{requests:1},services:[{service:'auth'}]});
  expect((await call('carol','logs')).status).toBe(403);
  expect((await call('mallory','metrics')).status).toBe(403);
  expect((await call('alice','metrics','POST')).status).toBe(405);
  const requests=((await (await call('alice','logs?source=requests')).json()) as any).data.requests;
  expect(requests).toHaveLength(1);
  const auth=((await (await call('alice','logs?source=auth&lines=50')).json()) as any).data.lines;
  expect(auth).toEqual(['line one','GET /verify?token=[redacted]']);
  expect(asked.at(-1)).toEqual({container:`sbarbase-durable-${runtime}-auth`,lines:50});
  expect((await call('alice','logs?source=db')).status).toBe(400);
  expect((await call('alice','logs?source=auth&lines=0')).status).toBe(400);
  expect((await call('alice','logs?source=auth&lines=5000')).status).toBe(400);
 } finally {catalog.close();}
});
