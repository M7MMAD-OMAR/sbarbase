import {randomUUID} from 'node:crypto';
import {Catalog} from './catalog';

export type BootstrapState={version:1;operation:string;email:string;organization:string;actor?:string};
export type BootstrapIdentity={id:string;operation:string};
export type BootstrapAuth={
 find(operation:string):Promise<BootstrapIdentity|null>;
 create(email:string,password:string,operation:string):Promise<BootstrapIdentity>;
 verify(email:string,password:string):Promise<string|null>;
};
export type BootstrapJournal={read():BootstrapState|null;write(state:BootstrapState):void};

/** Local operator only. The caller must hold the installation bootstrap lock.
 * Persist intent before Auth creation. An Auth-owned app_metadata marker permits
 * recovery across a crash before the new identity's ID reaches the local journal.
 * No password or token belongs in the journal or catalog.
 */
export async function bootstrapOperator(catalog:Catalog,auth:BootstrapAuth,journal:BootstrapJournal,
 input:{email:string;password:string;organization:string},checkpoint:(phase:string)=>void=()=>{}) {
 if(typeof input.email!=='string'||typeof input.password!=='string'||typeof input.organization!=='string')throw new Error('Invalid bootstrap input');
 const email=input.email.trim().toLowerCase(),organization=input.organization.trim();
 if(email.length>254||!/^\S+@[^\s@]+\.[^\s@]+$/.test(email)||input.password.length<12||input.password.length>1024||
  !organization||organization.length>100||/[\x00-\x1f]/.test(organization))throw new Error('Invalid bootstrap input');
 let state=journal.read();
 const initialized=catalog.installationBootstrap();
 if(!state) {
  if(initialized)throw new Error('Installation already initialized; journal recovery required');
  state={version:1,operation:randomUUID(),email,organization};journal.write(state);
 }
 if(state.version!==1||typeof state.operation!=='string'||!/^[a-f0-9-]{36}$/.test(state.operation)||state.email!==email||state.organization!==organization)
  throw new Error('Bootstrap intent does not match');
 if(initialized&&initialized.operation!==state.operation)throw new Error('Installation already initialized');
 checkpoint('intent');
 let identity=await auth.find(state.operation);
 if(!identity) {
  // A recorded identity disappearing must never silently create another owner.
  if(state.actor||initialized)throw new Error('Recorded management identity unavailable');
  identity=await auth.create(email,input.password,state.operation);checkpoint('identity-created');
 }
 if(identity.operation!==state.operation||!identity.id||(state.actor&&state.actor!==identity.id))throw new Error('Management identity mismatch');
 if(await auth.verify(email,input.password)!==identity.id)throw new Error('Management credentials do not match');
 state={...state,actor:identity.id};journal.write(state);checkpoint('identity-recorded');
 const id=catalog.initializeInstallation(state.operation,identity.id,organization);checkpoint('catalog');
 return {actor:identity.id,organization:id};
}
