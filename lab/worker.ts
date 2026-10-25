import {closeSync,existsSync} from 'node:fs';
import {settleWorkerReceipt} from './worker-receipt';
import {spawnWorkerEffect} from './worker-effect';
import {resolve} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {LifecycleAuthorizationStore} from '../src/control/lifecycle-authority';
import {InventoryRefusal} from '../src/control/lifecycle-contract';
import {durableAdapter,durableInventoryDeriver,runNextDurableOperation} from '../src/control/durable-lifecycle';

// Invoke through worker.py, which holds the installation-wide worker lock.
if(process.env.SBARBASE_WORKER_LOCKED!=='1') throw new Error('Use /usr/bin/python3 lab/worker.py');
const upstream=process.env.SBARBASE_RUNTIME_PROFILE==='upstream';
const settleOnly=process.env.SBARBASE_RECEIPT_ONLY==='1';
const watch=process.env.SBARBASE_WORKER_WATCH==='1';
const catalog=new Catalog(upstream?'.lab/upstream/control.sqlite':'.lab/control.sqlite');
let failed=false,stopping=false;
// The durable runtime's lifecycle: soft delete and restore of an environment run here, under the worker lock, and a
// new environment's exact inventory is recorded right after its provisioning settles (src/control/durable-lifecycle.ts).
const checkout=process.cwd(),durable=upstream?{adapter:durableAdapter(checkout,resolve(checkout,'.lab/upstream/control.sqlite')),derive:durableInventoryDeriver(checkout),attempts:new Map<string,number>()}:undefined;
let activeEffect:ReturnType<typeof spawnWorkerEffect>|null=null;
const stop=()=>{stopping=true;activeEffect?.kill('SIGTERM');};
process.on('SIGTERM',stop);process.on('SIGINT',stop);
const lockPath=upstream?'.lab/upstream/worker.lock':'.lab/worker.lock';
// Written by the supervisor before an update moves the checkout (lab/dev.py begin_drain): claim
// nothing new and exit once the job in hand has settled. A signal cannot ask for that, since
// SIGTERM stops the active effect as well.
const drainPath=upstream?'.lab/upstream/worker-drain':'.lab/worker-drain';
try {
 const operationFd=Number(process.env.SBARBASE_OPERATION_FD);
 if(!Number.isInteger(operationFd)||operationFd<3)throw new Error('Missing operation ownership');
 try{settleWorkerReceipt(catalog,lockPath,true);}finally{closeSync(operationFd);}
 if(!settleOnly)catalog.recoverProvisioning();
 while(!settleOnly&&!stopping) {
  if(watch&&existsSync(drainPath))break;
  const job=catalog.claimProvision();
  if(!job) {
   if(durable&&await runNextDurableOperation(catalog,durable.adapter,durable.attempts,{store:()=>LifecycleAuthorizationStore.installed(checkout)}))continue;
   if(!watch)break;await Bun.sleep(500);continue;
  }
  const command=upstream?['/usr/bin/python3','lab/durable_runtime.py','provision',job.runtime]:['/usr/bin/python3','lab/provision.py',job.runtime];
  const child=spawnWorkerEffect(command,Number(process.env.SBARBASE_WORKER_FD),lockPath,{environment:job.environment,runtime:job.runtime,claim:job.claim!,attempt:job.attempt});
  activeEffect=child;
  try{await child.exited;}finally{activeEffect=null;}
  const settled=settleWorkerReceipt(catalog,lockPath);
  const ok=settled==='succeeded';
  if(ok&&durable) {
   // Facts about a runtime that has just settled are the most certain there will be. A miss is not an error: deletion derives again.
   try{const installation=catalog.installationBootstrap()?.organization;if(installation)catalog.registerDurableInventory(job.runtime,await durable.derive(job.runtime,installation));}
   catch(error){console.error(`Inventory of ${job.environment} not recorded: ${error instanceof InventoryRefusal?error.reason:'failed'}`);}
  }
  // An interrupted external effect remains recoverable, not falsely completed.
  if(stopping&&!ok)break;
  if(settled==='absent')catalog.finishProvision(job.environment,job.claim!,false,'runtime_failed');
  console.log(`Provision ${job.environment}: ${ok?'succeeded':'failed'}`);
  if(!ok) failed=true;
 }
} finally {catalog.close();process.off('SIGTERM',stop);process.off('SIGINT',stop);}
if(failed&&!watch) process.exitCode=1;
