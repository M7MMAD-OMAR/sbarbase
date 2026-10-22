import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** GET and PUT `/environments/{id}/scheduled-jobs` (`{"enabled": true|false}`), for owners and admins of a
 * ready environment. The supervisor turns that environment's Scheduled jobs on or off (lab/realtime.py,
 * docs/engineering/SCHEDULED-JOBS.md) and records the outcome, which the console shows while it waits. */
export function scheduledJobsHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/scheduled-jobs$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   const answer=(status:number,data:ReturnType<Catalog['scheduledJobs']>)=>bindReadyEnvironmentPublication(reply(status,{data}),catalog,actor,environment,data.runtime,true);
   if(request.method==='GET')return answer(200,catalog.scheduledJobs(actor,environment));
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   const enabled=(input as {enabled?:unknown}|null)?.enabled;
   if(typeof enabled!=='boolean'||Object.keys(input as object).length!==1)return reply(400,{message:'Send {"enabled": true} or {"enabled": false}'});
   return answer(202,catalog.requestScheduledJobs(actor,environment,enabled));
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Scheduled jobs change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
