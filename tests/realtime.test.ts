import {test,expect} from 'bun:test';
import {realtimeUpgrade,isRealtimeSocket} from '../src/gateway/realtime';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import {serveLocal} from '../src/http/local-server';
import {Catalog} from '../src/control/catalog';
import {realtimeHandler} from '../src/control/realtime';

const E='e_'+'a'.repeat(24);
const route:EnvironmentRoute={auth:'http://auth:9999',rest:'http://rest:3000',keys:[],anonymousToken:'anon-token',enabled:true,
 realtime:{url:'http://10.0.0.9:4000',tenantHost:'a'.repeat(24)+'.realtime'},realtimeToken:'anon.jwt.token'};

function decider(overrides:{route?:EnvironmentRoute|undefined|'maintenance';key?:string}={}) {
 return realtimeUpgrade({route:runtime=>runtime===E?('route' in overrides?overrides.route:route):undefined,
  verifyKey:(runtime,key)=>runtime===E&&key===(overrides.key??'sb_publishable_ok')});
}
const headers=(extra:Record<string,string>={})=>new Headers({upgrade:'websocket',connection:'Upgrade','sec-websocket-key':'abc==',
 'sec-websocket-version':'13',host:'127.0.0.1:8790',cookie:'secret=1',authorization:'Bearer x',...extra});

test('only an environment Realtime socket path is taken as one',()=>{
 expect(isRealtimeSocket(`/${E}/realtime/v1/websocket?apikey=k&vsn=1.0.0`)).toBe(true);
 expect(isRealtimeSocket(`/${E}/rest/v1/websocket`)).toBe(false);
 expect(isRealtimeSocket(`/${E}/realtime/v1/websocket/extra`)).toBe(false);
});

test('a socket reaches only its own Realtime, with the anon token in place of the publishable key',()=>{
 const decision=decider()(`/${E}/realtime/v1/websocket?apikey=sb_publishable_ok&vsn=1.0.0`,headers());
 expect(decision.ok).toBe(true);
 if(!decision.ok)return;
 expect([decision.host,decision.port]).toEqual(['10.0.0.9',4000]);
 const lines=decision.head.split('\r\n');
 expect(lines[0]).toBe('GET /socket/websocket?apikey=anon.jwt.token&vsn=1.0.0 HTTP/1.1');
 expect(lines).toContain('Host: '+'a'.repeat(24)+'.realtime');
 expect(lines).toContain('sec-websocket-key: abc==');
 // Cookies, credentials and the client's own Host never reach Realtime.
 expect(decision.head).not.toContain('secret=1');
 expect(decision.head).not.toContain('Bearer x');
 expect(decision.head).not.toContain('sb_publishable_ok');
 expect(decision.head.endsWith('\r\n\r\n')).toBe(true);
});

test('a socket is refused without a live key, for a paused environment, or while Realtime is off',()=>{
 const path=(key:string)=>`/${E}/realtime/v1/websocket?apikey=${key}&vsn=1.0.0`;
 expect(decider()(path('wrong'),headers())).toMatchObject({ok:false,status:401});
 expect(decider()(`/${E}/realtime/v1/websocket`,headers())).toMatchObject({ok:false,status:401});
 expect(decider({route:'maintenance'})(path('sb_publishable_ok'),headers())).toMatchObject({ok:false,status:503});
 const off:EnvironmentRoute={...route,realtime:undefined,realtimeToken:undefined};
 expect(decider({route:off})(path('sb_publishable_ok'),headers())).toMatchObject({ok:false,status:404});
 expect(decider()(`/e_${'b'.repeat(24)}/realtime/v1/websocket?apikey=sb_publishable_ok`,headers())).toMatchObject({ok:false,status:404});
 expect(decider({route:{...route,realtime:{url:'http://x:4000',tenantHost:'evil.example'}}})(path('sb_publishable_ok'),headers()))
  .toMatchObject({ok:false,status:503});
});

test('the broadcast API goes to the environment Realtime with its tenant and token; the rest of its API does not',async()=>{
 const calls:{url:string;headers:Headers}[]=[];
 const handler=createGateway(new Map([[E,route]]),(async(url:URL,init:RequestInit)=>{calls.push({url:String(url),headers:new Headers(init.headers)});
  return Response.json({});}) as unknown as typeof fetch,(runtime,key)=>key==='sb_publishable_ok');
 const send=await handler(new Request(`http://local/${E}/realtime/v1/api/broadcast`,{method:'POST',headers:{apikey:'sb_publishable_ok'},body:'{}'}));
 expect(send.status).toBe(200);
 expect(calls[0]!.url).toBe('http://10.0.0.9:4000/api/broadcast');
 expect(calls[0]!.headers.get('host')).toBe('a'.repeat(24)+'.realtime');
 expect(calls[0]!.headers.get('apikey')).toBe('anon.jwt.token');
 expect(calls[0]!.headers.get('authorization')).toBe('Bearer anon.jwt.token');
 for(const [method,path] of [['GET','/api/tenants'],['POST','/api/tenants'],['GET','/api/broadcast'],['GET','/socket/websocket']] as const)
  expect((await handler(new Request(`http://local/${E}/realtime/v1${path}`,{method,headers:{apikey:'sb_publishable_ok'}}))).status).toBe(404);
 expect(calls).toHaveLength(1);
});

test('the listener pipes an accepted socket to Realtime and keeps answering plain requests',async()=>{
 const upstream=Bun.serve<{host:string;search:string}>({port:0,fetch(request,server){
  if(server.upgrade(request,{data:{host:request.headers.get('host')??'',search:new URL(request.url).search}}))return;
  return new Response('no',{status:400});},
  websocket:{open(ws){ws.send(`${ws.data.host} ${ws.data.search}`);},message(ws,message){ws.send('echo:'+message);}}});
 const listener=await serveLocal(async request=>new Response('plain '+new URL(request.url).pathname),0,{
  isUpgrade:isRealtimeSocket,
  upgrade:(path,headers)=>path.includes('apikey=good')?{ok:true,host:'127.0.0.1',port:upstream.port!,
   head:`GET /socket/websocket?apikey=token HTTP/1.1\r\nHost: tenant.realtime\r\nupgrade: websocket\r\nconnection: Upgrade\r\n`+
    `sec-websocket-key: ${headers.get('sec-websocket-key')}\r\nsec-websocket-version: 13\r\n\r\n`}:
   {ok:false,status:401,message:'Invalid API key'}});
 try {
  expect(await (await fetch(`http://127.0.0.1:${listener.port}/x`)).text()).toBe('plain /x');
  expect(await (await fetch(`http://127.0.0.1:${listener.port}/y`,{method:'POST',body:'abc'})).text()).toBe('plain /y');
  const accepted=await new Promise<string[]>((resolve,reject)=>{
   const socket=new WebSocket(`ws://127.0.0.1:${listener.port}/${E}/realtime/v1/websocket?apikey=good&vsn=1.0.0`);
   const got:string[]=[];
   socket.onmessage=event=>{got.push(String(event.data));if(got.length===1)socket.send('ping');else{socket.close();resolve(got);}};
   socket.onerror=()=>reject(new Error('socket failed'));
   setTimeout(()=>reject(new Error('timeout '+got.join())),3000);
  });
  expect(accepted).toEqual(['tenant.realtime ?apikey=token','echo:ping']);
  const refused=await new Promise<boolean>(resolve=>{
   const socket=new WebSocket(`ws://127.0.0.1:${listener.port}/${E}/realtime/v1/websocket?apikey=bad`);
   socket.onopen=()=>resolve(false);socket.onerror=()=>resolve(true);socket.onclose=()=>resolve(true);
  });
  expect(refused).toBe(true);
 } finally {listener.stop(true);upstream.stop(true);}
});

test('owners and admins turn Realtime on and off; one change at a time',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
  const handler=realtimeHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor:string,method='GET',body?:unknown)=>handler(new Request(`http://local/management/v1/environments/${environment}/realtime`,
   {method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('alice')).status).toBe(409);
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  expect((await call('carol')).status).toBe(403);
  expect(((await (await call('alice')).json()) as {data:{state:string}}).data.state).toBe('off');
  expect((await call('alice','PUT',{enabled:'yes'})).status).toBe(400);
  expect((await call('alice','PUT',{enabled:true,extra:1})).status).toBe(400);
  const on=await call('alice','PUT',{enabled:true});
  expect(on.status).toBe(202);
  expect(((await on.json()) as {data:{state:string;desired:string}}).data).toMatchObject({state:'pending',desired:'on'});
  expect((await call('alice','PUT',{enabled:false})).status).toBe(409);
 } finally {catalog.close();}
});
