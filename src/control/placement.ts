/** Trusted runtime addresses only. API keys and signing secrets stay outside placement. */
export type ServicePlacement={auth:string;rest:string;storage?:{url:string;tenantHost:string}};
export type DatabaseIdentity={name:string;oid:number};
export type NativePlacement={version:1;profile:'native-dedicated';runtime:string;ownership:string;generation:number;
 engine:{id:string;container:string;image:string;network:string;volume:string};
 application:DatabaseIdentity;maintenance:DatabaseIdentity;credentials:{application:string;maintenance:string};
 declaration:string;services:ServicePlacement};
export type RuntimePlacement=ServicePlacement|NativePlacement;
export type RuntimeRouting={revision:number;maintenance:boolean;placement:RuntimePlacement|null};
export class PlacementUnavailable extends Error {
 constructor(native=false){super(native?'Native dedicated placement is not admitted':'Runtime routing unavailable');}
}
export type ResolvedPlacement=
 |{profile:'legacy-shared';runtime:string;generation:null;database:string;services:ServicePlacement|null}
 |{profile:'native-dedicated';runtime:string;generation:number;identity:NativePlacement;admission:'unadmitted'};

function object(value:unknown,fields:readonly string[]):Record<string,unknown> {
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!fields.includes(key)))throw new Error('Invalid placement identity');
 return value as Record<string,unknown>;
}
function text(value:unknown,pattern:RegExp):string {
 if(typeof value!=='string'||!pattern.test(value))throw new Error('Invalid placement identity');
 return value;
}
function positive(value:unknown):number {
 if(typeof value!=='number'||!Number.isSafeInteger(value)||value<1)throw new Error('Invalid placement generation or OID');
 return value;
}
const reference=/^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$/;
const runtimeName=/^e_[a-f0-9]{24}$/;

function validateServices(input:unknown):ServicePlacement {
 const value=object(input,['auth','rest','storage']);
 const endpoint=(value:unknown)=>{
  if(typeof value!=='string')throw new Error('Invalid placement endpoint');
  const url=new URL(value);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw new Error('Invalid placement endpoint');
  return url.origin;
 };
 const result:ServicePlacement={auth:endpoint(value.auth),rest:endpoint(value.rest)};
 if(value.storage!==undefined){
  const storage=object(value.storage,['url','tenantHost']);
  result.storage={url:endpoint(storage.url),tenantHost:text(storage.tenantHost,/^[a-z0-9_][a-z0-9_.-]{0,252}$/)};
 }
 return result;
}

export function validatePlacement(input:unknown):RuntimePlacement {
 const value=object(input,['auth','rest','storage','version','profile','runtime','ownership','generation','engine','application','maintenance','credentials','declaration','services']);
 if(!('version' in value)&&!('profile' in value))return validateServices(value);
 if(value.version!==1||value.profile!=='native-dedicated')throw new Error('Unsupported placement version or profile');
 if(Object.keys(value).some(key=>['auth','rest','storage'].includes(key)))throw new Error('Invalid native placement');
 const engine=object(value.engine,['id','container','image','network','volume']);
 const database=(input:unknown):DatabaseIdentity=>{
  const db=object(input,['name','oid']);const oid=positive(db.oid);
  if(oid>4294967295)throw new Error('Invalid database OID');
  return {name:text(db.name,/^[a-z][a-z0-9_]{0,62}$/),oid};
 };
 const application=database(value.application),maintenance=database(value.maintenance);
 if(application.name!=='postgres'||application.name===maintenance.name||application.oid===maintenance.oid)throw new Error('Distinct application and maintenance identity required');
 const credentials=object(value.credentials,['application','maintenance']);
 const result:NativePlacement={version:1,profile:'native-dedicated',runtime:text(value.runtime,runtimeName),ownership:text(value.ownership,reference),generation:positive(value.generation),
  engine:{id:text(engine.id,reference),container:text(engine.container,/^[a-f0-9]{64}$/),image:text(engine.image,/^sha256:[a-f0-9]{64}$/),network:text(engine.network,/^[a-f0-9]{64}$/),volume:text(engine.volume,reference)},
  application,maintenance,credentials:{application:text(credentials.application,reference),maintenance:text(credentials.maintenance,reference)},declaration:text(value.declaration,reference),services:validateServices(value.services)};
 if(result.credentials.application===result.credentials.maintenance)throw new Error('Distinct credential references required');
 return result;
}

/** Observations compare declarations, never establish or refresh authority. */
export function resolveRuntimePlacement(runtime:string,routing:RuntimeRouting,observed?:NativePlacement):ResolvedPlacement {
 if(!runtimeName.test(runtime)||!Number.isSafeInteger(routing.revision)||routing.revision<0)throw new Error('Invalid runtime routing identity');
 const placement=routing.placement===null?null:validatePlacement(routing.placement);
 if(placement===null||!('version' in placement)){
  if(observed)throw new Error('Legacy placement requires explicit identity initialization');
  return {profile:'legacy-shared',runtime,generation:null,database:runtime,services:placement};
 }
 if(placement.runtime!==runtime)throw new Error('Foreign placement runtime');
 if(observed){
  const comparison=validatePlacement(observed);
  if(!('version' in comparison)||JSON.stringify(comparison)!==JSON.stringify(placement))throw new Error('Stale or foreign placement identity');
 }
 return {profile:'native-dedicated',runtime,generation:placement.generation,identity:placement,admission:'unadmitted'};
}

/** Declaration references are not verified runtime evidence. Native transport stays closed. */
export function placementServices(resolved:ResolvedPlacement):ServicePlacement|null {
 if(resolved.profile==='native-dedicated')throw new PlacementUnavailable(true);
 return resolved.services;
}

export function validatePlacementTransition(runtime:string,current:RuntimePlacement|null,next:RuntimePlacement) {
 // Only the catalog's paused, expected-revision operator transaction calls this.
 // An initial generation is a declaration, not adoption of an observed engine.
 if(!('version' in next)){
  if(current&&'version' in current)throw new Error('Native placement cannot revert to legacy routing');
  return;
 }
 if(next.runtime!==runtime)throw new Error('Foreign placement runtime');
 if(!current||!('version' in current)){
  if(next.generation!==1)throw new Error('Initial placement generation must be one');
  return;
 }
 if(next.ownership!==current.ownership||next.runtime!==current.runtime)throw new Error('Foreign placement ownership');
 if(next.generation===current.generation){
  if(JSON.stringify(next)!==JSON.stringify(current))throw new Error('Placement identity change requires a new generation');
 }else if(next.generation!==current.generation+1)throw new Error('Stale placement generation');
}
