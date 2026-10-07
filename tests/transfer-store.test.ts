import {afterEach,expect,test} from 'bun:test';
import {Database} from 'bun:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {TransferInventoryStore,type InventoryMutation,type InventoryCandidate,type InventorySnapshot,
 type TransferInventoryPorts,type InventoryVerification,type VerifiedInventoryPublication} from '../src/control/transfer-store';

const runtime='e_0123456789abcdef01234567',environment='11111111-1111-4111-8111-111111111111';
const installation='22222222-2222-4222-8222-222222222222',operation='33333333-3333-4333-8333-333333333333';
const sha=(value:string)=>createHash('sha256').update(value).digest('hex');
const databases:Database[]=[];
const directories:string[]=[];
afterEach(()=>{for(const db of databases.splice(0))db.close();for(const dir of directories.splice(0))rmSync(dir,{recursive:true,force:true});});
function resources(cid:string):InventoryCandidate {
 const inventory=JSON.stringify([{type:'container',id:cid,runtime}]);
 return {inventory,inventoryDigest:sha(inventory),coverage:'dedicated-resources',placementDigest:'d'.repeat(64)};
}
function fixture(path=':memory:') {
 const db=new Database(path);databases.push(db);db.exec('PRAGMA foreign_keys=ON');
 db.exec('CREATE TABLE ownership(owner TEXT NOT NULL); INSERT INTO ownership VALUES (\'source\')');
 const original=resources('a'.repeat(64)),next={...resources('b'.repeat(64)),placementDigest:'e'.repeat(64)};
 const initial:InventorySnapshot={runtime,environment,installation,generation:0,epoch:4,digestVersion:'sb07-json-v1',...original};
 const mutation:InventoryMutation={operation,runtime,environment,installation,sourceGeneration:0,sourceEpoch:4,heldEpoch:5,
  previousDigest:original.inventoryDigest,sourcePlacementDigest:original.placementDigest,heldPlacementDigest:'f'.repeat(64)};
 let guarded=true,adoptable=true,observations=0,proof='1'.repeat(64);
 let verifier:TransferInventoryPorts['verifyReplacement']=async({mutation,candidate})=>{
  observations++;return {...mutation,candidateDigest:candidate.inventoryDigest,candidatePlacementDigest:candidate.placementDigest,
   coverage:candidate.coverage,proofDigest:proof};
 };
 const ports:TransferInventoryPorts={assertAdoption:()=>{if(!adoptable)throw new Error('private diagnostic');},
  assertMutation:()=>{if(!guarded)throw new Error('private diagnostic');},
  validateManifest:(bound,inventory)=>{if(JSON.parse(inventory).some((row:any)=>row.runtime!==bound))throw new Error('crossed manifest');},
  verifyReplacement:request=>verifier(request)};
 const store=new TransferInventoryStore(db,ports);
 const adopt=()=>db.transaction(()=>store.adoptInTransaction(initial))();
 const append=(cap:VerifiedInventoryPublication)=>db.transaction(()=>store.appendInTransaction(cap))();
 return {db,store,ports,initial,mutation,next,adopt,append,guard:(value:boolean)=>{guarded=value;},
  admission:(value:boolean)=>{adoptable=value;},verify:(value:typeof verifier)=>{verifier=value;},
  proof:(value:string)=>{proof=value;},observations:()=>observations};
}

test('adoption and publication require the surrounding Catalog transaction',async()=>{
 const f=fixture();expect(()=>f.store.adoptInTransaction(f.initial)).toThrow('CATALOG_TRANSACTION_REQUIRED');
 f.adopt();const cap=await f.store.verifyPublication(f.mutation,f.next);
 expect(()=>f.store.appendInTransaction(cap)).toThrow('CATALOG_TRANSACTION_REQUIRED');
});
test('ownership and inventory publication roll back in one connection',async()=>{
 const f=fixture();f.adopt();const cap=await f.store.verifyPublication(f.mutation,f.next);
 expect(()=>f.db.transaction(()=>{
  f.store.appendInTransaction(cap);f.db.query('UPDATE ownership SET owner=?').run('destination');throw new Error('late refusal');
 })()).toThrow('late refusal');
 expect(f.store.head(runtime)?.generation).toBe(0);expect(f.store.history(runtime)).toHaveLength(1);
 expect(f.db.query('SELECT owner FROM ownership').get()).toEqual({owner:'source'});
});
test('published generations retain exact retired inventory and advance once',async()=>{
 const f=fixture();const initial=f.adopt(),cap=await f.store.verifyPublication(f.mutation,f.next);
 const published=f.append(cap);expect(f.append(cap)).toEqual(published);expect(f.store.history(runtime)).toEqual([initial,published]);
 expect(published.previousDigest).toBe(initial.inventoryDigest);expect(published.epoch).toBe(5);
});
test('history update and deletion cannot rewrite retired native identities',()=>{
 const f=fixture();f.adopt();
 expect(()=>f.db.exec('UPDATE transfer_inventory_generations SET inventory=\'[]\'')).toThrow('immutable');
 expect(()=>f.db.exec('DELETE FROM transfer_inventory_generations')).toThrow('immutable');
});
test('source generation and source epoch have separate monotonic meanings',async()=>{
 const f=fixture();f.adopt();const cap=await f.store.verifyPublication({...f.mutation,sourceEpoch:9,heldEpoch:10},f.next);
 const row=f.append(cap);expect(row.generation).toBe(1);expect(row.epoch).toBe(10);
});
test('stale generation digest placement and physical scope refuse before native verification',async()=>{
 const f=fixture();f.adopt();
 for(const changed of [{sourceGeneration:1},{previousDigest:'a'.repeat(64)},{sourcePlacementDigest:'a'.repeat(64)},
  {environment:'44444444-4444-4444-8444-444444444444'},{installation:'55555555-5555-4555-8555-555555555555'},
  {sourceEpoch:3,heldEpoch:4}])
  await expect(f.store.verifyPublication({...f.mutation,...changed},f.next)).rejects.toThrow('INVENTORY_CAS_REFUSED');
 expect(f.observations()).toBe(0);
});
test('guard rejection after awaited native observation prevents publication',async()=>{
 const f=fixture();f.adopt();
 f.verify(async({mutation,candidate})=>{f.guard(false);return {...mutation,candidateDigest:candidate.inventoryDigest,
  candidatePlacementDigest:candidate.placementDigest,coverage:candidate.coverage,proofDigest:'1'.repeat(64)};});
 await expect(f.store.verifyPublication(f.mutation,f.next)).rejects.toThrow('INVENTORY_AUTHORITY_REFUSED');
 expect(f.store.head(runtime)?.generation).toBe(0);
});
test('guard is checked again inside the final ownership transaction',async()=>{
 const f=fixture();f.adopt();const cap=await f.store.verifyPublication(f.mutation,f.next);f.guard(false);
 expect(()=>f.append(cap)).toThrow('INVENTORY_AUTHORITY_REFUSED');expect(f.store.history(runtime)).toHaveLength(1);
});
test('another operation winning the generation CAS invalidates awaited verification',async()=>{
 const f=fixture();f.adopt();const winning=await f.store.verifyPublication({...f.mutation,operation:'66666666-6666-4666-8666-666666666666'},f.next);
 f.verify(async({mutation,candidate})=>{f.append(winning);return {...mutation,candidateDigest:candidate.inventoryDigest,
  candidatePlacementDigest:candidate.placementDigest,coverage:candidate.coverage,proofDigest:'1'.repeat(64)};});
 await expect(f.store.verifyPublication(f.mutation,f.next)).rejects.toThrow('INVENTORY_CAS_REFUSED');
 expect(f.store.history(runtime)).toHaveLength(2);
});
test('plain JSON copied capabilities and capabilities from another store cannot publish',async()=>{
 const f=fixture();f.adopt();const cap=await f.store.verifyPublication(f.mutation,f.next);
 const reopened=new TransferInventoryStore(f.db,f.ports);
 for(const forged of [{...cap},null,undefined,'receipt',JSON.parse(JSON.stringify(cap))])
  expect(()=>f.append(forged as VerifiedInventoryPublication)).toThrow('TRUSTED_INVENTORY_VERIFICATION_REQUIRED');
 expect(()=>f.db.transaction(()=>reopened.appendInTransaction(cap))()).toThrow('TRUSTED_INVENTORY_VERIFICATION_REQUIRED');
});
test('a new store requires fresh native verification and preserves the historical proof',async()=>{
 const f=fixture();f.adopt();const cap=await f.store.verifyPublication(f.mutation,f.next),published=f.append(cap);
 const reopened=new TransferInventoryStore(f.db,f.ports);f.proof('2'.repeat(64));
 const replay=await reopened.verifyPublication(f.mutation,f.next);
 expect(f.observations()).toBe(2);
 expect(f.db.transaction(()=>reopened.appendInTransaction(replay))()).toEqual(published);
 expect(f.store.history(runtime)).toHaveLength(2);expect(published.proofDigest).toBe('1'.repeat(64));
});
test('file-backed close and reopen retries the same generation with a fresh capability',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'sb06-transfer-store-'));directories.push(dir);const path=join(dir,'catalog.sqlite');
 const f=fixture(path);f.adopt();const stale=await f.store.verifyPublication(f.mutation,f.next),published=f.append(stale);
 databases.splice(databases.indexOf(f.db),1);f.db.close();
 const db=new Database(path);databases.push(db);db.exec('PRAGMA foreign_keys=ON');const reopened=new TransferInventoryStore(db,f.ports);
 expect(()=>db.transaction(()=>reopened.appendInTransaction(stale))()).toThrow('TRUSTED_INVENTORY_VERIFICATION_REQUIRED');
 f.proof('2'.repeat(64));const fresh=await reopened.verifyPublication(f.mutation,f.next);
 expect(db.transaction(()=>reopened.appendInTransaction(fresh))()).toEqual(published);
 expect(reopened.history(runtime)).toHaveLength(2);expect(f.observations()).toBe(2);
});
test('restart replay refuses a reused operation with changed replacement identities',async()=>{
 const f=fixture();f.adopt();f.append(await f.store.verifyPublication(f.mutation,f.next));
 const reopened=new TransferInventoryStore(f.db,f.ports);
 await expect(reopened.verifyPublication(f.mutation,resources('c'.repeat(64)))).rejects.toThrow('INVENTORY_OPERATION_REUSED');
});
test('caller mutations cannot retarget a prepared native verification',async()=>{
 const f=fixture();f.adopt();const request={...f.mutation},next={...f.next};
 const pending=f.store.verifyPublication(request,next);request.operation='77777777-7777-4777-8777-777777777777';next.inventory='[]';
 const row=f.append(await pending);expect(row.operation).toBe(operation);expect(row.inventory).toBe(f.next.inventory);
});
test('native proof must match the exact operation and candidate and contain no extra fields',async()=>{
 for(const changed of [{heldEpoch:6},{candidateDigest:'a'.repeat(64)},{candidatePlacementDigest:'b'.repeat(64)},
  {coverage:'complete-shared-resources'},{proofDigest:'bad'},{secret:'private'}]){
  const f=fixture();f.adopt();f.verify(async({mutation,candidate})=>({...mutation,candidateDigest:candidate.inventoryDigest,
   candidatePlacementDigest:candidate.placementDigest,coverage:candidate.coverage,proofDigest:'1'.repeat(64),...changed}) as InventoryVerification);
  await expect(f.store.verifyPublication(f.mutation,f.next)).rejects.toThrow();expect(f.store.history(runtime)).toHaveLength(1);
 }
});
test('native diagnostics and admission diagnostics remain sanitized',async()=>{
 const f=fixture();f.admission(false);expect(()=>f.adopt()).toThrow('INVENTORY_AUTHORITY_REFUSED');
 f.admission(true);f.adopt();f.verify(async()=>{throw new Error('secret bearer contents');});
 await expect(f.store.verifyPublication(f.mutation,f.next)).rejects.toThrow('NATIVE_INVENTORY_VERIFICATION_REFUSED');
});
test('coverage cannot be upgraded by a caller flag',async()=>{
 const f=fixture();f.adopt();
 await expect(f.store.verifyPublication(f.mutation,{...f.next,coverage:'complete-shared-resources'})).rejects.toThrow('INVENTORY_COVERAGE_CHANGED');
 expect(f.observations()).toBe(0);
});
test('SB07 resource byte ordering remains part of the exact digest',async()=>{
 const f=fixture();f.adopt();const inventory=JSON.stringify([{runtime,id:'b'.repeat(64),type:'container'}]);
 const next={...f.next,inventory,inventoryDigest:sha(inventory)};expect(next.inventoryDigest).not.toBe(f.next.inventoryDigest);
 const row=f.append(await f.store.verifyPublication(f.mutation,next));expect(row.inventory).toBe(inventory);
});
test('duplicate JSON keys whitespace alternate encodings and mismatched digest refuse',async()=>{
 const f=fixture();f.adopt();
 for(const inventory of ['[{"runtime":"'+runtime+'","runtime":"'+runtime+'"}]',' [] ',
  '[{"runtime":"e_0123456789abcdef0123456\\u0037"}]','[]'])
  await expect(f.store.verifyPublication(f.mutation,{...f.next,inventory,inventoryDigest:sha(inventory)})).rejects.toThrow('INVENTORY_JSON_INVALID');
 await expect(f.store.verifyPublication(f.mutation,{...f.next,inventoryDigest:'a'.repeat(64)})).rejects.toThrow('INVENTORY_DIGEST_INVALID');
});
test('unknown crossed resource identities require the installed manifest validator',async()=>{
 const f=fixture();f.adopt();const inventory=JSON.stringify([{runtime:'e_ffffffffffffffffffffffff',type:'container'}]);
 await expect(f.store.verifyPublication(f.mutation,{...f.next,inventory,inventoryDigest:sha(inventory)})).rejects.toThrow('INVENTORY_AUTHORITY_REFUSED');
});
test('missing inventory after effects cannot be reconstructed from a proposed generation',async()=>{
 const f=fixture();
 await expect(f.store.verifyPublication(f.mutation,f.next)).rejects.toThrow('INVENTORY_CAS_REFUSED');
 expect(()=>f.db.transaction(()=>f.store.adoptInTransaction({...f.initial,generation:1}))()).toThrow('INVENTORY_BOOTSTRAP_INVALID');
});
test('native observations cannot run inside the Catalog ownership transaction',async()=>{
 const f=fixture();f.adopt();
 let pending:Promise<VerifiedInventoryPublication>|undefined;
 f.db.transaction(()=>{pending=f.store.verifyPublication(f.mutation,f.next);})();
 await expect(pending!).rejects.toThrow('NATIVE_VERIFICATION_OUTSIDE_TRANSACTION_REQUIRED');
});
test('adoption replay must exactly match the original immutable generation',()=>{
 const f=fixture();const initial=f.adopt();expect(f.adopt()).toEqual(initial);
 expect(()=>f.db.transaction(()=>f.store.adoptInTransaction({...f.initial,epoch:5}))()).toThrow('INVENTORY_BOOTSTRAP_DIFFERS');
});
test('a complete shared coverage label still requires independent adapter authority',()=>{
 const f=fixture();const ports={...f.ports,validateManifest:(_runtime:string,_inventory:string,coverage:string)=>{
  if(coverage==='complete-shared-resources')throw new Error('Shared writer isolation unavailable');
 }};
 const store=new TransferInventoryStore(f.db,ports);
 expect(()=>f.db.transaction(()=>store.adoptInTransaction({...f.initial,coverage:'complete-shared-resources'}))())
  .toThrow('INVENTORY_AUTHORITY_REFUSED');expect(store.history(runtime)).toHaveLength(0);
});
test('async adoption mutation and manifest refusals cannot enter publication',async()=>{
 for(const port of ['assertAdoption','assertMutation','validateManifest'] as const){
  const f=fixture();f.adopt();const ports={...f.ports,[port]:async()=>{throw new Error('late private refusal');}};
  const store=new TransferInventoryStore(f.db,ports);
  if(port==='assertAdoption')expect(()=>f.db.transaction(()=>store.adoptInTransaction(f.initial))()).toThrow('INVENTORY_AUTHORITY_REFUSED');
  else await expect(store.verifyPublication(f.mutation,f.next)).rejects.toThrow('INVENTORY_AUTHORITY_REFUSED');
  expect(store.head(runtime)?.generation).toBe(0);
 }
});
test('final transaction rejects a Promise-based guard even after valid native proof',async()=>{
 const f=fixture();f.adopt();let asyncGuard=false;
 const store=new TransferInventoryStore(f.db,{...f.ports,assertMutation:()=>asyncGuard?Promise.reject(new Error('late refusal')):undefined});
 const cap=await store.verifyPublication(f.mutation,f.next);asyncGuard=true;
 expect(()=>f.db.transaction(()=>store.appendInTransaction(cap))()).toThrow('INVENTORY_AUTHORITY_REFUSED');
 expect(store.history(runtime)).toHaveLength(1);
});
