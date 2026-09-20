import {closeSync,fsyncSync,openSync,readFileSync,unlinkSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {Catalog} from '../src/control/catalog';

/** Caller holds the worker lock. Commit the exact result before consuming it. */
export function settleWorkerReceipt(catalog:Catalog,lockPath:string):'absent'|'succeeded'|'refused' {
 const path=join(dirname(lockPath),'worker-effect.json');
 let text:string;
 try{text=readFileSync(path,'utf8');}catch(error){
  if((error as NodeJS.ErrnoException).code==='ENOENT')return 'absent';
  throw new Error('Cannot read provisioning outcome');
 }
 let receipt;
 try{receipt=JSON.parse(text);}catch{throw new Error('Invalid provisioning outcome');}
 if(!receipt||receipt.version!==1||typeof receipt.token!=='string'||!receipt.token||
    !receipt.job||typeof receipt.job!=='object')throw new Error('Invalid provisioning outcome');
 const job=receipt.job;
 if(['environment','runtime','claim'].some(key=>typeof job[key]!=='string'||!job[key])||
    !Number.isInteger(job.attempt)||job.attempt<1)throw new Error('Invalid provisioning identity');
 if(receipt.phase!=='completed'||![0,75].includes(receipt.exitCode))
  throw new Error('Provisioning outcome unresolved; explicit reconciliation required');
 catalog.applyProvisionReceipt(job.environment,job.runtime,job.claim,job.attempt,receipt.exitCode);
 unlinkSync(path);
 const directory=openSync(dirname(path),'r');
 try{fsyncSync(directory);}finally{closeSync(directory);}
 return receipt.exitCode===0?'succeeded':'refused';
}
