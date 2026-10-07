import {Catalog} from './catalog';
import {NativeLifecycleAuthority,refreshLifecycleAuthorization} from './lifecycle-authority';
import {validateLifecycleCoverage,type LifecycleOperation,type LifecycleResource} from './lifecycle-contract';

export type ResourceAction='inspect'|'quarantine'|'restore'|'purge'|'migrate-shared'|'migrate-service'|'readiness'|'inspect-quarantined';
export type ResourceOutcome={outcome:'present'|'absent'|'quarantined'|'restored'|'purged';reclaimedBytes:number;observedBytes?:number};
export type LifecycleReadiness=(row:LifecycleOperation)=>Promise<boolean>;
export type LifecycleAdapter=(input:{action:ResourceAction;resource:LifecycleResource;runtime:string;epoch:number;operation:string;files?:LifecycleResource;shared?:LifecycleResource;runtimeSecrets?:string})=>Promise<ResourceOutcome>;

/** The caller holds worker ownership. The adapter also holds fresh effect ownership. */
export async function enrollLifecycleResources(catalog:Catalog,adapter:LifecycleAdapter,runtime:string,
 resources:LifecycleResource[],coverage:string):Promise<void> {
 validateLifecycleCoverage(runtime,resources,coverage);
 for(const resource of resources){
  const result=await adapter({action:'inspect',resource,runtime,epoch:catalog.runtimeEpoch(runtime),operation:'enrollment'});
  if(result.outcome!=='present')throw new Error('Ownership enrollment requires present resources');
 }
 catalog.registerLifecycleResources(runtime,resources,coverage);
}

/** A pending effect is durable before external mutation. Replays ask the adapter to reconcile exact identities. */
export async function runLifecycleOperation(catalog:Catalog,adapter:LifecycleAdapter,row:LifecycleOperation,readiness?:LifecycleReadiness):Promise<void> {
 if(catalog.lifecycleAuthorizationStore){
  const authority=await NativeLifecycleAuthority.resume(catalog,catalog.lifecycleAuthorizationStore,row);
  return authority.run(()=>runAuthorizedLifecycleOperation(catalog,adapter,row,readiness));
 }
 return runAuthorizedLifecycleOperation(catalog,adapter,row,readiness);
}
async function runAuthorizedLifecycleOperation(catalog:Catalog,adapter:LifecycleAdapter,row:LifecycleOperation,readiness?:LifecycleReadiness):Promise<void> {
 try {
  validateLifecycleCoverage(row.runtime,row.resources,row.coverage);
  await refreshLifecycleAuthorization(catalog,row);
  catalog.assertLifecycleOperation(row);
  if(!row.resources.length)throw new Error('Exact ownership inventory unavailable');
  const action:ResourceAction=row.state==='deleting'?'quarantine':row.state==='restoring'?'restore':row.state==='purging'?'purge':
   (()=>{throw new Error('Lifecycle operation is not pending');})();
  // Validate the complete inventory before the first effect, including items previously settled.
  const measurements=new Map<string,number>();
  for(const resource of row.resources){
   await refreshLifecycleAuthorization(catalog,row);
   catalog.assertLifecycleOperation(row);
   const result=await adapter({action:action==='purge'&&resource.kind==='shared-database'?'inspect-quarantined':'inspect',resource,runtime:row.runtime,epoch:row.epoch,operation:row.operation});
   await refreshLifecycleAuthorization(catalog,row);
   catalog.assertLifecycleOperation(row);
   const previous=row.effects.find(effect=>effect.resource===resource.resource);
   if(result.outcome==='absent'&&(action!=='purge'||!previous))throw new Error('Retained resource is missing');
   const bytes=previous?.reclaimed_bytes??result.observedBytes??0;
   if(!Number.isSafeInteger(bytes)||bytes<0)throw new Error('Resource measurement unavailable');
   if(action==='purge'&&!previous&&result.observedBytes===undefined)throw new Error('Resource measurement unavailable');
   measurements.set(resource.resource,bytes);
  }
  const ordered=[...row.resources].sort((a,b)=>{
   const rank=(item:LifecycleResource)=>action==='restore'?(item.kind==='shared-database'?0:item.kind==='container'?2:1):
    item.kind==='container'?0:item.kind==='shared-database'?1:item.kind==='volume'?2:3;
   return rank(a)-rank(b);
  });
  for(const resource of ordered){
   await refreshLifecycleAuthorization(catalog,row);
   catalog.assertLifecycleOperation(row);
   if(row.effects.some(effect=>effect.resource===resource.resource&&effect.state==='done'))continue;
   catalog.lifecycleEffect(row,resource.resource,undefined,action==='purge'?measurements.get(resource.resource)!:0);
   const result=await adapter({action,resource,runtime:row.runtime,epoch:row.epoch,operation:row.operation});
   await refreshLifecycleAuthorization(catalog,row);
   catalog.assertLifecycleOperation(row);
   const expected=action==='quarantine'?'quarantined':action==='restore'?'restored':'purged';
   if(result.outcome!==expected&&!(action==='purge'&&result.outcome==='absent')||!Number.isSafeInteger(result.reclaimedBytes)||result.reclaimedBytes<0)
    throw new Error('Resource effect did not establish its postcondition');
   catalog.lifecycleEffect(row,resource.resource,expected,action==='purge'?measurements.get(resource.resource)!:result.reclaimedBytes);
  }
  if(action==='restore'){
   for(const resource of ordered){
    await refreshLifecycleAuthorization(catalog,row);
    catalog.assertLifecycleOperation(row);
    const result=await adapter({action:'readiness',resource,runtime:row.runtime,epoch:row.epoch,operation:row.operation});
    await refreshLifecycleAuthorization(catalog,row);
    catalog.assertLifecycleOperation(row);
    if(result.outcome!=='restored')throw new Error('Restore readiness unavailable');
   }
  }
  if(action==='restore'&&(row.coverage!=='disposable-fixture'||readiness)){
   await refreshLifecycleAuthorization(catalog,row);catalog.assertLifecycleOperation(row);
   if(!readiness||!await readiness(row))throw new Error('Complete runtime readiness unavailable');
   await refreshLifecycleAuthorization(catalog,row);catalog.assertLifecycleOperation(row);
  }
  await refreshLifecycleAuthorization(catalog,row);
  catalog.finishLifecycle(row);
 }catch(error){
  // Persist a bounded, credential-free blocker. Leave the intent for an explicit replay.
  try{catalog.blockLifecycle(row,error instanceof Error&&['Complete recovery proof unavailable','Lifecycle authority revoked','Shared writer isolation unavailable'].includes(error.message)
   ?error.message:'Resource reconciliation required');}catch{}
  throw error;
 }
}
