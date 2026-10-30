import type {FunctionRow} from './format';
export type {FunctionRow};
export type RuntimeState={desired:'on'|'off';state:'off'|'pending'|'on'|'failed';failure:string|null;functions:FunctionRow[];secrets:string[]};
export type Range='15m'|'1h'|'3h'|'1d';
export const RANGES:{id:Range;label:string}[]=[{id:'15m',label:'15 min'},{id:'1h',label:'1 hour'},{id:'3h',label:'3 hours'},{id:'1d',label:'1 day'}];
export type Point={at:number;requests:number;clientErrors:number;serverErrors:number;averageMs:number|null;maxMs:number|null};
export type Metrics={range:Range;countingSince:number;points:Point[];totals:{requests:number;clientErrors:number;serverErrors:number;averageMs:number|null;maxMs:number|null};
 sinceDeploy:{requests:number;clientErrors:number;serverErrors:number;since:number;complete:boolean}};
export type Call={at:number;method:string;path:string;status:number;ms:number};
export type SourceFile={path:string;size:number;shared:boolean;content:string|null};
export type SecretRow={name:string;digest:string;updated_at:number|null};
export type DefaultSecret={name:string;description:string;provided:'yes'|'no'|'unknown';deprecated?:boolean};
export type ApiKey={id:string;kind:'publishable'|'secret';name:string;revoked_at:number|null;token?:string|null};

/** What the Usage part of Overview and the output part of Logs get from the server for one function (the runtime's event worker). */
export type UsagePoint={at:number;workers:number;averageCpuMs:number|null;maxCpuMs:number|null;averageMemory:number|null;maxMemory:number|null};
export type Usage={available:true;known:boolean;range:Range;since:number;points:UsagePoint[];
 totals:{workers:number;boots:number;averageBootMs:number|null;averageCpuMs:number|null;maxCpuMs:number|null;averageMemory:number|null;maxMemory:number|null;heapPercent:number|null;externalPercent:number|null;errors:number}}
 |{available:false;reason:'off'|'unreachable'};
export type OutputLine={at:number;level:string;msg:string};
export type Output={available:true;known:boolean;lines:OutputLine[]}|{available:false;reason:'off'|'unreachable'};

/** The sentence under Usage when there is nothing to draw, or '' when there are numbers. Each says what is true and what to do. */
export function usageNote(usage:Usage|undefined):string {
 if(!usage)return '';
 if(!usage.available)return usage.reason==='off'
  ?'Edge Functions are not running, so there is nothing to show. Turn them on, or call the function.'
  :'The runtime did not answer. A runtime that was started before this version has no event worker yet: it gets one the next time Edge Functions start.';
 if(usage.totals.workers===0)return 'No worker has ended in this range. The runtime reports a worker\'s CPU time and memory when it drops the worker, about a minute after its last call.';
 return '';
}

/** Memory in the unit people read it in. */
export function mebibytes(bytes:number|null):string {
 if(bytes===null)return 'none';
 return bytes<1024*1024?`${Math.round(bytes/102.4)/10} KiB`:`${Math.round(bytes/104857.6)/10} MiB`;
}
export const cpuTime=(ms:number|null)=>ms===null?'none':ms<1000?`${ms} ms`:`${Math.round(ms/100)/10} s`;
export const share=(value:number|null)=>value===null?'none':`${value}%`;
