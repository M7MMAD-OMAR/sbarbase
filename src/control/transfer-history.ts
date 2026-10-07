import {Database} from 'bun:sqlite';
import {createHash} from 'node:crypto';
import type {PasswordIdentity} from './auth';
import {validatePlacement} from './placement';
import {TransferInventoryStore,type InventoryGeneration} from './transfer-store';

export type TransferHistoryQuery={installation:string;project:string;environment:string;runtime:string;operation:string;
 managementEpoch:number;sourceGeneration:number;sourceEpoch:number;sourceInventoryDigest:string;sourcePlacementDigest:string};
export type CurrentTransferHistory={query:Readonly<TransferHistoryQuery>;actor:string;organization:string;runtimeEpoch:number;
 sessionDigest:string;authenticationExpiresAt:number;
 routingRevision:number;heldPlacementDigest:string;head:Readonly<InventoryGeneration>;source:Readonly<InventoryGeneration>;
 generations:readonly Readonly<InventoryGeneration>[];bindingDigest:string;nativeRetirementAdmitted:false};
export type CurrentHistoryProof={authority:string;ledgerRevision:number;ledgerDigest:string;bindingDigest:string;
 proofDigest:string;expiresAt:number};
export type TransferHistoryPorts={
 // Startup-installed original management Auth verifier, never caller claims.
 identify:(request:Request)=>Promise<PasswordIdentity|null>;
 // Independently trusted live Catalog/history authority, never a restored Catalog or backup receipt.
 authority:string;
 verifyCurrentHistory:(snapshot:Readonly<CurrentTransferHistory>)=>Promise<CurrentHistoryProof>;
 assertCurrentHistory:(proof:Readonly<CurrentHistoryProof>)=>void;
};
export type CurrentHistoryCapability=Readonly<{operation:string;runtime:string;bindingDigest:string;nativeRetirementAdmitted:false}>;
export class TransferHistoryError extends Error {}
function fail(code:string):never {throw new TransferHistoryError(code);}
const sha=(value:string)=>createHash('sha256').update(value).digest('hex');
const exact=(value:unknown,re:RegExp)=>typeof value==='string'&&re.exec(value)?.[0]===value;
const uuid=(value:unknown)=>exact(value,/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/)&&value!=='00000000-0000-0000-0000-000000000000';
const digest=(value:unknown)=>exact(value,/^[a-f0-9]{64}$/);
const whole=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
function keys(value:unknown,expected:string[]):void {
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==expected.length||
  Object.keys(value).some(key=>!expected.includes(key)))fail('HISTORY_SHAPE_INVALID');
}
function admitted(input:TransferHistoryQuery):Readonly<TransferHistoryQuery> {
 keys(input,['installation','project','environment','runtime','operation','managementEpoch','sourceGeneration','sourceEpoch','sourceInventoryDigest','sourcePlacementDigest']);
 if(!['installation','project','environment','operation'].every(key=>uuid(input[key as keyof TransferHistoryQuery]))||
  !exact(input.runtime,/^e_[a-f0-9]{24}$/)||!whole(input.managementEpoch)||!whole(input.sourceGeneration)||
  !whole(input.sourceEpoch)||!digest(input.sourceInventoryDigest)||!digest(input.sourcePlacementDigest))fail('HISTORY_QUERY_INVALID');
 return Object.freeze({...input});
}
function synchronous(work:()=>unknown):void {
 try{const result=work();if(result!==undefined){
  if(result&&typeof (result as any).then==='function')Promise.resolve(result).catch(()=>{});
  fail('CURRENT_HISTORY_AUTHORITY_REFUSED');
 }}catch{fail('CURRENT_HISTORY_AUTHORITY_REFUSED');}
}

/** Authenticated history consultation only. Original native rekey/retirement remains separate. */
export class CurrentTransferHistoryQuery {
 readonly #capabilities=new WeakMap<object,{snapshot:Readonly<CurrentTransferHistory>;identity:PasswordIdentity;proof:Readonly<CurrentHistoryProof>;tokenExpiresAt:number}>();
 readonly #ports:Readonly<TransferHistoryPorts>;
 constructor(private readonly db:Database,private readonly inventory:TransferInventoryStore,ports:TransferHistoryPorts) {
  if(!inventory.usesConnection(db))fail('LIVE_CATALOG_CONNECTION_REQUIRED');
  if(!ports||!uuid(ports.authority)||['identify','verifyCurrentHistory','assertCurrentHistory'].some(key=>typeof (ports as any)[key]!=='function'))
   fail('CURRENT_HISTORY_PROVIDER_UNINSTALLED');
  this.#ports=Object.freeze({...ports});
 }
 #identity(identity:PasswordIdentity|null):PasswordIdentity {
  const now=Math.floor(Date.now()/1000);
  if(!identity||!uuid(identity.actor)||!uuid(identity.session)||identity.aal!=='aal2'||!whole(identity.passwordAt)||
   !whole(identity.verifiedAt)||identity.passwordAt<1||identity.passwordAt>now||now-identity.passwordAt>=43200||
   identity.verifiedAt<1||identity.verifiedAt>now||!Array.isArray(identity.factors)||identity.factors.length>16||
   identity.factors.some(factor=>!factor||!uuid(factor.id)||typeof factor.status!=='string'||typeof factor.factor_type!=='string'))
   fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
  return {...identity,factors:identity.factors.map(factor=>({...factor}))};
 }
 #tokenExpiry(header:string,identity:PasswordIdentity):number {
  // The installed original Auth verifier has already validated this exact token.
  // Reading its expiry never replaces that native verification or grants MFA.
  try{
   const parts=header.slice(7).split('.');if(parts.length!==3)fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
   const claims=JSON.parse(Buffer.from(parts[1]!,'base64url').toString('utf8'));
   if(claims.sub!==identity.actor||claims.session_id!==identity.session||!whole(claims.exp)||
    claims.exp>Math.floor(Number.MAX_SAFE_INTEGER/1000)||claims.exp*1000<=Date.now())fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
   return claims.exp*1000;
  }catch{fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');}
 }
 #read(query:Readonly<TransferHistoryQuery>,identity:PasswordIdentity,tokenExpiresAt:number):Readonly<CurrentTransferHistory> {
  if(!this.db.inTransaction)fail('CATALOG_TRANSACTION_REQUIRED');
  try {
   const now=Math.floor(Date.now()/1000),verified=this.#identity(identity);
   if(tokenExpiresAt<=Date.now())fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
   const grant=this.db.query<any,any>(`SELECT g.factor,g.expires,g.epoch,COALESCE(e.epoch,0) current_epoch,COALESCE(e.revoked,0) revoked
    FROM management_mfa_grant g LEFT JOIN management_mfa_epoch e ON e.actor=g.actor
    WHERE g.actor=? AND g.session=? AND g.verified=?`).get(verified.actor,verified.session,verified.verifiedAt);
   if(!grant||!whole(grant.expires)||grant.expires>Math.floor(Number.MAX_SAFE_INTEGER/1000)||grant.expires<=now||
    !whole(grant.revoked)||verified.verifiedAt<=grant.revoked||grant.epoch!==query.managementEpoch||grant.current_epoch!==query.managementEpoch||
    !verified.factors.some(factor=>factor.id===grant.factor&&factor.status==='verified'&&factor.factor_type==='totp'))
    fail('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
   const live=this.db.query<any,any>(`SELECT p.organization,e.project,j.runtime,j.state provision_state,
    m.role,l.state,l.epoch,l.operation,l.actor,l.management_epoch,l.inventory,l.coverage
    FROM environments e JOIN projects p ON p.id=e.project JOIN provision_jobs j ON j.environment=e.id
    JOIN memberships m ON m.organization=p.organization AND m.actor=?
    JOIN environment_lifecycle l ON l.environment=e.id WHERE e.id=?`).get(verified.actor,query.environment);
   if(!live||!uuid(live.organization)||live.project!==query.project||live.runtime!==query.runtime||live.role!=='owner'||live.provision_state!=='succeeded'||
    live.state!=='restoring'||live.operation!==query.operation||live.actor!==verified.actor||
    live.management_epoch!==query.managementEpoch||!whole(live.epoch))fail('CURRENT_OPERATION_AUTHORITY_REFUSED');
   const size=this.db.query<any,any>(`SELECT COUNT(*) count,COALESCE(SUM(LENGTH(CAST(inventory AS BLOB))),0) bytes
    FROM transfer_inventory_generations WHERE runtime=?`).get(query.runtime);
   if(!size||size.count<1||size.count>128||size.bytes>2*1024*1024)fail('CURRENT_HISTORY_INCOMPLETE');
   const rows=this.inventory.history(query.runtime),head=this.inventory.head(query.runtime);
   if(!head||head.generation!==rows.length-1||head.generation<query.sourceGeneration||head.epoch>live.epoch||
    head.inventory!==live.inventory||head.coverage!==live.coverage)fail('CURRENT_HISTORY_INCOMPLETE');
   for(let index=0;index<rows.length;index++){
    const row=rows[index]!,previous=rows[index-1];
    const manifest=JSON.parse(row.inventory);
    if(row.generation!==index||row.runtime!==query.runtime||row.environment!==query.environment||row.installation!==query.installation||
     !whole(row.epoch)||row.digestVersion!=='sb07-json-v1'||!digest(row.inventoryDigest)||!digest(row.placementDigest)||
     Buffer.byteLength(row.inventory)>128*1024||!Array.isArray(manifest)||manifest.length<1||manifest.length>100||
     !['dedicated-resources','complete-shared-resources','disposable-fixture'].includes(row.coverage)||
     sha(row.inventory)!==row.inventoryDigest||JSON.stringify(manifest)!==row.inventory||
     (index===0?(row.operation!==null||row.previousDigest!==null||row.proofDigest!==null):
      (!uuid(row.operation)||!digest(row.proofDigest)||row.previousDigest!==previous!.inventoryDigest||row.epoch<previous!.epoch)))
     fail('CURRENT_HISTORY_INCOMPLETE');
   }
   const source=rows[query.sourceGeneration]!;
   if(source.epoch!==query.sourceEpoch||source.inventoryDigest!==query.sourceInventoryDigest||
    source.placementDigest!==query.sourcePlacementDigest)fail('CLAIMED_SOURCE_HISTORY_REFUSED');
   const routing=this.db.query<any,any>('SELECT revision,maintenance,placement FROM runtime_routing WHERE runtime=?').get(query.runtime);
   if(!routing||!whole(routing.revision)||routing.maintenance!==1)fail('CURRENT_RUNTIME_FENCE_REQUIRED');
   const placement=routing.placement===null?null:validatePlacement(JSON.parse(routing.placement));
   const heldPlacementDigest=sha(JSON.stringify({revision:routing.revision,maintenance:true,placement}));
   const generations=Object.freeze(rows.map(row=>Object.freeze(row)));
   const state={query,actor:verified.actor,organization:live.organization as string,runtimeEpoch:live.epoch as number,
    sessionDigest:sha(verified.session),authenticationExpiresAt:Math.min(tokenExpiresAt,grant.expires*1000),
    routingRevision:routing.revision as number,heldPlacementDigest,head:Object.freeze(head),source:generations[query.sourceGeneration]!,generations};
   return Object.freeze({...state,bindingDigest:sha(JSON.stringify(state)),nativeRetirementAdmitted:false as const});
  }catch(error){if(error instanceof TransferHistoryError)throw error;fail('CURRENT_HISTORY_UNAVAILABLE');}
 }
 async query(request:Request,input:TransferHistoryQuery):Promise<{capability:CurrentHistoryCapability;history:Readonly<CurrentTransferHistory>}> {
  if(this.db.inTransaction)fail('LIVE_AUTHENTICATION_OUTSIDE_TRANSACTION_REQUIRED');
  const query=admitted(input),header=request.headers.get('authorization');
  if(!header||header.length>8192||!/^Bearer \S+$/i.test(header))fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
  const auth=()=>new Request('http://transfer-history.invalid/query',{headers:{authorization:header!}});
  let identity:PasswordIdentity;
  try{identity=this.#identity(await this.#ports.identify(auth()));}catch{fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');}
  const tokenExpiresAt=this.#tokenExpiry(header!,identity!);
  const snapshot=this.db.transaction(()=>this.#read(query,identity!,tokenExpiresAt)).immediate();
  let proof:CurrentHistoryProof;
  try{proof=await this.#ports.verifyCurrentHistory(snapshot);}catch{fail('CURRENT_HISTORY_AUTHORITY_REFUSED');}
  keys(proof!,['authority','ledgerRevision','ledgerDigest','bindingDigest','proofDigest','expiresAt']);
  if(proof!.authority!==this.#ports.authority||!whole(proof!.ledgerRevision)||!digest(proof!.ledgerDigest)||
   proof!.bindingDigest!==snapshot.bindingDigest||!digest(proof!.proofDigest)||!whole(proof!.expiresAt)||proof!.expiresAt<=Date.now())
   fail('CURRENT_HISTORY_PROOF_REFUSED');
  proof=Object.freeze({...proof!});
  let fresh:PasswordIdentity;
  try{fresh=this.#identity(await this.#ports.identify(auth()));}catch{fail('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');}
  if(fresh!.actor!==identity!.actor||fresh!.session!==identity!.session||fresh!.verifiedAt!==identity!.verifiedAt)
   fail('CURRENT_MANAGEMENT_AUTHORITY_CHANGED');
  this.db.transaction(()=>{
   synchronous(()=>this.#ports.assertCurrentHistory(proof));
   if(proof.expiresAt<=Date.now()||this.#read(query,fresh!,tokenExpiresAt).bindingDigest!==snapshot.bindingDigest)fail('CURRENT_HISTORY_CHANGED');
  }).immediate();
  const capability=Object.freeze({operation:query.operation,runtime:query.runtime,bindingDigest:snapshot.bindingDigest,nativeRetirementAdmitted:false as const});
  this.#capabilities.set(capability,{snapshot,identity:fresh!,proof,tokenExpiresAt});
  return {capability,history:snapshot};
 }
 /** Call immediately before effects/publication in the same current Catalog transaction. */
 assertCurrentInTransaction(capability:CurrentHistoryCapability):Readonly<CurrentTransferHistory> {
  if(!this.db.inTransaction)fail('CATALOG_TRANSACTION_REQUIRED');
  const held=capability&&typeof capability==='object'?this.#capabilities.get(capability):undefined;
  if(!held)fail('AUTHENTICATED_HISTORY_CAPABILITY_REQUIRED');
  synchronous(()=>this.#ports.assertCurrentHistory(held!.proof));
  if(held!.proof.expiresAt<=Date.now())fail('CURRENT_HISTORY_PROOF_EXPIRED');
  const fresh=this.#read(held!.snapshot.query,held!.identity,held!.tokenExpiresAt);
  if(fresh.bindingDigest!==held!.snapshot.bindingDigest)fail('CURRENT_HISTORY_CHANGED');
  return fresh;
 }
}
