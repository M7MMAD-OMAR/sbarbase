import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,unlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PAUSED_CHANGES,holdApplication,passesHold,upgradeHold} from '../src/gateway/hold';
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

test('while held, management changes answer 409 so none is lost on the way back; reads, sign-in and roll back pass',async()=>{
 let held=true;const seen:string[]=[];
 const handler=holdApplication(async request=>{seen.push(request.method+' '+new URL(request.url).pathname);return new Response('ok');},()=>held);
 const call=(method:string,path:string)=>handler(new Request('http://127.0.0.1'+path,{method}));
 for(const [method,path] of [['POST','/management/v1/organizations'],['PATCH','/management/v1/projects/0b5c1c3e-5f40-4d2e-9d6e-6a4e1f1d2c3b'],
  ['DELETE','/management/v1/environments/0b5c1c3e-5f40-4d2e-9d6e-6a4e1f1d2c3b'],['PUT','/management/v1/updates/settings'],
  ['POST','/management/v1/updates/apply'],['POST','/management/v1/updates/check'],['POST','/management/invitations/redeem']] as const) {
  const answer=await call(method,path);
  expect(answer.status).toBe(409);
  expect(answer.headers.get('cache-control')).toBe('no-store');
  expect((await answer.json() as {message:string}).message).toBe(PAUSED_CHANGES);
 }
 expect(seen).toEqual([]);
 for(const [method,path] of [['GET','/management/v1/organizations'],['GET','/management/v1/updates'],['HEAD','/management/v1/updates'],
  ['OPTIONS','/management/v1/organizations'],['POST','/management/auth/v1/token'],['POST','/management/auth/v1/logout'],
  ['POST','/management/v1/updates/rollback']] as const)expect((await call(method,path)).status).toBe(200);
 expect(seen).toHaveLength(7);
 expect(passesHold('GET','/management/v1/updates/rollback')).toBe(true);
 expect(passesHold('PUT','/management/v1/updates/rollback')).toBe(false);
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
