import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {internalToken} from './upstream-app';

/** Set a new password for a management account from the server's shell, when the operator lost
 * it. It needs what only the host has: the management realm's signing secret in
 * `.secrets/upstream/runtime.json`, read here and never printed. The account is found by its
 * email through the realm's admin API, its password replaced, and every console and Studio
 * session it had is ended (the actor epoch), so a session opened with the old password stops at
 * once. Its authenticator, if it has one, stays; a lost authenticator is a separate recovery
 * (lab/management-factor-recovery.ts). The change is written to the audit log.
 * docs/guides/operator-setup.md, "A forgotten password". */

export const MINIMUM=12;
const EMAIL=/^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type Realm={auth:string;secret:string};

export function realm(root:string):Realm {
 const management=JSON.parse(readFileSync(join(root,'.lab/upstream/management.json'),'utf8'));
 const runtime=JSON.parse(readFileSync(join(root,'.secrets/upstream/runtime.json'),'utf8'));
 const auth=management?.auth,secret=runtime?.management?.jwt;
 if(typeof auth!=='string'||!/^https?:\/\/[^/@]+\/?$/.test(auth)||typeof secret!=='string'||!secret)
  throw new Error('The management realm is not configured on this installation');
 return {auth:auth.replace(/\/+$/,''),secret};
}

export class ResetRefusal extends Error {}

/** Returns the account id whose password was replaced. */
export async function resetOperatorPassword(target:Realm,email:string,password:string,catalog:Catalog,request:typeof fetch=fetch):Promise<string> {
 if(!EMAIL.test(email))throw new ResetRefusal('That is not an email address.');
 if(password.length<MINIMUM)throw new ResetRefusal(`The password needs at least ${MINIMUM} characters.`);
 const token=internalToken(target.secret,'service_role');
 const call=async(path:string,init:RequestInit={})=>{
  let response:Response;
  try{response=await request(target.auth+path,{...init,redirect:'error',signal:AbortSignal.timeout(10_000),
   headers:{authorization:`Bearer ${token}`,apikey:token,'content-type':'application/json'}});}
  catch{throw new ResetRefusal('The management sign-in service did not answer. Is Sbarbase running?');}
  if(!response.ok)throw new ResetRefusal(response.status===422?'The sign-in service refused this password; choose a longer or less common one.'
   :`The management sign-in service refused the request (HTTP ${response.status}).`);
  return response.json();
 };
 const wanted=email.trim().toLowerCase();
 let found:{id:string}|undefined;
 for(let page=1;page<=50&&!found;page++){
  const body=await call(`/admin/users?page=${page}&per_page=100`) as {users?:{id:string;email?:string}[]};
  const users=body.users??[];
  found=users.find(user=>(user.email??'').toLowerCase()===wanted);
  if(users.length<100)break;
 }
 if(!found)throw new ResetRefusal('No management account has that email.');
 await call(`/admin/users/${encodeURIComponent(found.id)}`,{method:'PUT',body:JSON.stringify({password})});
 catalog.managementSecurity.revoke(found.id);
 catalog.recordOperatorPasswordReset(found.id);
 return found.id;
}
