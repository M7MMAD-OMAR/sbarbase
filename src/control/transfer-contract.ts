/** Public identity and status only. Native secrets belong in the private transfer journal. */
export type TransferState='pending'|'blocked'|'completed';
export type TransferPhase='intent'|'reserved'|'quiescing'|'rotating'|'verifying'|'publishing'|'resuming'|'settled';
export type TransferFailure='authority_revoked'|'inventory_changed'|'native_reconciliation_required'|
 'external_revocation_required'|'storage_write_barrier_unavailable'|'capacity_unavailable';

/** Mirrors the immutable binding in lab/transfer_journal.py. OIDs are canonical decimal strings. */
export type TransferRuntimeBinding={
 operation:string;project:string;source:string;destination:string;environment:string;runtime:string;
 actor:string;management_epoch:number;runtime_epoch:number;inventory_digest:string;
 installation:string;daemon_digest:string;engine_id:string;database_oid:string;
 roles:Record<string,string>;routing_revision:number;
};

export type TransferAuthority={actor:string;managementEpoch:number;authorizedAt:number};
export type TransferEnvironmentStatus={environment:string;runtime:string;epoch:number;
 phase:TransferPhase;state:TransferState;failure:TransferFailure|null};
export type ProjectTransferStatus={operation:string;project:string;source:string;destination:string;
 state:TransferState;phase:TransferPhase;failure:TransferFailure|null;requestedAt:number;updatedAt:number;
 environments:TransferEnvironmentStatus[]};

const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const DIGEST=/^[a-f0-9]{64}$/;
const RUNTIME=/^e_[a-f0-9]{24}$/;
const ROLE_SUFFIXES=['auth','rest','storage','realtime','developer','studio'] as const;
const FIELDS=['operation','project','source','destination','environment','runtime','actor',
 'management_epoch','runtime_epoch','inventory_digest','installation','daemon_digest','engine_id',
 'database_oid','roles','routing_revision'] as const;

function boundedInteger(value:unknown,positive=false):value is number {
 return typeof value==='number'&&Number.isSafeInteger(value)&&value>=(positive?1:0);
}
function matches(value:unknown,pattern:RegExp):value is string {
 return typeof value==='string'&&pattern.exec(value)?.[0]===value;
}
function uuid(value:unknown):value is string {
 return matches(value,UUID)&&value!=='00000000-0000-0000-0000-000000000000';
}
function oid(value:unknown):value is string {
 return matches(value,/^[1-9][0-9]{0,9}$/)&&Number(value)<=4294967295;
}

/** Rejects substitutions before any credential reservation or native effect. */
export function validateTransferBinding(input:unknown):TransferRuntimeBinding {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('Invalid transfer binding');
 const value=input as Record<string,unknown>;
 if(Object.keys(value).length!==FIELDS.length||Object.keys(value).some(key=>!FIELDS.includes(key as typeof FIELDS[number])))
  throw new Error('Invalid transfer binding');
 if(!['operation','project','source','destination','environment','installation'].every(key=>uuid(value[key]))||
  value.source===value.destination||!uuid(value.actor)||!matches(value.runtime,RUNTIME)||
  !['inventory_digest','daemon_digest','engine_id'].every(key=>matches(value[key],DIGEST))||
  !boundedInteger(value.runtime_epoch,true)||!boundedInteger(value.management_epoch)||!boundedInteger(value.routing_revision)||!oid(value.database_oid))
  throw new Error('Invalid transfer binding');
 const roles=value.roles;
 if(!roles||typeof roles!=='object'||Array.isArray(roles))throw new Error('Invalid transfer roles');
 const entries=Object.entries(roles);
 const names=ROLE_SUFFIXES.map(suffix=>value.runtime+'_'+suffix);
 if(entries.length<3||entries.length>names.length||entries.some(([name,value])=>!names.includes(name)||!oid(value))||
  !['auth','rest','storage'].every(suffix=>Object.hasOwn(roles,value.runtime+'_'+suffix))||
  new Set(entries.map(([,value])=>value)).size!==entries.length)
  throw new Error('Invalid transfer roles');
 // Copy every validated field so caller mutation cannot change the admitted identity.
 return Object.freeze({...value,roles:Object.freeze(Object.fromEntries(entries))}) as TransferRuntimeBinding;
}

/** Immutable identity includes initial authority. Resumption authorizes the same material separately. */
export function sameTransferBinding(left:TransferRuntimeBinding,right:TransferRuntimeBinding):boolean {
 const a=validateTransferBinding(left),b=validateTransferBinding(right);
 return FIELDS.every(key=>key==='roles'
  ?Object.keys(a.roles).sort().join('\0')===Object.keys(b.roles).sort().join('\0')&&Object.entries(a.roles).every(([role,oid])=>b.roles[role]===oid)
  :a[key]===b[key]);
}
