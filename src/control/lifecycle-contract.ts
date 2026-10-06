import {createHash} from 'node:crypto';
import type {RuntimeRouting} from './placement';

export const RETENTION_MS=7*24*60*60_000;
export type LifecycleState='active'|'deleting'|'deleted'|'restoring'|'purging'|'purged';
export type LifecycleResource={kind:'container'|'volume'|'directory'|'shared-database';id:string;resource:string;installation:string;
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
export function validateLifecycleCoverage(runtime:string,resources:LifecycleResource[],coverage:string|null):void {
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
