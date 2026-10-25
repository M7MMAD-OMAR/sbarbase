import {createHash} from 'node:crypto';
import type {RuntimeRouting} from './placement';

/** Coverage of an environment of the durable upstream runtime (lab/durable_inventory.py): its exact containers,
 * and its database, logins and Storage tenant inside the shared cluster and process. Delete and restore are
 * soft and destroy nothing; purge is not admitted because nothing proves that only this environment writes the
 * shared PostgreSQL cluster and Storage objects. */
/** An inventory that cannot be derived or is ambiguous. `reason` is a closed, nonsecret token. */
export class InventoryRefusal extends Error{constructor(readonly reason:string){super('Exact ownership inventory unavailable');}}
export const DURABLE_COVERAGE='durable-environment';
/** Coverages whose effects stay recoverable and need no native lifecycle authority beyond the owner's current management epoch. */
export const softCoverage=(coverage:string|null)=>coverage==='disposable-fixture'||coverage===DURABLE_COVERAGE;
export const DURABLE_SERVICES=['auth','rest','realtime','functions','studio','meta'] as const;
export const DURABLE_ROLES=['auth','rest','storage','realtime','studio','developer','cron'] as const;
export const RETENTION_MS=7*24*60*60_000;
export type LifecycleState='active'|'deleting'|'deleted'|'restoring'|'purging'|'purged';
export type LifecycleResource={kind:'container'|'volume'|'directory'|'shared-database'|'durable-container'|'durable-tenant';id:string;durable?:{database:string;roles:string[];storageTenant:string};resource:string;installation:string;
 runtime:string;service?:'auth'|'rest'|'database'|'storage';legacy?:{owner:string;image:string;createdAt:string;configDigest:string;endpoint:string;databaseEngine:string;databaseOid:number};createdAt?:string;device?:number;inode?:number;marker?:string;identity?:{engine:{id:string;owner:string;daemon:string};database:{name:string;oid:number;ownerOid:number};
 files:{id:string;resource:string;device:number;inode:number;marker:string};roles:{name:string;oid:number;login:boolean}[];tenant:{database:'storage_metadata';oid:number;id:string;rowDigest:string;writers:string[]}}};
export type LifecycleEffect={resource:string;state:'pending'|'done';outcome:string|null;reclaimed_bytes:number};
export type LifecycleOperation={environment:string;runtime:string;state:LifecycleState;epoch:number;deleted_at:number|null;
 retain_until:number|null;operation:string;failure:string|null;recovery_receipt:string|null;inventory:string|null;
 coverage:string|null;actor:string|null;management_epoch:number|null;resources:LifecycleResource[];effects:LifecycleEffect[]};
export type LifecycleBinding={environment:string;runtime:string;epoch:number;coverage:string|null;placement:'legacy-shared'|'native-dedicated';inventoryDigest:string;placementDigest:string};
/** Installed verifier callback only. The public API never supplies recovery evidence. */
export type LifecycleAdmission=(binding:LifecycleBinding,sourceInventory:LifecycleResource[])=>string|null;
export function digest(value:unknown):string{return createHash('sha256').update(JSON.stringify(value)).digest('hex');}
export const inventoryDigest=(resources:LifecycleResource[])=>digest(resources);
export const placementDigest=(placement:RuntimeRouting)=>digest(placement);
export function validateResources(runtime:string,resources:LifecycleResource[]):void {
 if(!/^e_[a-f0-9]{24}$/.test(runtime)||!Array.isArray(resources)||!resources.length||resources.length>100)
  throw new Error('Invalid ownership inventory');
 const ids=new Set<string>(),identities=new Set<string>();let installation:string|undefined;
 for(const item of resources){
  if(!item||typeof item!=='object'||!['container','volume','directory','shared-database'].includes(item.kind)||typeof item.id!=='string'||!item.id||
   !/^[a-f0-9-]{36}$/.test(item.resource)||!/^[a-f0-9-]{36}$/.test(item.installation)||item.runtime!==runtime||
   Object.keys(item).some(key=>!['kind','id','resource','installation','runtime','createdAt','device','inode','marker','identity','service','legacy'].includes(key))||
   ids.has(item.resource)||identities.has(item.kind+':'+item.id)||installation&&installation!==item.installation)
   throw new Error('Invalid ownership inventory');
  if(item.kind==='container'&&!/^[a-f0-9]{64}$/.test(item.id)||
   item.kind==='volume'&&(!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}$/.test(item.id)||typeof item.createdAt!=='string'||!item.createdAt)||
   item.kind==='directory'&&(!item.id.startsWith('/')||!Number.isSafeInteger(item.device)||!Number.isSafeInteger(item.inode)||typeof item.marker!=='string'))
   throw new Error('Invalid ownership identity');
  if(item.service!==undefined&&(item.kind!=='container'||!['database','auth','rest','storage'].includes(item.service)))
   throw new Error('Invalid service identity');
  if(item.kind==='shared-database'&&(item.id!==runtime||!item.identity||item.identity.database.name!==runtime||item.identity.tenant.id!==runtime||
   !resources.some(file=>file.kind==='directory'&&file.runtime===runtime&&file.installation===item.installation&&file.id.endsWith('/'+runtime)&&(['id','resource','device','inode','marker'] as const).every(key=>file[key]===item.identity!.files[key]))))
   throw new Error('Shared lifecycle requires exact tenant files');
  ids.add(item.resource);identities.add(item.kind+':'+item.id);installation=item.installation;
 }
}

/** Complete shared writer and file isolation has no admitted verifier contract yet. */
export function validateDurableInventory(runtime:string,resources:LifecycleResource[]):void {
 const bad=()=>{throw new Error('Invalid ownership inventory');};
 if(!/^e_[a-f0-9]{24}$/.test(runtime)||!Array.isArray(resources)||resources.length<3||resources.length>8)bad();
 const ids=new Set<string>(),services=new Set<string>();let tenants=0,installation:string|undefined;
 for(const item of resources){
  if(!item||typeof item!=='object'||item.runtime!==runtime||!/^[a-f0-9-]{36}$/.test(item.resource)||!/^[a-f0-9-]{36}$/.test(item.installation)||
   ids.has(item.resource)||installation&&installation!==item.installation)bad();
  ids.add(item.resource);installation=item.installation;
  const keys=Object.keys(item).sort().join();
  if(item.kind==='durable-container'){
   if(keys!==['id','installation','kind','resource','runtime','service'].sort().join()||!(DURABLE_SERVICES as readonly string[]).includes(item.service!)||
    item.id!=='sbarbase-durable-'+runtime+'-'+item.service||services.has(item.service!))bad();
   services.add(item.service!);
  }else if(item.kind==='durable-tenant'){
   const d=item.durable;
   if(keys!==['durable','id','installation','kind','resource','runtime'].sort().join()||item.id!==runtime||!d||typeof d!=='object'||
    Object.keys(d).sort().join()!=='database,roles,storageTenant'||d.database!==runtime||d.storageTenant!==runtime||!Array.isArray(d.roles)||
    new Set(d.roles).size!==d.roles.length||d.roles.some(name=>typeof name!=='string'||!name.startsWith(runtime+'_')||!(DURABLE_ROLES as readonly string[]).includes(name.slice(runtime.length+1)))||
    ['auth','rest','storage'].some(role=>!d.roles.includes(runtime+'_'+role)))bad();
   tenants++;
  }else bad();
 }
 if(tenants!==1||!services.has('auth')||!services.has('rest'))bad();
}

export function validateLifecycleCoverage(runtime:string,resources:LifecycleResource[],coverage:string|null):void {
 if(coverage===DURABLE_COVERAGE)return validateDurableInventory(runtime,resources);
 validateResources(runtime,resources);
 if(coverage==='complete-shared-resources'||resources.some(item=>item.kind==='shared-database'))
  throw new Error('Shared writer isolation unavailable');
 if(!['dedicated-resources','disposable-fixture'].includes(coverage??''))throw new Error('Unsupported lifecycle coverage');
 if(coverage==='disposable-fixture')return;
 const containers=resources.filter(item=>item.kind==='container');
 if(containers.length!==4||!['database','auth','rest','storage'].every(service=>containers.filter(item=>item.service===service).length===1)||
  !resources.some(item=>item.kind==='volume')||!resources.some(item=>item.kind==='directory'))
  throw new Error('Complete dedicated ownership inventory required');
}
