import {resolve} from 'node:path';
import {InventoryRefusal,type LifecycleResource} from './lifecycle-contract';
import {runLifecycleOperation,type LifecycleAdapter} from './lifecycle';
import type {Catalog} from './catalog';
import {LifecycleAuthorizationStore} from './lifecycle-authority';

/** One python run of the durable runtime's own tools (lab/durable_inventory.py, lab/durable_lifecycle.py). */
export type PythonRun=(script:string,args:string[],input?:string)=>Promise<{code:number;stdout:string;stderr:string}>;
const REASON=/^[a-z_]{1,40}(:[a-z]{1,12})?$/;

/** Longest one python run may take. The derivation reads docker and the database; an effect makes several calls of at most
 * a minute each. A run that outlives it is killed and fails the operation closed: every effect journals its intent first, so
 * the operation stays pending and is repeated safely. */
export const DERIVE_TIMEOUT_MS=120_000,EFFECT_TIMEOUT_MS=300_000;
export function pythonRunner(timeoutMs:number):PythonRun {
 return async(script,args,input)=>{
  const child=Bun.spawn(['/usr/bin/python3',script,...args],{stdin:input===undefined?'ignore':'pipe',stdout:'pipe',stderr:'pipe',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let timedOut=false;const timer=setTimeout(()=>{timedOut=true;child.kill('SIGKILL');},timeoutMs);
  try{
   if(input!==undefined){child.stdin!.write(input);child.stdin!.end();}
   const [stdout,stderr,code]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text(),child.exited]);
   if(timedOut)throw new Error('Python run timed out');
   return {code,stdout,stderr};
  }finally{clearTimeout(timer);if(child.exitCode===null)child.kill('SIGKILL');}
 };
}

/** Derives an environment's exact inventory from durable state on this host, or refuses with a closed reason. */
export function durableInventoryDeriver(checkout:string,run:PythonRun=pythonRunner(DERIVE_TIMEOUT_MS)):(runtime:string,installation:string)=>Promise<LifecycleResource[]> {
 return async(runtime,installation)=>{
  let result:Awaited<ReturnType<PythonRun>>;
  try{result=await run(resolve(checkout,'lab/durable_inventory.py'),['derive','--runtime',runtime,'--installation',installation]);}
  catch{throw new InventoryRefusal('observation_unavailable');}
  let value:any;try{value=JSON.parse(result.stdout);}catch{throw new InventoryRefusal('observation_unavailable');}
  if(!value||typeof value!=='object')throw new InventoryRefusal('observation_unavailable');
  if(value.ok!==true)throw new InventoryRefusal(typeof value.reason==='string'&&REASON.test(value.reason)?value.reason:'observation_unavailable');
  if(value.coverage!=='durable-environment'||!Array.isArray(value.resources))throw new InventoryRefusal('observation_unavailable');
  return value.resources;
 };
}

/** The adapter of the durable runtime: every effect is one python run that takes the operation lock and
 * refuses unless the catalog durably records the same operation, epoch, state and resource. */
export function durableAdapter(checkout:string,catalogPath:string,run:PythonRun=pythonRunner(EFFECT_TIMEOUT_MS)):LifecycleAdapter {
 return async input=>{
  const result=await run(resolve(checkout,'lab/durable_lifecycle.py'),['--catalog',catalogPath],JSON.stringify(input));
  if(result.code!==0)throw new Error('Resource adapter refused');
  const value=JSON.parse(result.stdout);
  if(!value||typeof value!=='object'||!['present','absent','quarantined','restored','purged'].includes(value.outcome))throw new Error('Invalid resource adapter result');
  return value;
 };
}

/** Runs the next pending durable operation, if any. A blocked operation keeps its recorded failure and is asked again
 * only after `retryMs`, so a refusal that needs an operator is not hammered. Returns whether one was attempted.
 * The native authority store (the retained original Auth, which an operator's request captured at the call) is installed
 * lazily and only for a pending operation, so a host where it is unavailable still provisions and serves. */
export async function runNextDurableOperation(catalog:Catalog,adapter:LifecycleAdapter,attempts:Map<string,number>,
 options:{retryMs?:number;now?:()=>number;store?:()=>LifecycleAuthorizationStore}={}):Promise<boolean> {
 const row=catalog.nextLifecycleOperation('durable');
 if(!row)return false;
 const now=options.now??Date.now,last=attempts.get(row.operation);
 if(last!==undefined&&now()-last<(options.retryMs??60_000))return false;
 attempts.set(row.operation,now());
 try{
  if(options.store&&!catalog.lifecycleAuthorizationStore)catalog.lifecycleAuthorizationStore=options.store();
  await runLifecycleOperation(catalog,adapter,row,async()=>true);attempts.delete(row.operation);
 }catch(error){console.error(`Lifecycle ${row.state} of ${row.environment} blocked: ${error instanceof Error?error.message:'failed'}`);}
 return true;
}
