/** The write side of Edge Functions beyond a plain deploy: the boot check that runs before a new version goes live, and the
 * settings a function can change without a new version. Deploy and delete stay in functions.ts. */

/** What a boot check says about a version folder that is written but not live.
 * - passed: the runtime created a worker on it.
 * - refused: the runtime could not (a syntax error, an import it cannot resolve, no entry point); `message` says why.
 * - skipped: nothing was asked (Edge Functions are off or not applied, so no runtime is there to ask).
 * - unreachable: the runtime did not answer in time or answered something unexpected; the deploy goes on and says it was not checked. */
export type BootResult={state:'passed'}|{state:'refused';message:string}|{state:'skipped'}|{state:'unreachable'};
export type Boot=(runtime:string,version:string,name:string)=>Promise<BootResult>;
export type BootCheck='passed'|'skipped'|'unreachable';

/** The runtime's message for a worker that would not start, without the container's folder names, so the person sees
 * `Module not found "./missing.ts"` rather than a path inside the runtime. */
export function bootMessage(raw:string):string {
 return raw
  .replace(/^worker boot error:\s*/i,'').replace(/^failed to bootstrap runtime:\s*/i,'').replace(/^failed to create the graph:\s*/i,'')
  .replace(/file:\/\/\/home\/deno\/functions\/v[a-z0-9]+-[a-f0-9]{8}\/[A-Za-z0-9_-]+\//g,'')
  .replace(/file:\/\/\/home\/deno\/functions\/v[a-z0-9]+-[a-f0-9]{8}\//g,'')
  .replace(/file:\/\/\/[^\s"']*/g,'[file]')
  .replace(/(?<![\w.])\/(?:home|var|tmp|run|etc|usr|opt|root|proc|deno-dir)\/[^\s"')]*/g,'[path]')
  .replace(/\u001b\[[0-9;]*m/g,'').replace(/\s+\n/g,'\n').trim().slice(0,600);
}

/** A runtime's answer to POST /_sb_boot/<name>/<version> as a BootResult. */
export async function bootResultOf(response:Response):Promise<BootResult> {
 if(response.status===200)return {state:'passed'};
 if(response.status===422){
  const value=await response.json().catch(()=>null) as {message?:unknown}|null;
  return {state:'refused',message:bootMessage(typeof value?.message==='string'?value.message:'The runtime could not start the function.')};
 }
 return {state:'unreachable'};
}

/** The body of a settings change: exactly `{"verify_jwt": true|false}`. */
export function settingsChange(input:unknown):{verify_jwt:boolean}|undefined {
 if(!input||typeof input!=='object'||Array.isArray(input))return undefined;
 const keys=Object.keys(input);
 const value=(input as {verify_jwt?:unknown}).verify_jwt;
 return keys.length===1&&keys[0]==='verify_jwt'&&typeof value==='boolean'?{verify_jwt:value}:undefined;
}

/** The boot call to one environment's runtime. `route` says where it is and which service role token to present (undefined
 * when there is no runtime to ask); `transport` is fetch. Nothing here throws: a failure to ask is `unreachable`. */
export function bootThrough(route:(runtime:string)=>{url?:string;token?:string}|'maintenance'|undefined,transport:typeof fetch,timeoutMs=20_000):Boot {
 return async(runtime,version,name)=>{
  try{
   const where=route(runtime);
   if(where==='maintenance')return {state:'unreachable'};
   if(!where?.url||!where.token)return {state:'skipped'};
   return await bootResultOf(await transport(new URL(`/_sb_boot/${encodeURIComponent(name)}/${encodeURIComponent(version)}`,where.url),
    {method:'POST',headers:{authorization:'Bearer '+where.token},signal:AbortSignal.timeout(timeoutMs)}));
  }catch{return {state:'unreachable'};}
 };
}
