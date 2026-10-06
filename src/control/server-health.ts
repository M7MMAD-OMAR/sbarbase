import {cpus,loadavg,uptime} from 'node:os';
import {existsSync,readFileSync,statfsSync} from 'node:fs';

export type ServerMemory={totalBytes:number|null;availableBytes:number|null;usedBytes:number|null;swapTotalBytes:number|null;swapFreeBytes:number|null};
export type ServerDisk={totalBytes:number|null;freeBytes:number|null;availableBytes:number|null};
export type ServerHealthSnapshot={capturedAt:number;scope:'container'|'os-visible';cpu:{cores:number|null;loadAverage:number[]|null};uptimeSeconds:number|null;memory:ServerMemory;disk:ServerDisk};

const emptyMemory=():ServerMemory=>({totalBytes:null,availableBytes:null,usedBytes:null,swapTotalBytes:null,swapFreeBytes:null});
const emptyDisk=():ServerDisk=>({totalBytes:null,freeBytes:null,availableBytes:null});
function nonnegative(value:number):number|null {return Number.isFinite(value)&&value>=0?value:null;}

/** Linux counters are KiB. Missing or inconsistent counters stay unavailable. */
export function parseServerMemory(text:string):ServerMemory {
 const values=new Map<string,number|null>();
 for(const line of text.split('\n')){
  const match=line.match(/^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s*(\d+)\s+kB\s*$/);
  if(!match)continue;
  const bytes=Number(match[2])*1024;
  values.set(match[1]!,values.has(match[1]!)||!Number.isSafeInteger(bytes)?null:bytes);
 }
 const total=values.get('MemTotal')??null,available=values.get('MemAvailable')??null;
 const swapTotal=values.get('SwapTotal')??null,swapFree=values.get('SwapFree')??null;
 const usableAvailable=total!==null&&available!==null&&available<=total?available:null;
 return {totalBytes:total,availableBytes:usableAvailable,usedBytes:total!==null&&usableAvailable!==null?total-usableAvailable:null,
  swapTotalBytes:swapTotal,swapFreeBytes:swapTotal!==null&&swapFree!==null&&swapFree<=swapTotal?swapFree:null};
}

/** Multiplication must remain precise; do not silently invent truncated byte counts. */
export function serverDisk(stat:{bsize:number;blocks:number;bfree:number;bavail:number}):ServerDisk {
 const {bsize,blocks,bfree,bavail}=stat;
 if(![bsize,blocks,bfree,bavail].every(value=>Number.isSafeInteger(value)&&value>=0)||bsize===0||bfree>blocks||bavail>bfree)return emptyDisk();
 const bytes=[blocks,bfree,bavail].map(value=>value*bsize);
 if(!bytes.every(Number.isSafeInteger))return emptyDisk();
 return {totalBytes:bytes[0]!,freeBytes:bytes[1]!,availableBytes:bytes[2]!};
}

function collectServerHealth():ServerHealthSnapshot {
 let memory=emptyMemory(),disk=emptyDisk(),container=false,cores:number|null=null,loadAverage:number[]|null=null,uptimeSeconds:number|null=null;
 try{memory=parseServerMemory(readFileSync('/proc/meminfo','utf8'));}catch{}
 try{disk=serverDisk(statfsSync('.'));}catch{}
 try{container=existsSync('/.dockerenv')||existsSync('/run/.containerenv')||/docker|containerd|kubepods|lxc/.test(readFileSync('/proc/1/cgroup','utf8'));}catch{}
 try{const count=cpus().length;cores=count>0?count:null;}catch{}
 try{const values=loadavg();loadAverage=values.length===3&&values.every(value=>nonnegative(value)!==null)?values:null;}catch{}
 try{uptimeSeconds=nonnegative(uptime());}catch{}
 return {capturedAt:Date.now(),scope:container?'container':'os-visible',cpu:{cores,loadAverage},uptimeSeconds,memory,disk};
}

/** No reads occur until called after authorization. Concurrent requests reuse one short snapshot. */
export function createServerHealthReader(collect:()=>ServerHealthSnapshot=collectServerHealth,now:()=>number=()=>performance.now()) {
 let cached:ServerHealthSnapshot|undefined,at=0;
 return ():ServerHealthSnapshot=>{
  const current=now();
  if(cached&&current>=at&&current-at<5000)return cached;
  cached=collect();at=current;return cached;
 };
}
