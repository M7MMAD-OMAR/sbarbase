import {openSync,closeSync,readSync,fstatSync,lstatSync,constants} from 'node:fs';

export type StorageCredentialPolicy={environment:string;purpose:string;role:string;generation:string;secretSha256:string;enabled:boolean;notBefore:number;expiresAt:number};
export type StorageCredentialPolicies={version:1;credentials:readonly StorageCredentialPolicy[]};
const forbidden=new Set(['anon','authenticated','service_role','postgres','authenticator','storage_control']);
export function validateStorageCredentialPolicies(input:unknown):StorageCredentialPolicies {
 if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('Invalid storage issuer policy');
 const source=input as Record<string,unknown>;
 if(source.version!==1||!Array.isArray(source.credentials)||source.credentials.length>64)throw new Error('Invalid storage issuer policy');
 const seen=new Set<string>();
 const scopeHashes=new Set<string>();
 const credentials=source.credentials.map((item:unknown):StorageCredentialPolicy=>{
  if(!item||typeof item!=='object'||Array.isArray(item))throw new Error('Invalid storage issuer policy');
  const r=item as Record<string,unknown>;
  if(typeof r.environment!=='string'||!/^e_[a-f0-9]{24}$/.test(r.environment)||(r.purpose!=='originals'&&r.purpose!=='evidence')
   ||typeof r.role!=='string'||!/^[a-z][a-z0-9_]{1,62}$/.test(r.role)||forbidden.has(r.role)||/^(pg_|supabase_)/.test(r.role)
   ||r.role!==`fanio_${r.environment.slice(2)}_${r.purpose==='originals'?'original_reader':'evidence_runtime'}`
   ||typeof r.generation!=='string'||!/^[A-Za-z0-9_-]{1,64}$/.test(r.generation)||typeof r.secretSha256!=='string'||!/^[a-f0-9]{64}$/.test(r.secretSha256)
   ||typeof r.enabled!=='boolean'||!Number.isSafeInteger(r.notBefore)||!Number.isSafeInteger(r.expiresAt)||Number(r.notBefore)<0||Number(r.expiresAt)<=Number(r.notBefore))throw new Error('Invalid storage issuer policy');
  const id=r.environment+':'+r.purpose+':'+r.secretSha256;
  if(seen.has(id)||scopeHashes.has(r.secretSha256))throw new Error('Ambiguous storage issuer credential');
  seen.add(id);scopeHashes.add(r.secretSha256);
  return Object.freeze({environment:r.environment,purpose:r.purpose,role:r.role,generation:r.generation,secretSha256:r.secretSha256,enabled:r.enabled,notBefore:Number(r.notBefore),expiresAt:Number(r.expiresAt)});
 });
 return Object.freeze({version:1,credentials:Object.freeze(credentials)});
}

/** One descriptor, bounded read, no symlinks or stale cache. Atomic replacement
 * or in-place modification during a read refuses rather than publishing old policy. */
export function readStorageCredentialPolicies(path:string):StorageCredentialPolicies {
 const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try {
  const before=fstatSync(fd,{bigint:true});
  const safe=(s:typeof before)=>s.isFile()&&s.nlink===1n&&(s.mode&0o777n)===0o600n&&s.uid===BigInt(process.geteuid!())&&s.size<=65536n;
  if(!safe(before))throw new Error('Unsafe storage issuer policy file');
  const buffer=Buffer.alloc(65537);let bytes=0;
  while(bytes<buffer.length){const n=readSync(fd,buffer,bytes,buffer.length-bytes,null);if(!n)break;bytes+=n;}
  const after=fstatSync(fd,{bigint:true}),current=lstatSync(path,{bigint:true});
  const same=(a:typeof before,b:typeof before)=>a.dev===b.dev&&a.ino===b.ino&&a.size===b.size&&a.mtimeNs===b.mtimeNs&&a.ctimeNs===b.ctimeNs&&a.mode===b.mode&&a.uid===b.uid&&a.nlink===b.nlink;
  if(bytes>65536||BigInt(bytes)!==before.size||!safe(after)||!safe(current)||!same(before,after)||!same(after,current))throw new Error('Storage issuer policy changed during read');
  return validateStorageCredentialPolicies(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer.subarray(0,bytes))));
 } finally {closeSync(fd);}
}
