import {Catalog,type MembershipRole} from './catalog';
import {reply} from './auth';

type Binding={catalog:Catalog;render:()=>Response};
const bindings=new WeakMap<Response,Readonly<Binding>>();

/** A trusted handler binds its actual response, never a client header or claimed scope. */
export function bindManagementPublication(response:Response,catalog:Catalog,render:()=>Response):Response {
 bindings.set(response,Object.freeze({catalog,render}));return response;
}

/** Render read-only state now and again synchronously after the final native lookup. */
export function managementPublication(catalog:Catalog,render:()=>Response):Response {
 return bindManagementPublication(render(),catalog,render);
}

export function requireOrganizationPublication(catalog:Catalog,actor:string,organization:string,roles:readonly MembershipRole[]):void {
 const membership=catalog.listOrganizations(actor).find(item=>item.id===organization);
 if(!membership||!roles.includes(membership.role))throw new Error('Forbidden');
}

export function requireOperatorPublication(catalog:Catalog,actor:string):void {
 if(!catalog.installationOperator(actor))throw new Error('Forbidden');
}

/** No await separates current scope, current aggregate rows and the serialized response. */
export function publishManagementResponse(catalog:Catalog,response:Response):Response {
 const bound=bindings.get(response);if(!bound)return response;
 if(bound.catalog!==catalog)return reply(403,{message:'Forbidden'});
 try{return catalog.withManagementPublication(bound.render);}
 catch(error){
  const message=error instanceof Error?error.message:'';
  if(message==='Forbidden')return reply(403,{message:'Forbidden'});
  if(message==='Environment is not ready')return reply(409,{message});
  return reply(503,{message:'Management publication unavailable'});
 }
}
