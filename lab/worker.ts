import {Catalog} from '../src/control/catalog';

// Invoke through worker.py, which holds the installation-wide worker lock.
if(process.env.SBARBASE_WORKER_LOCKED!=='1') throw new Error('Use /usr/bin/python3 lab/worker.py');
const upstream=process.env.SBARBASE_RUNTIME_PROFILE==='upstream';
const catalog=new Catalog(upstream?'.lab/upstream/control.sqlite':'.lab/control.sqlite');
let failed=false;
try {
 catalog.recoverProvisioning();
 while(true) {
  const job=catalog.claimProvision();if(!job) break;
  const command=upstream?['/usr/bin/python3','lab/durable_runtime.py','provision',job.runtime]:['/usr/bin/python3','lab/provision.py',job.runtime];
  const child=Bun.spawn(command,{stdout:'ignore',stderr:'ignore'});
  const ok=(await child.exited)===0;
  catalog.finishProvision(job.environment,job.claim!,ok);
  console.log(`Provision ${job.environment}: ${ok?'succeeded':'failed'}`);
  if(!ok) failed=true;
 }
} finally {catalog.close();}
if(failed) process.exitCode=1;
