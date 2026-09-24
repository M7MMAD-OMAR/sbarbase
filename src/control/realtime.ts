import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** GET and PUT `/environments/{id}/realtime` (`{"enabled": true|false}`), for owners and admins of
 * a ready environment. The supervisor starts or stops that environment's Realtime (lab/realtime.py)
 * and records the outcome, which the console shows while it waits. */
export function realtimeHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/realtime$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   if(request.method==='GET')return reply(200,{data:catalog.realtime(actor,environment)});
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   const enabled=(input as {enabled?:unknown}|null)?.enabled;
   if(typeof enabled!=='boolean'||Object.keys(input as object).length!==1)return reply(400,{message:'Send {"enabled": true} or {"enabled": false}'});
   return reply(202,{data:catalog.requestRealtime(actor,environment,enabled)});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Realtime change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
