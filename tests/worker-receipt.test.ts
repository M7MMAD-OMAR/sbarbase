import {test,expect} from 'bun:test';
import {mkdtempSync,writeFileSync,existsSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {settleWorkerReceipt} from '../lab/worker-receipt';

function fixture(){
 const dir=mkdtempSync(join(tmpdir(),'sbar-effect-')),path=join(dir,'control.sqlite');
 const catalog=new Catalog(path),org=catalog.createOrganization('owner','O'),project=catalog.createProject('owner',org,'P');
 const environment=catalog.createEnvironment('owner',project,'E'),job=catalog.claimProvision()!;
 const receipt={version:1,phase:'completed',token:'receipt-token',exitCode:0,
  job:{environment,runtime:job.runtime,claim:job.claim!,attempt:job.attempt}};
 return {dir,path,catalog,environment,job,receipt,lock:join(dir,'worker.lock'),file:join(dir,'worker-effect.json')};
}

test('successful receipt settles exact claim and crash after catalog commit is idempotent',()=>{
 const f=fixture();
 try{
  writeFileSync(f.file,JSON.stringify(f.receipt));
  f.catalog.applyProvisionReceipt(f.environment,f.job.runtime,f.job.claim!,f.job.attempt,0);
  f.catalog.close();f.catalog=new Catalog(f.path);
  expect(()=>f.catalog.applyProvisionReceipt(f.environment,f.job.runtime,'wrong',f.job.attempt,0)).toThrow('mismatch');
  expect(settleWorkerReceipt(f.catalog,f.lock)).toBe('succeeded');
  expect(f.catalog.getProvision('owner',f.environment).state).toBe('succeeded');
  expect(existsSync(f.file)).toBe(false);
  expect(settleWorkerReceipt(f.catalog,f.lock)).toBe('absent');
 }finally{f.catalog.close();rmSync(f.dir,{recursive:true,force:true});}
});

test('uncertain corrupt and mismatched receipts preserve running claim and prevent settlement',()=>{
 const f=fixture();
 try{
  for(const value of [
   '{bad',JSON.stringify({...f.receipt,phase:'pending'}),
   JSON.stringify({...f.receipt,exitCode:1}),
   JSON.stringify({...f.receipt,job:{...f.receipt.job,runtime:'other'}}),
   JSON.stringify({...f.receipt,job:{...f.receipt.job,claim:'other'}}),
   JSON.stringify({...f.receipt,job:{...f.receipt.job,attempt:2}}),
  ]){
   writeFileSync(f.file,value);
   expect(()=>settleWorkerReceipt(f.catalog,f.lock)).toThrow();
   expect(f.catalog.getProvision('owner',f.environment).state).toBe('running');
   expect(existsSync(f.file)).toBe(true);
   expect(()=>f.catalog.retryProvision('owner',f.environment)).toThrow('not retryable');
  }
 }finally{f.catalog.close();rmSync(f.dir,{recursive:true,force:true});}
});

test('known admission refusal settles once and a receipt from an earlier attempt cannot win',()=>{
 const f=fixture();
 try{
  writeFileSync(f.file,JSON.stringify({...f.receipt,exitCode:75}));
  expect(settleWorkerReceipt(f.catalog,f.lock)).toBe('refused');
  expect(f.catalog.getProvision('owner',f.environment).failure).toBe('capacity_exceeded');
  f.catalog.retryProvision('owner',f.environment);f.catalog.claimProvision();
  writeFileSync(f.file,JSON.stringify(f.receipt));
  expect(()=>settleWorkerReceipt(f.catalog,f.lock)).toThrow('mismatch');
  expect(f.catalog.getProvision('owner',f.environment).state).toBe('running');
 }finally{f.catalog.close();rmSync(f.dir,{recursive:true,force:true});}
});

test('claimed work without a published receipt is recoverable before any effect launch',()=>{
 const f=fixture();
 try{
  expect(settleWorkerReceipt(f.catalog,f.lock)).toBe('absent');
  f.catalog.recoverProvisioning();
  const retry=f.catalog.claimProvision()!;
  expect(retry.runtime).toBe(f.job.runtime);expect(retry.attempt).toBe(2);
 }finally{f.catalog.close();rmSync(f.dir,{recursive:true,force:true});}
});

test('committed historical refusal can be consumed after API retry without changing newer work',()=>{
 const f=fixture();
 try{
  f.catalog.applyProvisionReceipt(f.environment,f.job.runtime,f.job.claim!,f.job.attempt,75);
  f.catalog.retryProvision('owner',f.environment);
  const next=f.catalog.claimProvision()!;
  writeFileSync(f.file,JSON.stringify({...f.receipt,exitCode:75}));
  expect(settleWorkerReceipt(f.catalog,f.lock)).toBe('refused');
  expect(f.catalog.getProvision('owner',f.environment).claim).toBe(next.claim);
  expect(f.catalog.getProvision('owner',f.environment).attempt).toBe(2);
  expect(f.catalog.getProvision('owner',f.environment).state).toBe('running');
 }finally{f.catalog.close();rmSync(f.dir,{recursive:true,force:true});}
});
