import {Catalog} from '../src/control/catalog';

// Invoke through worker.py, which holds the installation-wide worker lock.
if(process.env.SBARBASE_WORKER_LOCKED!=='1') throw new Error('Use /usr/bin/python3 lab/worker.py');
const upstream=process.env.SBARBASE_RUNTIME_PROFILE==='upstream';
const watch=process.env.SBARBASE_WORKER_WATCH==='1';
const catalog=new Catalog(upstream?'.lab/upstream/control.sqlite':'.lab/control.sqlite');
let failed=false,stopping=false;
const stop=()=>{stopping=true;};
process.on('SIGTERM',stop);process.on('SIGINT',stop);
try {
 catalog.recoverProvisioning();
 while(!stopping) {
  const job=catalog.claimProvision();
  if(!job) {if(!watch)break;await Bun.sleep(500);continue;}
  const command=upstream?['/usr/bin/python3','lab/durable_runtime.py','provision',job.runtime]:['/usr/bin/python3','lab/provision.py',job.runtime];
  const child=Bun.spawn(command,{stdout:'ignore',stderr:'ignore'});
  const exitCode=await child.exited;
  const ok=exitCode===0;
  // An interrupted external effect remains recoverable, not falsely completed.
  if(stopping&&!ok)break;
  catalog.finishProvision(job.environment,job.claim!,ok,upstream&&exitCode===75?'capacity_exceeded':'runtime_failed');
  console.log(`Provision ${job.environment}: ${ok?'succeeded':'failed'}`);
  if(!ok) failed=true;
 }
} finally {catalog.close();process.off('SIGTERM',stop);process.off('SIGINT',stop);}
if(failed&&!watch) process.exitCode=1;
