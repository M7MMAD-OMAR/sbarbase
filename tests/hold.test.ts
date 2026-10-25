import {test,expect} from 'bun:test';
import {mkdirSync,mkdtempSync,rmSync,unlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PAUSED_CHANGES,holdApplication,holdScope,passesHold,upgradeHold,upgradeHoldScope,windowOpen,WINDOW_MAX_MS} from '../src/gateway/hold';
import {healthHandler} from '../src/http/health';

function directory() {
 const path=mkdtempSync(join(tmpdir(),'sbarbase-hold-'));
 return {path,marker:join(path,'hold'),state:join(path,'state.json'),done(){rmSync(path,{recursive:true,force:true});}};
}

test('the marker holds only while an upgrade waits for confirmation, and its removal applies within the cache',()=>{
 const place=directory();
 try {
  let clock=0;
  const held=upgradeHold(place.marker,place.state,{ttlMs:1000,now:()=>clock});
  expect(held()).toBe(false);
  writeFileSync(place.state,JSON.stringify({phase:'applied'}));
  writeFileSync(place.marker,'{}');
  // The first answer is cached for a second.
  expect(held()).toBe(false);
  clock=1000;
  expect(held()).toBe(true);
  unlinkSync(place.marker);
  clock=1500;
  expect(held()).toBe(true);
  clock=2000;
  expect(held()).toBe(false);
  writeFileSync(place.marker,'{}');
  writeFileSync(place.state,JSON.stringify({phase:'rolling_back'}));
  clock=3000;
  expect(held()).toBe(true);
 } finally {place.done();}
});

test('a stale marker never wedges an installation: a finished or unreadable state fails open',()=>{
 const place=directory();
 try {
  let clock=0;
  const held=upgradeHold(place.marker,place.state,{ttlMs:1000,now:()=>clock});
  writeFileSync(place.marker,'{}');
  for(const [text,expected] of [['',false],['not json',false],[JSON.stringify({phase:'confirmed'}),false],
   [JSON.stringify({phase:'rollback_failed'}),false],[JSON.stringify({phase:'applied'}),true]] as const) {
   if(text)writeFileSync(place.state,text);else rmSync(place.state,{force:true});
   clock+=1000;
   expect(held()).toBe(expected);
  }
 } finally {place.done();}
});

test('held application requests get 503 with Retry-After and CORS; management reads and sign-in pass',async()=>{
 let held=true;const seen:string[]=[];
 const handler=holdApplication(async request=>{seen.push(new URL(request.url).pathname);return new Response('ok');},()=>held);
 const application=await handler(new Request('http://127.0.0.1/e_0123456789abcdef01234567/rest/v1/items',{method:'POST',headers:{origin:'https://app.example'}}));
 expect(application.status).toBe(503);
 expect(application.headers.get('retry-after')).toBe('5');
 expect(application.headers.get('access-control-allow-origin')).toBe('*');
 const preflight=await handler(new Request('http://127.0.0.1/e_0123456789abcdef01234567/rest/v1/items',{method:'OPTIONS',
  headers:{origin:'https://app.example','access-control-request-method':'POST'}}));
 expect(preflight.status).toBe(503);
 expect(preflight.headers.get('access-control-allow-origin')).toBe('*');
 expect((await handler(new Request('http://127.0.0.1/management/v1/organizations'))).status).toBe(200);
 expect((await handler(new Request('http://127.0.0.1/management/auth/v1/token',{method:'POST'}))).status).toBe(200);
 expect(seen).toEqual(['/management/v1/organizations','/management/auth/v1/token']);
 held=false;
 expect((await handler(new Request('http://127.0.0.1/e_0123456789abcdef01234567/rest/v1/items'))).status).toBe(200);
});

test('while held, management changes answer 409 so none is lost on the way back; reads, sign-in and the update routes pass',async()=>{
 let held=true;const seen:string[]=[];
 const handler=holdApplication(async request=>{seen.push(request.method+' '+new URL(request.url).pathname);return new Response('ok');},()=>held);
 const call=(method:string,path:string)=>handler(new Request('http://127.0.0.1'+path,{method}));
 for(const [method,path] of [['POST','/management/v1/organizations'],['PATCH','/management/v1/projects/0b5c1c3e-5f40-4d2e-9d6e-6a4e1f1d2c3b'],
  ['DELETE','/management/v1/environments/0b5c1c3e-5f40-4d2e-9d6e-6a4e1f1d2c3b'],
  ['POST','/management/v1/updates/apply'],['POST','/management/invitations/redeem']] as const) {
  const answer=await call(method,path);
  expect(answer.status).toBe(409);
  expect(answer.headers.get('cache-control')).toBe('no-store');
  expect(answer.headers.get('x-content-type-options')).toBe('nosniff');
  expect((await answer.json() as {message:string}).message).toBe(PAUSED_CHANGES);
 }
 expect(seen).toEqual([]);
 // Roll back, check now and the update settings write only .lab/upgrades, which the snapshot
 // does not restore, so nothing they change is lost on the way back.
 for(const [method,path] of [['GET','/management/v1/organizations'],['GET','/management/v1/updates'],['HEAD','/management/v1/updates'],
  ['OPTIONS','/management/v1/organizations'],['POST','/management/auth/v1/token'],['POST','/management/auth/v1/logout'],
  ['POST','/management/v1/updates/rollback'],['POST','/management/v1/updates/check'],['PUT','/management/v1/updates/settings']] as const)
  expect((await call(method,path)).status).toBe(200);
 expect(seen).toHaveLength(9);
 expect(passesHold('GET','/management/v1/updates/rollback')).toBe(true);
 expect(passesHold('PUT','/management/v1/updates/rollback')).toBe(false);
 expect(passesHold('POST','/management/v1/updates/settings')).toBe(false);
 expect(passesHold('PUT','/management/v1/updates/check')).toBe(false);
 held=false;
 expect((await call('POST','/management/v1/organizations')).status).toBe(200);
 expect(PAUSED_CHANGES).toContain('confirmed');
});

test('/health answers once the catalog reads, and says whether traffic is held',async()=>{
 let broken=false;
 const health=healthHandler({schemaVersion(){if(broken)throw new Error('closed');return 3;}},()=>true);
 const answer=health(new Request('http://127.0.0.1/health'));
 expect(answer.status).toBe(200);
 expect(answer.headers.get('cache-control')).toBe('no-store');
 expect(await answer.json()).toEqual({status:'ok',held:true});
 expect(health(new Request('http://127.0.0.1/health',{method:'POST'})).status).toBe(405);
 broken=true;
 expect(health(new Request('http://127.0.0.1/health')).status).toBe(503);
});

test('the marker names its scope; the old marker, an empty or broken one and any unknown scope hold everything',()=>{
 expect(holdScope(JSON.stringify({scope:'management'}))).toBe('management');
 for(const text of [JSON.stringify({phase:'applied',since:'2026-10-23T10:00:00Z'}),'{}','','not json','null','[]',
  JSON.stringify({scope:'all'}),JSON.stringify({scope:'tenants'}),JSON.stringify({scope:'Management'}),JSON.stringify({scope:42}),
  JSON.stringify({scope:['management']})])
  expect(holdScope(text)).toBe('all');
});

test('the running version follows the scope in the marker, and a rescope applies within the cache',()=>{
 const place=directory();
 try {
  let clock=0;
  const scope=upgradeHoldScope(place.marker,place.state,{ttlMs:1000,now:()=>clock});
  const held=upgradeHold(place.marker,place.state,{ttlMs:1000,now:()=>clock});
  writeFileSync(place.state,JSON.stringify({phase:'applied'}));
  // The marker lab/upgrade.py writes today, without a scope: the whole hold.
  writeFileSync(place.marker,JSON.stringify({phase:'applied',since:'2026-10-23T10:00:00Z'}));
  expect([scope(),held()]).toEqual(['all',true]);
  writeFileSync(place.marker,JSON.stringify({phase:'applied',since:'2026-10-23T10:00:00Z',scope:'management'}));
  clock=500;
  expect(scope()).toBe('all');
  clock=1000;
  expect([scope(),held()]).toEqual(['management',true]);
  writeFileSync(place.marker,JSON.stringify({scope:'something newer'}));
  clock=2000;
  expect(scope()).toBe('all');
  // A finished state ignores the marker whatever its scope.
  writeFileSync(place.marker,JSON.stringify({scope:'management'}));
  writeFileSync(place.state,JSON.stringify({phase:'confirmed'}));
  clock=3000;
  expect([scope(),held()]).toEqual([undefined,false]);
  // A marker that exists but cannot be read as a file holds everything.
  unlinkSync(place.marker);mkdirSync(place.marker);
  writeFileSync(place.state,JSON.stringify({phase:'applied'}));
  clock=4000;
  expect(scope()).toBe('all');
 } finally {place.done();}
});

test('a management hold lets only the tenant data plane through; /v1, /mcp and the console stay held',async()=>{
 let scope:'all'|'management'|undefined='management';const seen:string[]=[];
 const handler=holdApplication(async request=>{seen.push(request.method+' '+new URL(request.url).pathname);return new Response('ok');},()=>scope);
 const call=(method:string,path:string)=>handler(new Request('http://127.0.0.1'+path,{method}));
 expect((await call('POST','/e_0123456789abcdef01234567/rest/v1/items')).status).toBe(200);
 expect((await call('GET','/e_0123456789abcdef01234567/functions/v1/hello')).status).toBe(200);
 const change=await call('POST','/management/v1/organizations');
 expect(change.status).toBe(409);
 expect((await change.json() as {message:string}).message).toBe(PAUSED_CHANGES);
 expect((await call('GET','/management/v1/organizations')).status).toBe(200);
 expect((await call('POST','/management/v1/updates/rollback')).status).toBe(200);
 for(const service of ['auth','storage','realtime'])
  expect((await call('POST',`/e_0123456789abcdef01234567/${service}/v1/x`)).status).toBe(200);
 // The Supabase-compatible API and the hosted MCP endpoint write control state: held, reads too.
 for(const [method,path] of [['POST','/v1/projects'],['PATCH','/v1/projects/abc/api-keys/1'],['GET','/v1/projects'],
  ['POST','/mcp'],['POST','/v1/rest/v1/items'],['POST','/mcp/rest/v1/items'],['POST','/target/rest/v1/items'],
  ['POST','/e_0123456789abcdef01234567/graphql/v1'],['POST','/e_0123456789abcdef01234567/pg/v1/query'],
  ['POST','/e_0123456789abcdef01234567/meta/v1/tables'],['POST','/e_0123456789abcdef01234567/admin/v1/x'],['POST','/e_0123456789abcdef01234567/rest/v2/items'],
  // A runtime route only counts at the start of the path.
  ['POST','/mcp/e_0123456789abcdef01234567/rest/v1/x'],['POST','/v1/projects/e_0123456789abcdef01234567/rest/v1'],
  ['POST','/v1/e_0123456789abcdef01234567/rest/v1/x'],['POST','//e_0123456789abcdef01234567/rest/v1/x']] as const)
  expect((await call(method,path)).status).toBe(503);
 expect((await call('POST','/management/v1/e_0123456789abcdef01234567/rest/v1/x')).status).toBe(409);
 expect(seen).toEqual(['POST /e_0123456789abcdef01234567/rest/v1/items','GET /e_0123456789abcdef01234567/functions/v1/hello',
  'GET /management/v1/organizations','POST /management/v1/updates/rollback','POST /e_0123456789abcdef01234567/auth/v1/x',
  'POST /e_0123456789abcdef01234567/storage/v1/x','POST /e_0123456789abcdef01234567/realtime/v1/x']);
 scope='all';
 expect((await call('POST','/e_0123456789abcdef01234567/rest/v1/items')).status).toBe(503);
 scope=undefined;
 expect((await call('POST','/management/v1/organizations')).status).toBe(200);
});

test('the cache runs on a monotonic clock: a wall clock stepped back does not keep a cached answer',async()=>{
 const place=directory();const wall=Date.now;
 try {
  // The wall clock is stubbed from the start, so a cache that read it would read the stub.
  let clock=wall();Date.now=()=>clock;
  const scope=upgradeHoldScope(place.marker,place.state,{ttlMs:50});
  expect(scope()).toBeUndefined();
  writeFileSync(place.state,JSON.stringify({phase:'applied'}));
  writeFileSync(place.marker,'{}');
  // The wall clock steps back a day; the cached "no hold" must still expire after 50 ms.
  clock-=86_400_000;
  await Bun.sleep(80);
  expect(scope()).toBe('all');
 } finally {Date.now=wall;place.done();}
});

test('an open database window holds everything with no upgrade pending, ends by itself, and a bad marker holds nothing',()=>{
 const place=directory();const window=join(place.path,'database-window');
 try {
  let wall=1_000_000,clock=0;
  const scope=()=>upgradeHoldScope(place.marker,place.state,{ttlMs:0,now:()=>clock++,window,wall:()=>wall})();
  expect(scope()).toBeUndefined();
  writeFileSync(window,JSON.stringify({until:wall+5000}));
  expect(scope()).toBe('all');
  // The end is in the marker: a tool that died leaves a marker that stops holding.
  wall+=5000;
  expect(scope()).toBeUndefined();
  for(const text of ['','not json','null','{}',JSON.stringify({until:'soon'}),JSON.stringify({until:wall+WINDOW_MAX_MS+1}),JSON.stringify({until:wall-1})]) {
   writeFileSync(window,text);
   expect(windowOpen(window,()=>wall)).toBe(false);
  }
  writeFileSync(window,JSON.stringify({until:wall+WINDOW_MAX_MS}));
  expect(windowOpen(window,()=>wall)).toBe(true);
  unlinkSync(window);
  expect(scope()).toBeUndefined();
 } finally {place.done();}
});
