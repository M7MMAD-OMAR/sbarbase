import {closeSync,existsSync} from 'node:fs';
import {settleWorkerReceipt} from './worker-receipt';
import {spawnWorkerEffect} from './worker-effect';
import {Catalog} from '../src/control/catalog';

// Invoke through worker.py, which holds the installation-wide worker lock.
if(process.env.SBARBASE_WORKER_LOCKED!=='1') throw new Error('Use /usr/bin/python3 lab/worker.py');
const upstream=process.env.SBARBASE_RUNTIME_PROFILE==='upstream';
const settleOnly=process.env.SBARBASE_RECEIPT_ONLY==='1';
const watch=process.env.SBARBASE_WORKER_WATCH==='1';
const catalog=new Catalog(upstream?'.lab/upstream/control.sqlite':'.lab/control.sqlite');
let failed=false,stopping=false;
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
  if(!job) {if(!watch)break;await Bun.sleep(500);continue;}
  const command=upstream?['/usr/bin/python3','lab/durable_runtime.py','provision',job.runtime]:['/usr/bin/python3','lab/provision.py',job.runtime];
  const child=spawnWorkerEffect(command,Number(process.env.SBARBASE_WORKER_FD),lockPath,{environment:job.environment,runtime:job.runtime,claim:job.claim!,attempt:job.attempt});
  activeEffect=child;
  try{await child.exited;}finally{activeEffect=null;}
  const settled=settleWorkerReceipt(catalog,lockPath);
  const ok=settled==='succeeded';
  // An interrupted external effect remains recoverable, not falsely completed.
  if(stopping&&!ok)break;
  if(settled==='absent')catalog.finishProvision(job.environment,job.claim!,false,'runtime_failed');
  console.log(`Provision ${job.environment}: ${ok?'succeeded':'failed'}`);
  if(!ok) failed=true;
 }
} finally {catalog.close();process.off('SIGTERM',stop);process.off('SIGINT',stop);}
if(failed&&!watch) process.exitCode=1;
