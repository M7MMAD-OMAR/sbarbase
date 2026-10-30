import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {parseServiceChange,SERVICE_CHANGE_HELP} from './service-mode-http';

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
   const withMode=(data:ReturnType<Catalog['realtime']>)=>{const mode=catalog.serviceMode(data.runtime,'realtime');return {...data,mode:mode.mode,idle_minutes:mode.idleMinutes,kill_switch:catalog.installationServices().killSwitch};};
   const answer=(status:number,data:ReturnType<Catalog['realtime']>)=>bindReadyEnvironmentPublication(reply(status,{data:withMode(data)}),catalog,actor,environment,data.runtime,true);
   if(request.method==='GET')return answer(200,catalog.realtime(actor,environment));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   const change=parseServiceChange(input);
   if(!change)return reply(400,{message:SERVICE_CHANGE_HELP});
   if(change.kind==='enabled')return answer(202,catalog.requestRealtime(actor,environment,change.enabled));
   catalog.setServiceMode(actor,environment,'realtime',change.mode,change.idle);
   return answer(202,catalog.realtime(actor,environment));
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Invalid services setting')return reply(400,{message:SERVICE_CHANGE_HELP});
   if(message==='Environment is not ready'||message==='Realtime change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
