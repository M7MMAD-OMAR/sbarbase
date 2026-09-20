/** Retain the same flock description until the direct provisioning effect exits. */
export function spawnWorkerEffect(command:string[],lockFd:number,lockPath:string) {
 if(!Number.isInteger(lockFd)||lockFd<3)throw new Error('Invalid worker lock descriptor');
 return Bun.spawn(['/usr/bin/python3','lab/worker_lock_exec.py',lockPath,...command],{
  stdio:['ignore','ignore','ignore',lockFd],
  env:{...process.env,SBARBASE_WORKER_FD:'3'},
 });
}
