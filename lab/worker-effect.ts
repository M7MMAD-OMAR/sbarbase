/** Retain the same flock description until the direct provisioning effect exits. */
export type EffectIdentity={environment:string;runtime:string;claim:string;attempt:number};
export function spawnWorkerEffect(command:string[],lockFd:number,lockPath:string,identity:EffectIdentity,timeoutSeconds=180) {
 if(!Number.isInteger(lockFd)||lockFd<3)throw new Error('Invalid worker lock descriptor');
 const effectFd=Number(process.env.SBARBASE_EFFECT_FD);
 if(!Number.isInteger(effectFd)||effectFd<3||effectFd===lockFd)throw new Error('Invalid effect lease descriptor');
 if(effectFd===3&&lockFd!==3)throw new Error('Effect descriptor overlaps child mapping; export high descriptors');
 if(!Number.isFinite(timeoutSeconds)||timeoutSeconds<=0||timeoutSeconds>180)throw new Error('Invalid effect deadline');
 return Bun.spawn(['/usr/bin/python3','lab/parent_bound.py',String(process.pid),'/usr/bin/python3','lab/worker_lock_exec.py',lockPath,JSON.stringify(identity),String(timeoutSeconds),...command],{
  stdio:['ignore','ignore','ignore',lockFd,effectFd],
  env:{...process.env,SBARBASE_WORKER_FD:'3',SBARBASE_EFFECT_FD:'4'},
 });
}
