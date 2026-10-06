import {createClient} from '@supabase/supabase-js';
import {Catalog} from '../src/control/catalog';
import {managementIdentity} from '../src/control/auth';

type Recovery={target:string;factor:string;reason:'lost_factor'|'compromised_factor';operatorToken:string;targetPassword:string};
type Realm={auth:string;publishableKey:string;serviceToken:string};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

/** Private host operation. No HTTP route or anonymous recovery credential exists. */
export async function recoverManagementFactor(catalog:Catalog,realm:Realm,input:Recovery,transport:typeof fetch=fetch):Promise<void>{
 if(!UUID.test(input.target)||!UUID.test(input.factor)||!['lost_factor','compromised_factor'].includes(input.reason))throw new Error('Invalid recovery request');
 const endpoint=new URL(realm.auth);
 if(!['http:','https:'].includes(endpoint.protocol)||endpoint.username||endpoint.password||endpoint.pathname!=='/'||endpoint.search||endpoint.hash)throw new Error('Invalid management endpoint');
 const scoped=(async(resource:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const url=new URL(String(resource));
  if(url.origin!=='http://management-recovery.internal'||!url.pathname.startsWith('/auth/v1/'))throw new Error('Unexpected recovery request');
  return transport(new URL(url.pathname.slice('/auth/v1'.length)+url.search,endpoint),{...init,redirect:'error',signal:AbortSignal.timeout(5000)});
 }) as typeof fetch;
 const identify=managementIdentity('http://management-recovery.internal',realm.publishableKey,scoped,catalog.managementSecurity);
 const actor=await identify(new Request('http://management-recovery.internal',{headers:{authorization:'Bearer '+input.operatorToken}}));
 if(!actor||actor===input.target)throw new Error('A different installation owner with verified MFA is required');
 const admin=createClient('http://management-recovery.internal',realm.serviceToken,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:scoped}});
 const lease=catalog.managementSecurity.lease(input.target);
 if(!lease)throw new Error('Another factor operation is in progress');
 let receipt:string|undefined,user:ReturnType<typeof createClient>|undefined;
 async function authorized(){
  if(await identify(new Request('http://management-recovery.internal',{headers:{authorization:'Bearer '+input.operatorToken}}))!==actor)
   throw new Error('Operator MFA session is no longer valid');
  catalog.assertManagementRecoveryOwner(actor!);
  if(!catalog.managementSecurity.holds(input.target,lease!))throw new Error('Recovery lease expired');
 }
 // Check current ownership before any admin lookup and revoke all target session grants durably.
 try{
  receipt=catalog.revokeManagementMfa(actor,input.target,input.reason);
  await authorized();
  const {data,error}=await admin.auth.admin.mfa.listFactors({userId:input.target});
  if(error||!data.factors.some(factor=>factor.id===input.factor))throw new Error('Native recovery lookup failed');
  await authorized();
  const found=await admin.auth.admin.getUserById(input.target);
  if(found.error||!found.data.user.email||typeof input.targetPassword!=='string'||input.targetPassword.length>1024)throw new Error('Target password proof required');
  await authorized();
  user=createClient('http://management-recovery.internal',realm.publishableKey,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:scoped}});
  const proof=await user.auth.signInWithPassword({email:found.data.user.email,password:input.targetPassword});
  if(proof.error||proof.data.user?.id!==input.target)throw new Error('Target password proof failed');
  await authorized();
  const logout=await user.auth.signOut({scope:'global'});
  if(logout.error)throw new Error('Target native session revocation failed');
  await authorized();
  const result=await admin.auth.admin.mfa.deleteFactor({userId:input.target,id:input.factor});
  if(result.error)throw new Error('Native recovery removal failed');
  catalog.finishManagementMfaRecovery(actor,input.target,'completed',receipt);
 }catch{
  if(receipt)catalog.finishManagementMfaRecovery(actor,input.target,'failed',receipt);
  throw new Error('Recovery failed; target management grants remain revoked');
 }finally{
  try{if(user)await user.auth.signOut({scope:'local'});}
  finally{catalog.managementSecurity.release(input.target,lease);}
 }
}

if(import.meta.main){
 // Private JSON from stdin keeps all credentials out of argv and terminal output.
 let catalog:Catalog|undefined;
 try{const payload=await Bun.stdin.json();catalog=new Catalog(payload.catalog);await recoverManagementFactor(catalog,payload.realm,payload.recovery);process.stdout.write('Factor removed; the account must complete native MFA again.\n');}
 catch{process.stderr.write('Recovery refused or failed. Inspect the management security audit.\n');process.exitCode=1;}
 finally{catalog?.close();}
}
