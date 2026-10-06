import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {createServerHealthReader,parseServerMemory,serverDisk,type ServerHealthSnapshot} from '../src/control/server-health';

test('memory counters preserve zero swap and compute used from available',()=>{
 expect(parseServerMemory('MemTotal: 100 kB\nMemAvailable: 40 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB')).toEqual({totalBytes:102400,availableBytes:40960,usedBytes:61440,swapTotalBytes:0,swapFreeBytes:0});
});
test('missing, duplicate, inconsistent and oversized memory counters stay unavailable',()=>{
 expect(parseServerMemory('')).toEqual({totalBytes:null,availableBytes:null,usedBytes:null,swapTotalBytes:null,swapFreeBytes:null});
 expect(parseServerMemory('MemTotal: 10 kB\nMemAvailable: 11 kB').availableBytes).toBeNull();
 expect(parseServerMemory('MemTotal: 10 kB\nMemTotal: 10 kB').totalBytes).toBeNull();
 expect(parseServerMemory('MemTotal: 9007199254740991 kB').totalBytes).toBeNull();
 expect(parseServerMemory('SwapTotal: 1 kB\nSwapFree: 2 kB').swapFreeBytes).toBeNull();
});
test('filesystem bytes distinguish available from reserved free space',()=>{
 expect(serverDisk({bsize:4096,blocks:100,bfree:50,bavail:40})).toEqual({totalBytes:409600,freeBytes:204800,availableBytes:163840});
 for(const value of [{bsize:0,blocks:1,bfree:0,bavail:0},{bsize:4096,blocks:1,bfree:2,bavail:0},{bsize:4096,blocks:1,bfree:1,bavail:2},{bsize:4096,blocks:Number.MAX_SAFE_INTEGER,bfree:0,bavail:0}])expect(serverDisk(value)).toEqual({totalBytes:null,freeBytes:null,availableBytes:null});
});
test('reader is lazy, caches five seconds and refreshes when the clock moves backwards',()=>{
 let now=0,reads=0;
 const snapshot={capturedAt:1} as ServerHealthSnapshot;
 const read=createServerHealthReader(()=>{reads++;return {...snapshot,capturedAt:reads};},()=>now);
 expect(reads).toBe(0);expect(read().capturedAt).toBe(1);
 now=4999;expect(read().capturedAt).toBe(1);
 now=5000;expect(read().capturedAt).toBe(2);
 now=4000;expect(read().capturedAt).toBe(3);
});
test('server overview requires an installation operator and only permits GET',async()=>{
 const catalog=new Catalog(':memory:');
 try{
  const installation=catalog.initializeInstallation('server-fixture','owner','Installation');
  catalog.setMember('owner',installation,'viewer','viewer');
  catalog.setMember('owner',installation,'operator','admin');
  const handler=managementHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor?:string,method='GET')=>handler(new Request('http://local/management/v1/server',{method,headers:actor?{authorization:actor}:{}}));
  expect((await call()).status).toBe(401);
  expect((await call('viewer')).status).toBe(403);
  expect((await call('owner','POST')).status).toBe(405);
  const response=await call('operator');expect(response.status).toBe(200);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const {data}=await response.json();
  expect(['container','os-visible']).toContain(data.scope);
  expect(typeof data.capturedAt).toBe('number');
  expect(Object.keys(data).sort()).toEqual(['capturedAt','cpu','disk','memory','scope','uptimeSeconds']);
  catalog.changeMember('owner',installation,'operator','viewer');
  expect((await call('operator')).status).toBe(403);
 }finally{catalog.close();}
});
