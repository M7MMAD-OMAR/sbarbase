import {Catalog} from './catalog';
import {KeyStore} from './keys';
import type {ManagementIdentity} from './auth';

const reply=(status:number,data:unknown)=>Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
/** Publishable keys only. Never accepts a client-supplied runtime, role or actor.
 * Raw key material is returned once; list responses contain only metadata.
 */
export function keyHandler(catalog:Catalog,keys:KeyStore,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  const match=path.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/(keys|connection)(?:\/([a-f0-9-]{36}))?$/);
  if(!match||!match[1]) return reply(404,{message:'Unknown route'});
  const environment=match[1],action=match[2],keyId=match[3];
  const method=request.method;
  if(action==='connection'&&(method!=='GET'||keyId)||action==='keys'&&
    !(keyId?method==='DELETE':['GET','POST'].includes(method))) return reply(405,{message:'Method not allowed'});
  let actor:string|null;
  try {actor=await identify(request);} catch {return reply(503,{message:'Authentication unavailable'});}
  if(!actor) return reply(401,{message:'Authentication required'});
  if(request.body) return reply(400,{message:'This endpoint does not accept a body'});
  try {
   return catalog.withReadyEnvironment(actor,environment,action==='keys',job=>{
    if(action==='connection') return reply(200,{environment,apiPath:`/${job.runtime}`,services:['auth','rest']});
    if(method==='GET') return reply(200,{data:keys.list(job.runtime)});
    if(method==='POST') return reply(201,keys.issue(job.runtime,'publishable'));
    return keys.revoke(job.runtime,keyId!)?reply(200,{revoked:true}):reply(404,{message:'Active key not found'});
   });
  } catch(error) {
   if(error instanceof Error&&error.message==='Forbidden') return reply(403,{message:'Forbidden'});
   if(error instanceof Error&&error.message==='Environment is not ready') return reply(409,{message:'Environment is not ready'});
   return reply(500,{message:'Key operation failed'});
  }
 };
}
