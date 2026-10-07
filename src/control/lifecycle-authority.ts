import {AsyncLocalStorage} from 'node:async_hooks';
import {spawnSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {constants,openSync,closeSync,fstatSync,readFileSync,writeFileSync,fsyncSync,linkSync,unlinkSync,mkdirSync,lstatSync,realpathSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {managementPasswordIdentity,hasManagementMfa,type PasswordIdentity} from './auth';
import type {Catalog} from './catalog';
import type {LifecycleOperation} from './lifecycle-contract';

export type LifecycleRealm={auth:string;key:string;process:string};
export type LifecycleRealmReader=()=>Promise<LifecycleRealm>;
const scope=new AsyncLocalStorage<NativeLifecycleAuthority>();
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const refused=()=>{throw new Error('Lifecycle authority revoked');};
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
type Binding=Pick<LifecycleOperation,'environment'|'runtime'|'operation'|'state'|'epoch'|'inventory'|'coverage'|'actor'|'management_epoch'>;
function binding(row:Binding):Binding {return {environment:row.environment,runtime:row.runtime,operation:row.operation,state:row.state,
 epoch:row.epoch,inventory:row.inventory,coverage:row.coverage,actor:row.actor,management_epoch:row.management_epoch};}
type Material={version:1;binding:Binding;realm:LifecycleRealm;bearer:string;identity:PasswordIdentity;factor:string;epoch:number;catalog:string;catalogIdentity:string};
function fileIdentity(path:string):string {
 const s=lstatSync(path);if(!s.isFile()||s.isSymbolicLink()||s.nlink!==1||s.uid!==process.getuid?.()||(s.mode&0o077))return refused();
 return `${s.dev}:${s.ino}`;
}
/** Credentials live only in immutable owned private files, never in Catalog projections. */
export class LifecycleAuthorizationStore {
 readonly catalog:string;readonly root:string;readonly catalogIdentity:string;private readonly rootIdentity:string;
 private constructor(catalog:string,root:string,readonly realm:LifecycleRealmReader,readonly fixture:boolean,readonly transport:typeof fetch,private readonly syncNative:(path:string,digest:string,identity:string)=>PasswordIdentity['factors']|null){
  this.catalog=resolve(catalog);this.catalogIdentity=fileIdentity(this.catalog);this.root=resolve(root);
  if(this.root===this.catalog||this.root.startsWith(this.catalog+'/'))throw new Error('Lifecycle authority revoked');
  mkdirSync(this.root,{mode:0o700,recursive:true});const s=lstatSync(this.root);
  if(!s.isDirectory()||s.isSymbolicLink()||realpathSync(this.root)!==this.root||s.uid!==process.getuid?.()||(s.mode&0o077))throw new Error('Lifecycle authority revoked');
  this.rootIdentity=`${s.dev}:${s.ino}`;
 }
 static fixtureOnly(catalog:string,root:string,realm:LifecycleRealmReader,transport:typeof fetch,syncCurrent:()=>PasswordIdentity['factors']|null){
  return new LifecycleAuthorizationStore(catalog,root,realm,true,transport,()=>syncCurrent());
 }
 static installed(checkout:string){
  const base=resolve(checkout),catalog=resolve(base,'.lab/upstream/control.sqlite'),root=resolve(base,'.secrets/upstream/lifecycle-authorization');
  const script=resolve(base,'lab/lifecycle_auth_realm.py');
  const realm:LifecycleRealmReader=async()=>{
   const child=Bun.spawn(['/usr/bin/python3',script],{stdout:'pipe',stderr:'pipe',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
   const [output,,code]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text(),child.exited]);
   if(code!==0||output.length>8192)throw new Error('Retained original Auth unavailable');
   const value=JSON.parse(output);if(!value||typeof value.auth!=='string'||typeof value.key!=='string'||
    typeof value.process!=='string'||!/^[a-f0-9]{64}:[a-f0-9]{64}$/.test(value.process))throw new Error('Retained original Auth unavailable');
   return value;
  };
  const syncNative=(path:string,digest:string,identity:string)=>{
   const child=spawnSync('/usr/bin/python3',[script,'--material',path,'--digest',digest,'--identity',identity],
    {encoding:'utf8',timeout:15000,maxBuffer:8192,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
   if(child.error||child.status!==0)return null;
   try{const value=JSON.parse(child.stdout);return Array.isArray(value.factors)?value.factors:null;}catch{return null;}
  };
  return new LifecycleAuthorizationStore(catalog,root,realm,false,fetch,syncNative);
 }
 assertOwned(){if(fileIdentity(this.catalog)!==this.catalogIdentity)return refused();
  const s=lstatSync(this.root);if(!s.isDirectory()||s.isSymbolicLink()||s.uid!==process.getuid?.()||(s.mode&0o077)||`${s.dev}:${s.ino}`!==this.rootIdentity||realpathSync(this.root)!==this.root)return refused();}
 private path(operation:string,digest:string){if(!uuid.test(operation)||!/^\w{64}$/.test(digest)||!/^[a-f0-9]{64}$/.test(digest))return refused();return join(this.root,operation+'.'+digest+'.json');}
 save(value:Material):string {
  this.assertOwned();const digest=hash(value),target=this.path(value.binding.operation,digest),pending=join(this.root,'.'+randomUUID());
  const fd=openSync(pending,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600);
  try{writeFileSync(fd,JSON.stringify(value));fsyncSync(fd);}finally{closeSync(fd);}
  try{linkSync(pending,target);}catch(error){
   if((error as NodeJS.ErrnoException).code!=='EEXIST'||fileIdentity(target)===''||readFileSync(target,'utf8')!==JSON.stringify(value))throw error;
  }finally{unlinkSync(pending);}const directory=openSync(this.root,constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW);
  try{fsyncSync(directory);}finally{closeSync(directory);}return digest;
 }
 verifyNative(operation:string,digest:string,identity:string):PasswordIdentity['factors'] {
  this.assertOwned();const value=this.syncNative(this.path(operation,digest),digest,identity);if(!value)return refused();return value;
 }
 identity(operation:string,digest:string):string {this.assertOwned();return fileIdentity(this.path(operation,digest));}
 load(row:LifecycleOperation,digest:string,expectedIdentity:string|null):Material {
  this.assertOwned();const path=this.path(row.operation,digest);if(!expectedIdentity||fileIdentity(path)!==expectedIdentity)return refused();const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW);
  try{const s=fstatSync(fd);if(!s.isFile()||s.nlink!==1||s.uid!==process.getuid?.()||(s.mode&0o077)||s.size>32768||`${s.dev}:${s.ino}`!==expectedIdentity)return refused();
   const value=JSON.parse(readFileSync(fd,'utf8')) as Material;
    if(hash(value)!==digest||value.version!==1||value.catalog!==this.catalog||value.catalogIdentity!==this.catalogIdentity||hash(value.binding)!==hash(binding(row)))return refused();
    if(typeof value.factor!=='string'||!uuid.test(value.factor)||!value.identity.factors.some(factor=>
     factor.id===value.factor&&factor.status==='verified'&&factor.factor_type==='totp'))return refused();
   return value;
  }finally{closeSync(fd);}
 }
}
/** A concrete native lookup capability. An installed worker cannot supply an identity callback. */
export class NativeLifecycleAuthority {
 private fresh:PasswordIdentity|null=null;private digest:string|null=null;private material:Material|null=null;
  private constructor(private catalog:Catalog,private store:LifecycleAuthorizationStore,private bearer:string,
   private realm:LifecycleRealm,private identity:PasswordIdentity,private readonly factor:string,private epoch:number,private transport:typeof fetch){}
 static async request(catalog:Catalog,store:LifecycleAuthorizationStore,request:Request,transport:typeof fetch=fetch){
  if(!store.fixture&&transport!==fetch)return refused();store.assertOwned();const realm=await store.realm();
  const bearer=request.headers.get('authorization');if(!bearer||bearer.length>8192||!/^Bearer \S+$/i.test(bearer))return refused();
  const identify=managementPasswordIdentity(realm.auth,realm.key,transport),identity=await identify(new Request('http://management.internal',{headers:{authorization:bearer}}));
   if(!identity||!hasManagementMfa(identity,catalog.managementSecurity))return refused();
   const factor=catalog.managementSecurity.grantedFactor(identity.actor,identity.session,identity.verifiedAt,
    identity.factors.filter(value=>value.status==='verified'&&value.factor_type==='totp').map(value=>value.id));
   if(!factor)return refused();
   const authority=new NativeLifecycleAuthority(catalog,store,bearer,realm,identity,factor,catalog.managementSecurity.epoch(identity.actor),transport);
  await authority.refresh();return authority;
 }
 static async resume(catalog:Catalog,store:LifecycleAuthorizationStore,row:LifecycleOperation){
  const digest=catalog.lifecycleAuthorizationDigest(row.operation);if(!digest)return refused();const value=store.load(row,digest,catalog.lifecycleAuthorizationIdentity(row.operation));
   const authority=new NativeLifecycleAuthority(catalog,store,value.bearer,value.realm,value.identity,value.factor,value.epoch,store.transport);
  authority.material=value;authority.digest=digest;await authority.refresh(row);return authority;
 }
 actor(){return this.identity.actor;}
 owns(catalog:Catalog){return this.catalog===catalog;}
 run<T>(work:()=>T):T{return scope.run(this,work);}
 capture(row:Binding,renewal=false):{digest:string;identity:string} {
  this.assertCurrent();if(row.coverage!=='disposable-fixture'&&this.store.fixture||!renewal&&this.identity.actor!==row.actor)return refused();
   const value:Material={version:1,binding:binding(row),realm:this.realm,bearer:this.bearer,identity:this.identity,factor:this.factor,epoch:this.epoch,
   catalog:this.store.catalog,catalogIdentity:this.store.catalogIdentity};
  const digest=this.store.save(value);this.fresh={...this.identity,factors:this.store.verifyNative(row.operation,digest,this.store.identity(row.operation,digest))};this.assertCurrent();this.material=value;this.digest=digest;return {digest,identity:this.store.identity(row.operation,digest)};
 }
  async refresh(row?:LifecycleOperation):Promise<void>{
  this.fresh=null;this.store.assertOwned();const realm=await this.store.realm();if(hash(realm)!==hash(this.realm))return refused();
  const identity=await managementPasswordIdentity(realm.auth,realm.key,this.transport)(new Request('http://management.internal',{headers:{authorization:this.bearer}}));
  const after=await this.store.realm();this.store.assertOwned();if(hash(after)!==hash(this.realm)||!identity||
   identity.actor!==this.identity.actor||identity.session!==this.identity.session||identity.expiresAt!==this.identity.expiresAt||
   identity.passwordAt!==this.identity.passwordAt||identity.verifiedAt!==this.identity.verifiedAt||identity.aal!==this.identity.aal)return refused();
   this.fresh=identity;this.assertCurrent(row);
  }
  private hasOriginalGrant(identity:PasswordIdentity):boolean {
   return hasManagementMfa(identity,this.catalog.managementSecurity)&&this.catalog.managementSecurity.grantedFactor(
    identity.actor,identity.session,identity.verifiedAt,
    identity.factors.filter(factor=>factor.status==='verified'&&factor.factor_type==='totp').map(factor=>factor.id))===this.factor;
  }
 assertCurrent(row?:LifecycleOperation):void {
  if(!row&&this.material)row=this.catalog.lifecycleAuthorizationOperation(this.material.binding.operation)??undefined;
  if(this.material&&!row)return refused();
  this.store.assertOwned();if(!this.fresh||this.catalog.managementSecurity.epoch(this.identity.actor)!==this.epoch||
    !this.hasOriginalGrant(this.fresh))return refused();
  if(row){if(row.coverage!=='disposable-fixture'&&this.store.fixture)return refused();const digest=this.catalog.lifecycleAuthorizationDigest(row.operation);if(!digest)return refused();
    const material=this.store.load(row,digest,this.catalog.lifecycleAuthorizationIdentity(row.operation));if(material.bearer!==this.bearer||hash(material.identity)!==hash(this.identity)||material.factor!==this.factor||material.epoch!==this.epoch||hash(material.realm)!==hash(this.realm))return refused();
   if(this.digest&&digest!==this.digest)return refused();this.digest=digest;this.material=material;
   this.fresh={...this.identity,factors:this.store.verifyNative(row.operation,digest,this.catalog.lifecycleAuthorizationIdentity(row.operation)!)};
    if(!this.hasOriginalGrant(this.fresh)||this.catalog.managementSecurity.epoch(this.identity.actor)!==this.epoch)return refused();
   this.catalog.assertLifecycleOwner(this.identity.actor,row.environment,row.state);
  }
 }
}
export function captureLifecycleAuthorization(catalog:Catalog,row:Binding,renewal=false):{digest:string;identity:string}|null {
 const authority=scope.getStore();if(authority){if(!authority.owns(catalog))return refused();return authority.capture(row,renewal);}
 if(row.coverage!=='disposable-fixture')return refused();return null;
}
export function requireLifecycleAuthorization(catalog:Catalog,row:LifecycleOperation):void {
 if(!catalog.lifecycleAuthorizationDigest(row.operation)&&row.coverage==='disposable-fixture')return;
 const authority=scope.getStore();if(!authority||!authority.owns(catalog))return refused();authority.assertCurrent(row);
}
export async function refreshLifecycleAuthorization(catalog:Catalog,row:LifecycleOperation):Promise<void>{
 if(!catalog.lifecycleAuthorizationDigest(row.operation)&&row.coverage==='disposable-fixture')return;
 const authority=scope.getStore();if(!authority||!authority.owns(catalog))return refused();await authority.refresh(row);
}

export async function refreshLifecycleRequest(catalog:Catalog):Promise<void>{
 const authority=scope.getStore();if(!authority){if(catalog.lifecycleAuthorizationStore)return refused();return;}
 if(!authority.owns(catalog))return refused();await authority.refresh();
}
