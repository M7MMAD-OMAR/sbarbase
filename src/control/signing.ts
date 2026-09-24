import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** The environment's JWT signing secret (docs/guides/signing-keys.md), for owners and admins of a ready environment.
 *
 * GET  /environments/{id}/signing-key         the last rotation and whether one is waiting (never the secret)
 * POST /environments/{id}/signing-key/rotate  a new secret; the supervisor applies it (lab/durable_runtime.py `rotate_signing`)
 *
 * Every token signed with the old secret stops working. Publishable and secret API keys are not
 * JWTs and keep working. */
export function signingHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/signing-key(\/rotate)?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const rotate=!!match[2];
  if(request.method!==(rotate?'POST':'GET'))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   return rotate?reply(202,{data:catalog.requestSigningRotation(actor,environment)}):reply(200,{data:catalog.signing(actor,environment)});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Signing key rotation in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
