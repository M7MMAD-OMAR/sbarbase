import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import type {RequestLog} from '../gateway/observe';

/** Logs and metrics for one environment.
 *
 * GET `/environments/{id}/metrics`: the gateway's totals for the last hour, and the memory
 * and processor use of the environment's own services. Any member may read it.
 * GET `/environments/{id}/logs?source=requests|auth|rest|storage|realtime[&errors=1][&lines=N]`:
 * the gateway's last requests, or the last lines a service wrote. Owners and admins only,
 * because service logs name users and tables.
 *
 * Service lines come from the original containers as they wrote them. Storage is shared by
 * every environment, so only lines naming this environment's tenant are shown. Tokens, keys,
 * passwords and signed JWTs are replaced with `[redacted]` before a line leaves the server. */

export const SOURCES=['requests','auth','rest','storage','realtime'] as const;
export type Source=typeof SOURCES[number];
export type ContainerStats={service:string;cpuPercent:number|null;memoryBytes:number|null;memoryLimitBytes:number|null};
export type ContainerReader={
 logs(container:string,lines:number):Promise<string>;
 stats(containers:string[]):Promise<ContainerStats[]>;
};

const PREFIX='sbarbase-durable';
const MAX_LINES=1000,MAX_LINE=4000;
const runtimePattern=/^e_[a-f0-9]{24}$/;

export function containerName(runtime:string,source:Exclude<Source,'requests'>):string {
 if(!runtimePattern.test(runtime))throw new Error('Invalid runtime');
 return source==='storage'?`${PREFIX}-storage`:`${PREFIX}-${runtime}-${source}`;
}

const SECRET_PARAMETER=/((?:^|[?&\s"'{,;])(?:[a-z_]*token|apikey|api_key|[a-z_]*secret|password|signature|x-amz-signature)["']?\s*[=:]\s*["']?)([^&\s"',;}]+)/gi;
// In a query string only: `code` is also PostgREST's error code, which stays readable.
const QUERY_SECRET=/([?&](?:code|key|sig|state)=)([^&\s"',;}]+)/gi;
const BEARER=/\b(bearer\s+)[A-Za-z0-9._~+/=-]+/gi;
const JWT=/\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g;
const PUBLISHABLE=/\bsb_(publishable|secret)_[A-Za-z0-9_-]+/g;
const CONNECTION=/(postgres(?:ql)?:\/\/[^:\s/@]+:)[^@\s]+@/gi;

/** Removes what could let someone act as a user or the service from a log line. */
export function redact(line:string):string {
 return line.replace(CONNECTION,'$1[redacted]@').replace(JWT,'[redacted]').replace(PUBLISHABLE,'[redacted]')
  .replace(BEARER,'$1[redacted]').replace(SECRET_PARAMETER,'$1[redacted]')
  .replace(QUERY_SECRET,'$1[redacted]');
}

/** The last lines of one source, oldest first, redacted. Storage lines are kept only when
 * they name this environment's tenant. */
export function serviceLines(text:string,source:Exclude<Source,'requests'>,runtime:string,options:{errors?:boolean;lines:number}):string[] {
 let lines=text.split('\n').map(line=>line.trimEnd()).filter(Boolean);
 if(source==='storage')lines=lines.filter(line=>line.includes(`"${runtime}"`)||line.includes(`${runtime}.storage.internal`));
 if(options.errors)lines=lines.filter(line=>/\b(error|fatal|panic|fail(ed|ure)?|warn(ing)?)\b|"level":\s*"?(4\d|5\d|error|warn|fatal)|\b[45]\d\d\b/i.test(line));
 return lines.slice(-options.lines).map(line=>redact(line.length>MAX_LINE?line.slice(0,MAX_LINE)+'…':line));
}

async function run(args:string[],timeoutMs=10_000):Promise<string> {
 const child=Bun.spawn(['docker',...args],{stdout:'pipe',stderr:'pipe',stdin:'ignore'});
 const timer=setTimeout(()=>child.kill(),timeoutMs);
 try {
  const [out,err]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text()]);
  const code=await child.exited;
  if(code!==0){
   if(/no such container/i.test(err))return '';
   throw new Error('Docker is unavailable');
  }
  // Services write to both streams; both are the service's log.
  return out+err;
 } finally {clearTimeout(timer);}
}

function bytes(value:string):number|null {
 const match=value.trim().match(/^([\d.]+)\s*([KMGT]?i?B)$/i);
 if(!match)return null;
 const unit=match[2]!.toUpperCase();
 const scale:Record<string,number>={B:1,KB:1e3,MB:1e6,GB:1e9,TB:1e12,KIB:1024,MIB:1024**2,GIB:1024**3,TIB:1024**4};
 return scale[unit]===undefined?null:Math.round(Number(match[1])*scale[unit]!);
}

/** Parses `docker stats --no-stream --format '{{json .}}'`. */
export function parseStats(text:string,services:Record<string,string>):ContainerStats[] {
 const found:ContainerStats[]=[];
 for(const line of text.split('\n')){
  if(!line.trim())continue;
  let row:{Name?:string;CPUPerc?:string;MemUsage?:string};
  try{row=JSON.parse(line);}catch{continue;}
  const service=row.Name&&services[row.Name];
  if(!service)continue;
  const [used,limit]=(row.MemUsage??'').split('/');
  const cpu=Number.parseFloat((row.CPUPerc??'').replace('%',''));
  found.push({service,cpuPercent:Number.isFinite(cpu)?cpu:null,memoryBytes:used?bytes(used):null,memoryLimitBytes:limit?bytes(limit):null});
 }
 return found;
}

export const dockerReader:ContainerReader={
 logs:(container,lines)=>run(['logs','--timestamps','--tail',String(lines),container]),
 async stats(containers){
  const running=(await run(['ps','--format','{{.Names}}'])).split('\n').filter(name=>containers.includes(name));
  if(!running.length)return [];
  return parseStats(await run(['stats','--no-stream','--format','{{json .}}',...running],15_000),
   Object.fromEntries(running.map(name=>[name,name.replace(/^.*-/,'')])));
 },
};

export function observeHandler(catalog:Catalog,identify:ManagementIdentity,log:RequestLog,reader:ContainerReader=dockerReader) {
 const statsCache=new Map<string,{at:number;value:Promise<ContainerStats[]>}>();
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url);
  const match=url.pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(metrics|logs)$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!,kind=match[2]!;
  let runtime:string;
  try{runtime=catalog.withReadyEnvironment(actor,environment,kind==='logs',job=>job.runtime);}
  catch(error){
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
  if(kind==='metrics'){
   const containers=(['auth','rest','realtime'] as const).map(service=>containerName(runtime,service));
   let cached=statsCache.get(runtime);
   if(!cached||Date.now()-cached.at>10_000){
    cached={at:Date.now(),value:reader.stats(containers).catch(()=>[] as ContainerStats[])};
    statsCache.set(runtime,cached);
   }
   return reply(200,{data:{...log.metrics(runtime),services:await cached.value}});
  }
  const source=url.searchParams.get('source')??'requests';
  if(!(SOURCES as readonly string[]).includes(source))return reply(400,{message:`Choose a source: ${SOURCES.join(', ')}`});
  const errors=url.searchParams.get('errors')==='1';
  const requested=Number(url.searchParams.get('lines')??'200');
  if(!Number.isInteger(requested)||requested<1||requested>MAX_LINES)return reply(400,{message:`Ask for 1 to ${MAX_LINES} lines`});
  if(source==='requests')return reply(200,{data:{source,requests:log.recent(runtime,{errors,limit:requested})}});
  const service=source as Exclude<Source,'requests'>;
  try {
   // Storage is shared, so read further back to find this environment's own lines.
   const text=await reader.logs(containerName(runtime,service),service==='storage'?Math.min(requested*20,20_000):requested*(errors?5:1));
   return reply(200,{data:{source,lines:serviceLines(text,service,runtime,{errors,lines:requested})}});
  } catch {
   return reply(503,{message:'Service logs are unavailable right now'});
  }
 };
}
