import {Database} from 'bun:sqlite';
import {createHash} from 'node:crypto';

/** Metadata CAS only. Installed guard/verifier ports must supply actual Catalog/native authority. */
export type InventorySnapshot={runtime:string;environment:string;installation:string;generation:number;epoch:number;
 digestVersion:'sb07-json-v1';inventory:string;inventoryDigest:string;coverage:string;placementDigest:string};
export type InventoryMutation={operation:string;runtime:string;environment:string;installation:string;
 sourceGeneration:number;sourceEpoch:number;heldEpoch:number;previousDigest:string;
 sourcePlacementDigest:string;heldPlacementDigest:string};
// The source placement is historical, held placement is current fenced authority,
// and candidate placement is the separately observed replacement after rotation.
export type InventoryCandidate={inventory:string;inventoryDigest:string;coverage:string;placementDigest:string};
export type InventoryVerification=InventoryMutation&{candidateDigest:string;candidatePlacementDigest:string;coverage:string;proofDigest:string};
export type InventoryGeneration=InventorySnapshot&{operation:string|null;previousDigest:string|null;proofDigest:string|null};
export type VerifiedInventoryPublication=Readonly<{operation:string;runtime:string;candidateDigest:string}>;
export type TransferInventoryPorts={
 assertAdoption:(snapshot:Readonly<InventorySnapshot>)=>void;
 assertMutation:(mutation:Readonly<InventoryMutation>)=>void;
 validateManifest:(runtime:string,inventory:string,coverage:string)=>void;
 verifyReplacement:(request:Readonly<{mutation:Readonly<InventoryMutation>;previous:Readonly<InventoryGeneration>;
  candidate:Readonly<InventoryCandidate>}>)=>Promise<InventoryVerification>;
};
export class TransferInventoryError extends Error {}
function fail(code:string):never {throw new TransferInventoryError(code);}
const digest=(value:string)=>createHash('sha256').update(value).digest('hex');
const whole=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
const exact=(value:unknown,pattern:RegExp)=>typeof value==='string'&&pattern.exec(value)?.[0]===value;
const uuid=(value:unknown)=>exact(value,/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/)&&value!=='00000000-0000-0000-0000-000000000000';
const sha=(value:unknown)=>exact(value,/^[a-f0-9]{64}$/);
const runtime=(value:unknown)=>exact(value,/^e_[a-f0-9]{24}$/);
const coverage=(value:unknown)=>['dedicated-resources','complete-shared-resources','disposable-fixture'].includes(value as string);
function fields(value:unknown,keys:string[]):asserts value is Record<string,unknown> {
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length||
  Object.keys(value).some(key=>!keys.includes(key)))fail('INVENTORY_SHAPE_INVALID');
}
function candidate(value:InventoryCandidate):Readonly<InventoryCandidate> {
 fields(value,['inventory','inventoryDigest','coverage','placementDigest']);
 if(typeof value.inventory!=='string'||Buffer.byteLength(value.inventory)>128*1024||!sha(value.inventoryDigest)||
  !sha(value.placementDigest)||!coverage(value.coverage)||digest(value.inventory)!==value.inventoryDigest)
  fail('INVENTORY_DIGEST_INVALID');
 let parsed:unknown;
 try{parsed=JSON.parse(value.inventory);}catch{fail('INVENTORY_JSON_INVALID');}
 // Preserve SB07 JSON.stringify byte order and reject duplicate-key or alternate encodings.
 if(!Array.isArray(parsed)||!parsed.length||parsed.length>100||JSON.stringify(parsed)!==value.inventory)
  fail('INVENTORY_JSON_INVALID');
 return Object.freeze({...value});
}
function snapshot(value:InventorySnapshot):Readonly<InventorySnapshot> {
 fields(value,['runtime','environment','installation','generation','epoch','digestVersion','inventory','inventoryDigest','coverage','placementDigest']);
 if(!runtime(value.runtime)||!uuid(value.environment)||!uuid(value.installation)||!whole(value.generation)||!whole(value.epoch)||
  value.digestVersion!=='sb07-json-v1')fail('INVENTORY_BINDING_INVALID');
 candidate({inventory:value.inventory,inventoryDigest:value.inventoryDigest,coverage:value.coverage,placementDigest:value.placementDigest});
 return Object.freeze({...value});
}
function mutation(value:InventoryMutation):Readonly<InventoryMutation> {
 fields(value,['operation','runtime','environment','installation','sourceGeneration','sourceEpoch','heldEpoch','previousDigest','sourcePlacementDigest','heldPlacementDigest']);
 if(!uuid(value.operation)||!runtime(value.runtime)||!uuid(value.environment)||!uuid(value.installation)||
  !whole(value.sourceGeneration)||value.sourceGeneration>=Number.MAX_SAFE_INTEGER||!whole(value.sourceEpoch)||
  value.sourceEpoch>=Number.MAX_SAFE_INTEGER||value.heldEpoch!==value.sourceEpoch+1||
  !sha(value.previousDigest)||!sha(value.sourcePlacementDigest)||!sha(value.heldPlacementDigest))
  fail('INVENTORY_MUTATION_INVALID');
 return Object.freeze({...value});
}

/** Use the exact Catalog connection. Mutation methods require its surrounding transaction. */
export class TransferInventoryStore {
 readonly #prepared=new WeakMap<object,{mutation:Readonly<InventoryMutation>;candidate:Readonly<InventoryCandidate>;proofDigest:string}>();
 constructor(private readonly db:Database,private readonly ports:TransferInventoryPorts) {
  if(!ports||['assertAdoption','assertMutation','validateManifest','verifyReplacement'].some(name=>typeof (ports as any)[name]!=='function'))
   fail('INVENTORY_AUTHORITY_UNAVAILABLE');
  this.ports=Object.freeze({...ports});
  db.exec(`CREATE TABLE IF NOT EXISTS transfer_inventory_generations(
   runtime TEXT NOT NULL,generation INTEGER NOT NULL,environment TEXT NOT NULL,installation TEXT NOT NULL,
   epoch INTEGER NOT NULL,digest_version TEXT NOT NULL,inventory TEXT NOT NULL,inventory_digest TEXT NOT NULL,
   coverage TEXT NOT NULL,placement_digest TEXT NOT NULL,operation TEXT,previous_digest TEXT,proof_digest TEXT,
   PRIMARY KEY(runtime,generation),UNIQUE(runtime,operation));
   CREATE TABLE IF NOT EXISTS transfer_inventory_heads(runtime TEXT PRIMARY KEY,generation INTEGER NOT NULL,
    FOREIGN KEY(runtime,generation) REFERENCES transfer_inventory_generations(runtime,generation));
   CREATE TRIGGER IF NOT EXISTS transfer_inventory_no_update BEFORE UPDATE ON transfer_inventory_generations
    BEGIN SELECT RAISE(ABORT,'Transfer inventory history is immutable'); END;
   CREATE TRIGGER IF NOT EXISTS transfer_inventory_no_delete BEFORE DELETE ON transfer_inventory_generations
    BEGIN SELECT RAISE(ABORT,'Transfer inventory history is immutable'); END;`);
 }
 #authority(work:()=>unknown):void {
  try {
   const result=work();
   // Async refusal cannot be awaited within the final Catalog transaction.
   if(result!==undefined){
    if(result&&typeof (result as any).then==='function')Promise.resolve(result).catch(()=>{});
    fail('INVENTORY_AUTHORITY_REFUSED');
   }
  }catch{fail('INVENTORY_AUTHORITY_REFUSED');}
 }
 /** The history consumer must share this exact live Catalog transaction connection. */
 usesConnection(connection:Database):boolean {return this.db===connection;}
 #transaction():void {if(!this.db.inTransaction)fail('CATALOG_TRANSACTION_REQUIRED');}
 #row(value:any):InventoryGeneration|null {
  if(!value)return null;
  return {runtime:value.runtime,generation:value.generation,environment:value.environment,installation:value.installation,
   epoch:value.epoch,digestVersion:value.digest_version,inventory:value.inventory,inventoryDigest:value.inventory_digest,
   coverage:value.coverage,placementDigest:value.placement_digest,operation:value.operation,
   previousDigest:value.previous_digest,proofDigest:value.proof_digest};
 }
 head(runtimeId:string):InventoryGeneration|null {
  if(!runtime(runtimeId))fail('INVENTORY_BINDING_INVALID');
  return this.#row(this.db.query(`SELECT g.* FROM transfer_inventory_generations g JOIN transfer_inventory_heads h
   ON h.runtime=g.runtime AND h.generation=g.generation WHERE h.runtime=?`).get(runtimeId));
 }
 history(runtimeId:string):InventoryGeneration[] {
  if(!runtime(runtimeId))fail('INVENTORY_BINDING_INVALID');
  return this.db.query('SELECT * FROM transfer_inventory_generations WHERE runtime=? ORDER BY generation').all(runtimeId)
   .map(row=>this.#row(row)!);
 }
 #insert(row:InventoryGeneration):void {
  this.db.query(`INSERT INTO transfer_inventory_generations(runtime,generation,environment,installation,epoch,digest_version,
   inventory,inventory_digest,coverage,placement_digest,operation,previous_digest,proof_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`)
   .run(row.runtime,row.generation,row.environment,row.installation,row.epoch,row.digestVersion,row.inventory,row.inventoryDigest,
    row.coverage,row.placementDigest,row.operation,row.previousDigest,row.proofDigest);
 }
 adoptInTransaction(input:InventorySnapshot):InventoryGeneration {
  this.#transaction();const admitted=snapshot(input);
  if(admitted.generation!==0)fail('INVENTORY_BOOTSTRAP_INVALID');
  return this.db.transaction(()=>{
   this.#authority(()=>this.ports.validateManifest(admitted.runtime,admitted.inventory,admitted.coverage));
   this.#authority(()=>this.ports.assertAdoption(admitted));
   const current=this.head(admitted.runtime);
   if(current){
    if(current.operation!==null||Object.entries(admitted).some(([key,value])=>current[key as keyof InventoryGeneration]!==value))
     fail('INVENTORY_BOOTSTRAP_DIFFERS');
    return current;
   }
   if(this.history(admitted.runtime).length)fail('INVENTORY_HEAD_MISSING');
   const row={...admitted,operation:null,previousDigest:null,proofDigest:null};
   this.#insert(row);
   this.db.query('INSERT INTO transfer_inventory_heads VALUES (?,0)').run(row.runtime);
   return row;
  })();
 }
 #previous(request:Readonly<InventoryMutation>):InventoryGeneration {
  const current=this.head(request.runtime);
  return this.#checkPrevious(current,request);
 }
 #checkPrevious(current:InventoryGeneration|null,request:Readonly<InventoryMutation>):InventoryGeneration {
  if(!current||current.generation!==request.sourceGeneration||current.inventoryDigest!==request.previousDigest||
   current.environment!==request.environment||current.installation!==request.installation||current.epoch>request.sourceEpoch||
   current.placementDigest!==request.sourcePlacementDigest)fail('INVENTORY_CAS_REFUSED');
  return current!;
 }
 #publicationState(request:Readonly<InventoryMutation>,next:Readonly<InventoryCandidate>):
  {previous:InventoryGeneration;existing:InventoryGeneration|null} {
  const existing=this.#row(this.db.query('SELECT * FROM transfer_inventory_generations WHERE runtime=? AND operation=?')
   .get(request.runtime,request.operation));
  if(!existing)return {previous:this.#previous(request),existing:null};
  const head=this.head(request.runtime);
  if(existing.generation!==request.sourceGeneration+1||existing.epoch!==request.heldEpoch||
   existing.environment!==request.environment||existing.installation!==request.installation||existing.digestVersion!=='sb07-json-v1'||
   existing.inventory!==next.inventory||existing.inventoryDigest!==next.inventoryDigest||
   existing.placementDigest!==next.placementDigest||existing.coverage!==next.coverage||
   existing.previousDigest!==request.previousDigest||!sha(existing.proofDigest)||head?.generation!==existing.generation)
   fail('INVENTORY_OPERATION_REUSED');
  const previous=this.#checkPrevious(this.#row(this.db.query('SELECT * FROM transfer_inventory_generations WHERE runtime=? AND generation=?')
   .get(request.runtime,request.sourceGeneration)),request);
  return {previous,existing};
 }
 async verifyPublication(input:InventoryMutation,next:InventoryCandidate):Promise<VerifiedInventoryPublication> {
  if(this.db.inTransaction)fail('NATIVE_VERIFICATION_OUTSIDE_TRANSACTION_REQUIRED');
  const request=mutation(input),admitted=candidate(next);
  this.#authority(()=>this.ports.assertMutation(request));
  this.#authority(()=>this.ports.validateManifest(request.runtime,admitted.inventory,admitted.coverage));
  const previous=Object.freeze(this.#publicationState(request,admitted).previous);
  if(admitted.coverage!==previous.coverage)fail('INVENTORY_COVERAGE_CHANGED');
  let verified:InventoryVerification;
  try{verified=await this.ports.verifyReplacement(Object.freeze({mutation:request,previous,candidate:admitted}));}
  catch{fail('NATIVE_INVENTORY_VERIFICATION_REFUSED');}
  fields(verified!,[...Object.keys(request),'candidateDigest','candidatePlacementDigest','coverage','proofDigest']);
  if(Object.entries(request).some(([key,value])=>verified![key as keyof InventoryVerification]!==value)||
   verified!.candidateDigest!==admitted.inventoryDigest||verified!.candidatePlacementDigest!==admitted.placementDigest||
   verified!.coverage!==admitted.coverage||!sha(verified!.proofDigest))fail('NATIVE_INVENTORY_VERIFICATION_DIFFERS');
  // Native observations cannot retain a stale Catalog guard while awaited work finishes.
  this.#authority(()=>this.ports.assertMutation(request));const settled=this.#publicationState(request,admitted);
  const capability=Object.freeze({operation:request.operation,runtime:request.runtime,candidateDigest:admitted.inventoryDigest});
  // Reopening the store requires fresh native observations while preserving the historical receipt.
  this.#prepared.set(capability,{mutation:request,candidate:admitted,proofDigest:settled.existing?.proofDigest??verified!.proofDigest});
  return capability;
 }
 appendInTransaction(capability:VerifiedInventoryPublication):InventoryGeneration {
  this.#transaction();
  if(!capability||typeof capability!=='object')fail('TRUSTED_INVENTORY_VERIFICATION_REQUIRED');
  const prepared=this.#prepared.get(capability);
  if(!prepared)fail('TRUSTED_INVENTORY_VERIFICATION_REQUIRED');
  const request=prepared.mutation,next=prepared.candidate;
  return this.db.transaction(()=>{
   this.#authority(()=>this.ports.assertMutation(request));
   this.#authority(()=>this.ports.validateManifest(request.runtime,next.inventory,next.coverage));
   const existing=this.#publicationState(request,next).existing;
   if(existing){
    if(existing.proofDigest!==prepared.proofDigest)
     fail('INVENTORY_OPERATION_REUSED');
    return existing;
   }
   this.#previous(request);
   const row:InventoryGeneration={runtime:request.runtime,environment:request.environment,installation:request.installation,
    generation:request.sourceGeneration+1,epoch:request.heldEpoch,digestVersion:'sb07-json-v1',inventory:next.inventory,
    inventoryDigest:next.inventoryDigest,coverage:next.coverage,placementDigest:next.placementDigest,
    operation:request.operation,previousDigest:request.previousDigest,proofDigest:prepared.proofDigest};
   this.#insert(row);
   const result=this.db.query('UPDATE transfer_inventory_heads SET generation=? WHERE runtime=? AND generation=?')
    .run(row.generation,row.runtime,request.sourceGeneration);
   if(result.changes!==1)fail('INVENTORY_CAS_REFUSED');
   return row;
  })();
 }
}
