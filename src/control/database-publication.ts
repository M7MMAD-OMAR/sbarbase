import {Catalog} from './catalog';
import {reply} from './auth';

type Snapshot=ReturnType<Catalog['databaseWorkflowState']>;
type Binding={catalog:Catalog;actor:string;epoch:number;environment:string;runtime:string;
 render:(state:Snapshot)=>Response};
// Only the exact Response constructed by the trusted handler carries this binding.
// Headers, JSON and caller supplied readiness flags cannot create publication authority.
const bindings=new WeakMap<Response,Readonly<Binding>>();

export function bindDatabasePublication(response:Response,catalog:Catalog,actor:string,epoch:number,
 environment:string,runtime:string,render:Binding['render']):Response {
 bindings.set(response,Object.freeze({catalog,actor,epoch,environment,runtime,render}));
 return response;
}

/** Final synchronous publication, after application has awaited control and checked native MFA. */
export function publishDatabaseResponse(catalog:Catalog,response:Response):Response {
 const bound=bindings.get(response);
 if(!bound)return response;
 if(bound.catalog!==catalog)return reply(403,{message:'Forbidden'});
 try {
  const state=bound.catalog.databaseWorkflowState(bound.actor,bound.environment,bound.epoch,bound.runtime);
  return bound.render(state);
 }catch(error){
  const message=error instanceof Error?error.message:'';
  return message==='Environment is not ready'?reply(409,{message}):reply(403,{message:'Forbidden'});
 }
}
