import {createHash,createHmac,randomUUID,timingSafeEqual} from 'node:crypto';
import type {StorageCredentialPolicies,StorageCredentialPolicy} from './storage-credentials-private';
export type StorageCredentialIssuer=(request:Request)=>Promise<Response>;
export type StorageIssuerOptions={
 policies:()=>StorageCredentialPolicies;
 /** Null means missing, paused, deleted, Storage unavailable or incompatible placement. */
 epoch:(environment:string)=>number|null;
 secret:(environment:string)=>string|undefined;
 rate:(bucket:string,limit:number,windowMs:number)=>boolean;
 host:()=>string;
 now?:()=>number;
};
const response=(status:number,message:string)=>Response.json({message},{status,headers:{'cache-control':'private, no-store','x-content-type-options':'nosniff'}});
const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
export function storageCredentialIssuer(options:StorageIssuerOptions):StorageCredentialIssuer {
 const now=options.now??(()=>Math.floor(Date.now()/1000));
 return async(request:Request):Promise<Response>=>{
  try {
   const url=new URL(request.url),match=url.pathname.match(/^\/(e_[a-f0-9]{24})\/storage-credentials\/v1\/([a-z][a-z0-9_-]{0,63})$/);
   if(!match)return response(404,'Unknown credential route');
   if(request.method!=='POST')return response(405,'Method not allowed');
   if(url.host!==options.host()||request.headers.has('host')&&request.headers.get('host')!==url.host||url.search||request.headers.has('origin')||request.headers.has('sec-fetch-site')||request.body!==null)return response(400,'Invalid credential request');
   const authorization=request.headers.get('authorization');
   if(!authorization||!/^Bearer sb_storage_refresh_[A-Za-z0-9_-]{43}$/.test(authorization))return response(401,'Invalid storage credential');
   const hash=createHash('sha256').update(authorization.slice(7)).digest(),environment=match[1]!,purpose=match[2]!,at=now();
   const choose=(policies:StorageCredentialPolicies):StorageCredentialPolicy|undefined=>{
    let found:StorageCredentialPolicy|undefined;
    for(const candidate of policies.credentials){
     const same=timingSafeEqual(Buffer.from(candidate.secretSha256,'hex'),hash);
     if(same&&candidate.environment===environment&&candidate.purpose===purpose&&candidate.enabled&&candidate.notBefore<=at&&candidate.expiresAt>at)found=candidate;
    }
    return found;
   };
   const policy=choose(options.policies());
   if(!policy)return response(401,'Invalid storage credential');
   // Invalid public requests cannot exhaust authenticated refresh admission.
   if(!options.rate('storage-issuer:global',1200,60000))return response(429,'Credential request limited');
   if(!options.rate('storage-issuer:'+environment+':'+purpose+':'+policy.generation,600,60000))return response(429,'Credential request limited');
   const epoch=options.epoch(environment);
   if(epoch===null)return response(503,'Storage credential unavailable');
   const secret=options.secret(environment);
   if(!secret||secret.length<32)return response(503,'Storage credential unavailable');
   const expiresAt=at+3600,refreshAfter=at+1800;
   const unsigned=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role:policy.role,aud:'sbarbase:storage',iss:'sbarbase-storage-issuer',iat:at,nbf:at-5,exp:expiresAt,jti:randomUUID(),storage_credential_generation:policy.generation});
   const accessToken=unsigned+'.'+createHmac('sha256',secret).update(unsigned).digest('base64url');
   // Observe revocation, generation, signing-key rotation and lifecycle again
   // immediately before publication. No policy TTL or cached token is used here.
   const current=choose(options.policies());
   if(!current||JSON.stringify(current)!==JSON.stringify(policy)||options.epoch(environment)!==epoch||options.secret(environment)!==secret||now()>=policy.expiresAt||request.signal.aborted)return response(503,'Storage credential unavailable');
   return Response.json({accessToken,expiresAt,refreshAfter,environment,purpose,generation:policy.generation},{headers:{'cache-control':'private, no-store','x-content-type-options':'nosniff'}});
  } catch {return response(503,'Storage credential unavailable');}
 };
}
