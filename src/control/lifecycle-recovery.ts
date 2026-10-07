import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import type {LifecycleAdmission} from './lifecycle-contract';

/** Explicit installation input. Verification and its source/material bindings run on every admission. */
export function installedLifecycleAdmission(checkout:string,receipt:string):LifecycleAdmission {
 if(!checkout.startsWith('/')||!receipt.startsWith('/'))throw new Error('Explicit recovery verifier paths required');
 return (binding,sourceInventory)=>{
  const child=spawnSync('/usr/bin/python3',[resolve(checkout,'lab/lifecycle_recovery.py'),'--checkout',checkout,'--receipt',receipt],
   {input:JSON.stringify({binding,sourceInventory}),encoding:'utf8',timeout:30_000,maxBuffer:64*1024});
  if(child.error||child.status!==0)return null;
  try{const value=JSON.parse(child.stdout).receipt;return typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)?value:null;}catch{return null;}
 };
}
