import {describe,expect,test} from 'bun:test';
import {consolePort,serveLocal} from '../src/http/local-server';

describe('console port',()=>{
 test('an unset port stays ephemeral, as the lab expects',()=>{
  expect(consolePort(undefined)).toBe(0);
  expect(consolePort('')).toBe(0);
 });
 test('a server pins a valid unprivileged port',()=>{
  expect(consolePort('8787')).toBe(8787);
 });
 test('anything else refuses instead of binding somewhere unexpected',()=>{
  for(const value of ['80','0','65536','87 87','8787x','-1','0x2000'])expect(()=>consolePort(value)).toThrow('SBARBASE_CONSOLE_PORT');
 });
 test('the listener binds the pinned port on loopback',async()=>{
  const probe=await serveLocal(()=>new Response('ok'));const free=probe.port;probe.stop(true);
  const server=await serveLocal(()=>new Response('ok'),free);
  try {
   expect(server.port).toBe(free);
   expect(await (await fetch(`http://127.0.0.1:${free}/`)).text()).toBe('ok');
  } finally {server.stop(true);}
 });
});

describe('request bodies over the real listener',()=>{
 test('a POST without a body reaches the handler without one',async()=>{
  const seen:(boolean|string)[]=[];
  const server=await serveLocal(async request=>{seen.push(request.body===null?true:await request.text());return new Response('ok');});
  try {
   await fetch(`http://127.0.0.1:${server.port}/keys`,{method:'POST'});
   await fetch(`http://127.0.0.1:${server.port}/keys/1`,{method:'DELETE'});
   await fetch(`http://127.0.0.1:${server.port}/projects`,{method:'POST',body:'{"name":"x"}'});
   expect(seen).toEqual([true,true,'{"name":"x"}']);
  } finally {server.stop(true);}
 });
});
test('a refused upload answers with its own status and does not hold up the next request',async()=>{
 // A handler that refuses without reading the body (declared too large), and one that stops
 // reading part way (the gateway's upload limit). Neither may turn into a 200, and the unread
 // rest of the body must not stall the client's next request on the same connection.
 for(const cancel of [false,true]) {
  const server=await serveLocal(async request=>{
   if(request.method==='POST'){if(cancel)void request.body?.cancel();return new Response('too large',{status:413});}
   return new Response('ok');
  },0);
  try {
   const first=await fetch(`http://127.0.0.1:${server.port}/upload`,{method:'POST',body:new Uint8Array(8*1024*1024),signal:AbortSignal.timeout(8000)});
   expect([first.status,await first.text(),first.headers.get('connection')]).toEqual([413,'too large','close']);
   const started=Date.now();
   const next=await fetch(`http://127.0.0.1:${server.port}/list`,{signal:AbortSignal.timeout(8000)});
   expect(await next.text()).toBe('ok');
   expect(Date.now()-started).toBeLessThan(3000);
  } finally {server.stop(true);}
 }
},30000);
