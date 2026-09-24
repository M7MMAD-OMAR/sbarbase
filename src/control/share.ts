import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** GET and PUT `/environments/{id}/share`: an environment's guaranteed share of the application
 * gateway. Any member reads it; owners and admins change it with `{"share": n}`. The gateway reads
 * the catalog on each request, so a change applies at once. docs/engineering/FAIR-SHARE-ADMISSION.md */
export function shareHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/share$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   if(request.method==='GET')return reply(200,{data:catalog.gatewayShareState(actor,environment)});
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   const share=input&&typeof input==='object'&&!Array.isArray(input)&&Object.keys(input).length===1?(input as {share?:unknown}).share:undefined;
   if(typeof share!=='number')return reply(400,{message:'Invalid request'});
   return reply(200,{data:catalog.setGatewayShare(actor,environment,share)});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Invalid share')return reply(400,{message:'Invalid share'});
   if(['Environment is not ready','Shares exceed gateway capacity'].includes(message))return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
