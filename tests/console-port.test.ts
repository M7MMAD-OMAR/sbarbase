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
