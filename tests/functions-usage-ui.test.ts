import {expect,test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {cpuTime,mebibytes,share,usageNote,type Usage} from '../ui/functions-workspace/types';

const totals={workers:2,boots:3,averageBootMs:20,averageCpuMs:10,maxCpuMs:30,averageMemory:10_000_000,maxMemory:12_000_000,heapPercent:70,externalPercent:30,errors:1};
const ok=(over:Partial<typeof totals>={}):Usage=>({available:true,known:true,range:'1h',since:1,points:[],totals:{...totals,...over}});

test('the sentence under Usage says what is true when there is nothing to draw, and is empty when there are numbers',()=>{
 expect(usageNote(undefined)).toBe('');
 expect(usageNote(ok())).toBe('');
 expect(usageNote({available:false,reason:'off'})).toContain('not running');
 expect(usageNote({available:false,reason:'unreachable'})).toContain('did not answer');
 expect(usageNote({available:false,reason:'unreachable'})).toContain('no event worker yet');
 expect(usageNote(ok({workers:0}))).toContain('No worker has ended');
 expect(usageNote(ok({workers:0}))).toContain('about a minute after its last call');
});

test('memory and CPU read in units a person uses',()=>{
 expect(mebibytes(null)).toBe('none');expect(mebibytes(0)).toBe('0 KiB');expect(mebibytes(2048)).toBe('2 KiB');
 expect(mebibytes(10_778_351)).toBe('10.3 MiB');expect(mebibytes(150*1024*1024)).toBe('150 MiB');
 expect(cpuTime(null)).toBe('none');expect(cpuTime(59)).toBe('59 ms');expect(cpuTime(1500)).toBe('1.5 s');
 expect(share(null)).toBe('none');expect(share(70.5)).toBe('70.5%');
});

test('Overview asks for the usage of its own function and range, and Logs for the output, through the passed path only',()=>{
 const overview=readFileSync('ui/functions-workspace/Overview.tsx','utf8'),logs=readFileSync('ui/functions-workspace/Logs.tsx','utf8');
 expect(overview).toContain('`${path}/functions/${fn.name}/usage?range=${range}`');
 expect(logs).toContain('`${path}/functions/${fn.name}/output?range=${range}&limit=200');
 for(const text of [overview,logs])expect(text).not.toMatch(/fetch\(|XMLHttpRequest|location\.origin\s*\+\s*'\/management/);
});

test('the numbers are said to be per worker lifetime and memory at the end, and never as per call',()=>{
 const overview=readFileSync('ui/functions-workspace/Overview.tsx','utf8');
 expect(overview).toContain("one worker's whole life, not one call");
 expect(overview).toContain('memory is what the worker held when it ended');
 expect(overview).not.toMatch(/per call|per request/i);
});

test('when the runtime cannot say which function wrote a line, Logs falls back to the environment\'s output and says so',()=>{
 const logs=readFileSync('ui/functions-workspace/Logs.tsx','utf8');
 expect(logs).toContain('lines cannot be told apart by function');
 expect(logs).toContain('These lines are for the whole environment');
 expect(logs).toContain('Output of {fn.name}');
});
