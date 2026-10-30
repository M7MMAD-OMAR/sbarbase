import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {capacityHandler,composeCapacity,diskLevel,memoryLevel,type Thresholds} from '../src/control/capacity';
import {readStats,type Stats} from '../src/control/services';
import type {ServerHealthSnapshot} from '../src/control/server-health';
import {actionText,busy,cacheText,CACHE_GUARANTEE,diskNote,filesystemLabel,memoryNote,size,swapNote,type Capacity} from '../ui/capacity-view';
import {ago} from '../ui/functions-workspace/format';

const GIB=1024**3,MIB=1024**2;
const thresholds:Thresholds={memoryReserveBytes:2*GIB,newEnvironmentMemoryBytes:512*MIB,diskReserveBytes:5*GIB,newEnvironmentDiskBytes:GIB,diskWarningBytes:5*GIB,diskWarningShare:.10,diskCriticalBytes:2*GIB,diskCriticalShare:.05};
const stats=(at:number,extra:Partial<Stats>={}):Stats=>({capturedAt:at,containers:{},costs:{realtime:null,functions:null},thresholds,buildCache:{totalBytes:466_000_000,reclaimableBytes:400_000_000,at},...extra});
const health=(available:number,disk=50*GIB,total=100*GIB,swap=0):ServerHealthSnapshot=>({capturedAt:1,scope:'os-visible',cpu:{cores:4,loadAverage:null},uptimeSeconds:1,
 memory:{totalBytes:16*GIB,availableBytes:available,usedBytes:16*GIB-available,swapTotalBytes:swap,swapFreeBytes:swap},disk:{totalBytes:total,freeBytes:disk,availableBytes:disk}});

test('memory is critical where admission would refuse a new environment, a warning with room for one more, and enough above',()=>{
 expect(memoryLevel(2*GIB-1,thresholds)).toBe('critical');
 expect(memoryLevel(2*GIB+512*MIB-1,thresholds)).toBe('critical');                      // reserve plus the environment's own memory: below it admission refuses
 expect(memoryLevel(2*GIB+512*MIB,thresholds)).toBe('warning');
 expect(memoryLevel(2*GIB+1024*MIB-1,thresholds)).toBe('warning');
 expect(memoryLevel(2*GIB+1024*MIB,thresholds)).toBe('ok');
 expect(memoryLevel(null,thresholds)).toBe('unknown');expect(memoryLevel(8*GIB,null)).toBe('unknown');
});

test('disk uses the supervisor\'s two lines, bytes or share, whichever is hit first',()=>{
 expect(diskLevel(GIB,100*GIB,thresholds)).toBe('critical');                 // under 2 GiB
 expect(diskLevel(4*GIB,100*GIB,thresholds)).toBe('critical');                // 4% of 100 GiB
 expect(diskLevel(9*GIB,100*GIB,thresholds)).toBe('warning');                 // 9%
 expect(diskLevel(4*GIB,20*GIB,thresholds)).toBe('warning');                  // under 5 GiB, 20%
 expect(diskLevel(30*GIB,100*GIB,thresholds)).toBe('ok');
 expect(diskLevel(1,0,thresholds)).toBe('unknown');expect(diskLevel(null,10,thresholds)).toBe('unknown');expect(diskLevel(30*GIB,100*GIB,null)).toBe('unknown');
});

test('a stale sample shows no thresholds and levels that say not known, never a stale verdict',()=>{
 const now=Date.now();
 const fresh=composeCapacity({health:health(3.2*GIB),stats:stats(now-60_000),action:null,now});
 expect(fresh).toMatchObject({stale:false,memory:{level:'ok',nextEnvironmentNeedsBytes:2.5*GIB},buildCache:{totalBytes:466_000_000,beforeClear:false}});
 const old=composeCapacity({health:health(100*MIB),stats:stats(now-20*60_000),action:null,now});
 expect(old.stale).toBe(true);expect(old.memory.level).toBe('unknown');expect(old.thresholds).toBeNull();
 const none=composeCapacity({health:health(100*MIB),action:null,now});
 expect(none).toMatchObject({stale:true,measuredAt:null,buildCache:null});
});

test('the sampler file keeps thresholds and the build cache size only in their exact shape',()=>{
 const directory=mkdtempSync(join(tmpdir(),'capacity-'));
 try{
  const path=join(directory,'stats.json'),base={version:1,capturedAt:9,containers:{}};
  writeFileSync(path,JSON.stringify({...base,thresholds,buildCache:{totalBytes:5,reclaimableBytes:4,at:3}}));
  expect(readStats(path)).toMatchObject({thresholds,buildCache:{totalBytes:5,reclaimableBytes:4,at:3}});
  writeFileSync(path,JSON.stringify({...base,thresholds:{...thresholds,diskWarningShare:2},buildCache:{totalBytes:-1,reclaimableBytes:4,at:3}}));
  const bad=readStats(path)!;expect(bad.thresholds).toBeUndefined();expect(bad.buildCache).toBeUndefined();
  writeFileSync(path,JSON.stringify({...base,thresholds:{...thresholds,memoryReserveBytes:'a lot'}}));expect(readStats(path)!.thresholds).toBeUndefined();
  writeFileSync(path,JSON.stringify({...base,thresholds:{...thresholds,extra:'ignored'}}));
  expect(Object.keys(readStats(path)!.thresholds!)).not.toContain('extra');
 }finally{rmSync(directory,{recursive:true,force:true});}
});

function ready() {
 const catalog=new Catalog(':memory:');
 catalog.initializeInstallation('op-1','alice','Home');
 catalog.setMember('alice',catalog.installationBootstrap()!.organization,'carol','viewer');
 return catalog;
}
const call=(handler:ReturnType<typeof capacityHandler>,actor:string,method:string,path='capacity')=>handler(new Request(`http://local/management/v1/server/${path}`,{method,headers:{authorization:actor}}));

test('the capacity route is for installation operators only, and a build cache request is one at a time and audited',async()=>{
 const catalog=ready();
 try{
  const handler=capacityHandler(catalog,async request=>request.headers.get('authorization'),{health:()=>health(3.2*GIB),stats:()=>stats(Date.now())});
  expect((await call(handler,'carol','GET')).status).toBe(403);
  expect((await call(handler,'carol','POST','build-cache')).status).toBe(403);
  expect((await call(handler,'mallory','POST','build-cache')).status).toBe(403);
  expect((await call(handler,'alice','PUT')).status).toBe(405);
  expect((await call(handler,'alice','POST')).status).toBe(405);
  expect((await call(handler,'alice','GET','build-cache')).status).toBe(405);
  const view=await (await call(handler,'alice','GET')).json() as {data:Capacity};
  expect(view.data.action).toBeNull();expect(view.data.buildCache?.reclaimableBytes).toBe(400_000_000);
  const first=await call(handler,'alice','POST','build-cache');
  expect(first.status).toBe(202);expect(((await first.json()) as {data:Capacity}).data.action?.state).toBe('pending');
  expect((await call(handler,'alice','POST','build-cache')).status).toBe(409);
  const audit=catalog.db.query<{actor:string;action:string},[]>("SELECT actor,action FROM audit_events WHERE action='host.build_cache_clear_requested'").all();
  expect(audit).toEqual([{actor:'alice',action:'host.build_cache_clear_requested'}]);
  // The supervisor finished: another request is allowed.
  catalog.db.query("UPDATE host_actions SET state='done',reclaimed_bytes=366000000,finished_at=?").run(Date.now());
  expect((await call(handler,'alice','POST','build-cache')).status).toBe(202);
 }finally{catalog.close();}
});

test('a request the supervisor never finished stops blocking after half an hour',async()=>{
 const catalog=ready();
 try{
  catalog.requestHostAction('alice','build_cache_clear');
  expect(()=>catalog.requestHostAction('alice','build_cache_clear')).toThrow('Host action in progress');
  catalog.db.query("UPDATE host_actions SET state='running',requested_at=?").run(Date.now()-31*60_000);
  expect(catalog.requestHostAction('alice','build_cache_clear').state).toBe('pending');
  expect(()=>catalog.requestHostAction('alice','image_prune' as never)).toThrow();
  expect(()=>catalog.requestHostAction('carol','build_cache_clear')).toThrow('Forbidden');
 }finally{catalog.close();}
});

const cap=(over:Partial<Capacity>={}):Capacity=>({thresholds,memory:{availableBytes:3.2*GIB,totalBytes:16*GIB,swapTotalBytes:0,level:'ok',nextEnvironmentNeedsBytes:2.5*GIB},
 disk:{availableBytes:50*GIB,totalBytes:100*GIB,level:'ok',reserveBytes:5*GIB,filesystems:[]},buildCache:{totalBytes:466_000_000,reclaimableBytes:400_000_000,measuredAt:1,beforeClear:false},action:null,measuredAt:1,stale:false,...over});

test('the words say what the level means in the supervisor\'s own numbers, and what clearing the cache does not touch',()=>{
 expect(memoryNote(cap())).toContain('The server keeps 2 GiB in reserve. A new environment needs about 2.5 GiB available.');
 expect(memoryNote(cap({memory:{...cap().memory,level:'critical',availableBytes:GIB}}))).toContain('a new environment is refused until memory is freed');
 expect(memoryNote(cap({memory:{...cap().memory,level:'warning'}}))).toContain('room for only one more environment');
 // It promises nothing for Realtime or Edge Functions, which pass their own check.
 expect(memoryNote(cap())).toContain('Realtime and Edge Functions pass their own check of memory and database connections as well');
 expect(memoryNote(cap())).not.toContain('will be refused');
 expect(memoryNote(cap({thresholds:null}))).toContain('have not arrived yet');
 expect(diskNote(cap())).toContain('warning below 5 GiB or 10% free and critical below 2 GiB or 5%');
 expect(diskNote(cap({disk:{...cap().disk,level:'critical'}}))).toContain('Backups and databases stop working when the disk is full');
 expect(swapNote(0)).toContain('no swap');expect(swapNote(0)).toContain('stopped, not slowed down');
 expect(swapNote(2*GIB)).toContain('2 GiB of swap');expect(swapNote(null)).toContain('could not be read');
 expect(cacheText(cap(),1_000_000,()=> '2 minutes ago')).toBe("Docker's build cache holds 444 MiB, of which up to 381 MiB can be freed (measured 2 minutes ago).");
 expect(cacheText(cap({buildCache:{...cap().buildCache!,beforeClear:true}}))).toContain('cleared after the last measurement');
 expect(cacheText(cap({buildCache:null}))).toContain('has not been measured yet');
 expect(CACHE_GUARANTEE).toContain('Images, containers and volumes are never touched');
 expect(CACHE_GUARANTEE).toContain('the way back of an update needs the images');
 expect(size(null)).toBe('Unavailable');
});

test('after a clear the old size is not shown as current, and the page does not call a stuck request in progress for ever',()=>{
 const now=Date.now();
 const old=stats(now-600_000);
 const cleared=composeCapacity({health:health(3.2*GIB),stats:old,action:{state:'done',requestedAt:now-90_000,finishedAt:now-60_000,reclaimedBytes:1,failure:null},now});
 expect(cleared.buildCache?.beforeClear).toBe(true);
 const remeasured=composeCapacity({health:health(3.2*GIB),stats:stats(now-30_000),action:{state:'done',requestedAt:now-90_000,finishedAt:now-60_000,reclaimedBytes:1,failure:null},now});
 expect(remeasured.buildCache?.beforeClear).toBe(false);
 const stuck={state:'running' as const,requestedAt:now-31*60_000,finishedAt:null,reclaimedBytes:null,failure:null};
 expect(busy(stuck,now)).toBe(false);
 expect(busy({...stuck,requestedAt:now-60_000},now)).toBe(true);
 expect(busy(null,now)).toBe(false);
});

test('each filesystem Sbarbase writes to has its own level and the disk is the worst of them',()=>{
 const now=Date.now();
 const withFs=composeCapacity({health:health(3.2*GIB,50*GIB),stats:stats(now-1000,{filesystems:{checkout:{availableBytes:80*GIB,totalBytes:100*GIB},docker:{availableBytes:GIB,totalBytes:100*GIB}}}),action:null,now});
 expect(withFs.disk.filesystems.map(item=>[item.name,item.level])).toEqual([['checkout','ok'],['docker','critical']]);
 expect(withFs.disk.level).toBe('critical');                                         // the console's own disk is fine, Docker's is not
 const only=composeCapacity({health:health(3.2*GIB,50*GIB),stats:stats(now-1000,{filesystems:{checkout:{availableBytes:80*GIB,totalBytes:100*GIB}}}),action:null,now});
 expect(only.disk.level).toBe('ok');
 const none=composeCapacity({health:health(3.2*GIB,50*GIB),stats:stats(now-1000),action:null,now});
 expect(none.disk.filesystems).toEqual([]);expect(none.disk.level).toBe('ok');         // falls back to the console's own filesystem
 expect(filesystemLabel('docker')).toBe('The disk Docker keeps its data on');
});

test('the sampler file keeps filesystems only in their exact shape',()=>{
 const directory=mkdtempSync(join(tmpdir(),'fs-'));
 try{
  const path=join(directory,'s.json');
  writeFileSync(path,JSON.stringify({version:1,capturedAt:9,containers:{},filesystems:{checkout:{availableBytes:5,totalBytes:10},docker:{availableBytes:11,totalBytes:10},other:{availableBytes:1,totalBytes:2}}}));
  expect(readStats(path)!.filesystems).toEqual({checkout:{availableBytes:5,totalBytes:10}});
 }finally{rmSync(directory,{recursive:true,force:true});}
});

test('the outcome of a clear is said as it happened',()=>{
 const now=Date.UTC(2026,9,30,12);
 expect(actionText(null,now,ago)).toBeNull();
 expect(actionText({state:'pending',requestedAt:now,finishedAt:null,reclaimedBytes:null,failure:null},now,ago)).toBe('Clearing the build cache…');
 expect(actionText({state:'done',requestedAt:now-5000,finishedAt:now-60_000,reclaimedBytes:366_000_000,failure:null},now,ago)).toBe('Cleared 1 minute ago and freed 349 MiB.');
 expect(actionText({state:'failed',requestedAt:1,finishedAt:2,reclaimedBytes:null,failure:'Docker did not clear the build cache. Nothing else was touched.'},now,ago)).toContain('Nothing else was touched');
});
