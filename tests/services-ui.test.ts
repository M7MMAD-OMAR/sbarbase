import {expect,test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {ago} from '../ui/functions-workspace/format';
import {ABOUT,COST_NOTE,NAMES,SWITCH_ROUTE,changing,confirmOff,costText,lastUsedText,memory,memoryText,staleText,type Service,type Services} from '../ui/services-view';

const MIB=1024*1024,now=Date.UTC(2026,9,30,12,0,0);
const service=(over:Partial<Service>={}):Service=>({id:'realtime',state:'on',failure:null,updatedAt:null,memoryBytes:183*MIB,memoryLimitBytes:320*MIB,costBytes:320*MIB,
 lastUsedAt:now-11*86_400_000,tracked:true,inUse:0,...over});
const capacity:Services['capacity']={availableBytes:Math.round(2.1*1024*MIB),totalBytes:16*1024*MIB};

test('memory reads in MiB under a GiB and in GiB with one decimal above',()=>{
 expect(memory(183*MIB)).toBe('183 MiB');expect(memory(1024*MIB)).toBe('1 GiB');expect(memory(Math.round(2.14*1024*MIB))).toBe('2.1 GiB');expect(memory(0)).toBe('0 MiB');
});

test('the memory line says what it can: a figure, not measured yet, none while off, or no container',()=>{
 expect(memoryText(service())).toBe('183 MiB of 320 MiB');
 expect(memoryText(service({memoryLimitBytes:null}))).toBe('183 MiB');
 expect(memoryText(service({memoryBytes:null}))).toBe('Not measured yet');
 expect(memoryText(service({memoryBytes:null,state:'off'}))).toBe('None while off');
 expect(memoryText(service({id:'database',memoryBytes:null,state:'off'}))).toBe('No container of its own');
 expect(memoryText(service({id:'cron',memoryBytes:null,state:'on'}))).toBe('No container of its own');
});

test('what turning a service on costs is said against what the server has free',()=>{
 expect(costText(service({state:'off'}),capacity)).toBe('Turning it on needs about 320 MiB. This server has about 2.1 GiB of memory available now and keeps a reserve, so it can still say no.');
 expect(costText(service({state:'off'}),{availableBytes:null,totalBytes:null})).toBe('Turning it on needs about 320 MiB.');
 expect(costText(service({id:'cron',costBytes:null}),capacity)).toBeNull();
});

test('last use is a time, never used, or not recorded for services the product does not track',()=>{
 expect(lastUsedText(service(),now,ago)).toBe('11 days ago');
 expect(lastUsedText(service({lastUsedAt:null}),now,ago)).toBe('Never used');
 expect(lastUsedText(service({tracked:false,lastUsedAt:null}),now,ago)).toBe('Not recorded');
});

test('turning off asks first only when something is open, and says how many',()=>{
 expect(confirmOff(service())).toBeNull();
 expect(confirmOff(service({inUse:1}))).toContain('1 connection is open right now');
 expect(confirmOff(service({inUse:3}))).toContain('3 connections are open right now');
 expect(confirmOff(service({id:'functions',inUse:2}))).toContain('2 calls are running right now');
 expect(confirmOff(service({id:'functions',inUse:1}))).toContain('1 call is running right now');
 expect(confirmOff(service({state:'off',inUse:4}))).toBeNull();
 expect(confirmOff(service({id:'cron',inUse:4}))).toBeNull();
});

test('a service that is starting or stopping is waited for',()=>{
 expect(changing(service({state:'starting'}))).toBe(true);expect(changing(service({state:'stopping'}))).toBe(true);
 expect(changing(service({state:'on'}))).toBe(false);expect(changing(service({state:'failed'}))).toBe(false);
});

test('an old or missing sample is said plainly',()=>{
 expect(staleText({capturedAt:now-5*60_000,stale:true},now,ago)).toBe('Memory figures are out of date (measured 5 minutes ago).');
 expect(staleText({capturedAt:null,stale:true},now,ago)).toContain('not available yet');
 expect(staleText({capturedAt:now,stale:false},now,ago)).toBeNull();
});

test('every service has a name, a description and a cost note',()=>{
 for(const id of ['realtime','functions','database','cron','studio'] as const){expect(NAMES[id]).toBeTruthy();expect(ABOUT[id].length).toBeGreaterThan(20);expect(COST_NOTE[id].length).toBeGreaterThan(10);}
});

test('the switches go to the routes that already exist, and only for the three services they own',()=>{
 expect(SWITCH_ROUTE).toEqual({realtime:'/realtime',functions:'/functions',cron:'/scheduled-jobs'});
});

test('the page text carries no long dash and the sources have no borders on cards',()=>{
 for(const file of ['../ui/services-view.ts','../ui/Services.tsx','../ui/services.css']){
  const text=readFileSync(new URL(file,import.meta.url),'utf8');
  expect(text).not.toMatch(/[–—]/);
 }
 expect(readFileSync(new URL('../ui/services.css',import.meta.url),'utf8')).not.toMatch(/border(-[a-z]+)?\s*:\s*[1-9]/);
});
