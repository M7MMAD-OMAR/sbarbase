import {Catalog} from './catalog';
import {KeyStore,KEY_NAME,type KeyKind} from './keys';
import {PlacementUnavailable} from './placement';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {bindReadyEnvironmentPublication} from './ready-publication';
import {boundedJsonBody,InputError} from './http';

/** Publishable and secret API keys (docs/guides/api-keys.md). Never accepts a client-supplied
 * runtime, role or actor. Raw key material is returned once; list responses contain only metadata.
 *
 * POST /environments/{id}/keys  optional {"type": "publishable"|"secret", "name": "backend"}
 */
export type ServiceDiscovery = (runtime:string)=>readonly ('auth'|'rest'|'storage'|'realtime'|'functions')[];
export function keyHandler(catalog:Catalog,keys:KeyStore,identify:ManagementIdentity,services:ServiceDiscovery=()=>['auth','rest']) {
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  const match=path.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(keys|connection)(?:\/([a-f0-9-]{36}))?$/);
  if(!match||!match[1]) return reply(404,{message:'Unknown route'});
  const environment=match[1],action=match[2],keyId=match[3];
  const method=request.method;
  if(action==='connection'&&(method!=='GET'||keyId)||action==='keys'&&
    !(keyId?method==='DELETE':['GET','POST'].includes(method))) return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response) return actor;
  let kind:KeyKind='publishable',name='default';
  if(method==='POST'&&request.body) {
   // A body is optional; one that is sent goes through the bounded reader (4 KiB, 5 seconds, JSON).
   let value:Record<string,unknown>;
   try {value=await boundedJsonBody(request,[],['type','name']);}
   catch(error) {
    if(error instanceof InputError)return reply(400,{message:'Send JSON with "type" ("publishable" or "secret") and an optional "name"'});
    throw error;
   }
   if(value.type!==undefined&&value.type!=='publishable'&&value.type!=='secret')return reply(400,{message:'"type" is "publishable" or "secret"'});
   if(value.name!==undefined&&(typeof value.name!=='string'||!KEY_NAME.test(value.name)))
    return reply(400,{message:'A key name has 4 to 64 lowercase letters, digits or underscores, and does not start with a digit'});
   kind=(value.type as KeyKind|undefined)??'publishable';name=(value.name as string|undefined)??'default';
  } else if(request.body) return reply(400,{message:'This endpoint does not accept a body'});
  try {
    return catalog.withReadyEnvironment(actor,environment,action==='keys',job=>{
     const answer=(response:Response)=>bindReadyEnvironmentPublication(response,catalog,actor,environment,job.runtime,action==='keys');
     if(action==='connection') return answer(reply(200,{environment,apiPath:`/${job.runtime}`,services:services(job.runtime)}));
     if(method==='GET') return answer(reply(200,{data:keys.list(job.runtime)}));
     if(method==='POST') {
      // The key store is its own database: a key whose audit record cannot be written is revoked
      // again, so no key exists that the audit log does not show.
      const issued=keys.issue(job.runtime,kind,name);
      try {catalog.recordApiKey(actor,environment,'keys.created',{key:issued.id,type:kind,name});}
      catch(error) {keys.revoke(job.runtime,issued.id);throw error;}
      return answer(reply(201,{...issued,type:kind,name}));
     }
     if(keys.revoke(job.runtime,keyId!)){catalog.recordApiKey(actor,environment,'keys.revoked',{key:keyId!});return answer(reply(200,{revoked:true}));}
     return answer(reply(404,{message:'Active key not found'}));
   });
  } catch(error) {
   if(error instanceof PlacementUnavailable)return reply(503,{message:error.message});
   if(error instanceof Error&&error.message==='Forbidden') return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready') return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Key operation failed'});
  }
 };
}
