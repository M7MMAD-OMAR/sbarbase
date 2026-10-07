import {Catalog} from './catalog';
import {reply} from './auth';

type Binding={catalog:Catalog;actor:string;environment:string;runtime:string;epoch:number;write:boolean};
// Only a Response bound by its trusted handler can carry captured tenant access.
const bindings=new WeakMap<Response,Readonly<Binding>>();

export function bindReadyEnvironmentPublication(response:Response,catalog:Catalog,actor:string,
 environment:string,runtime:string,write:boolean,epoch=catalog.runtimeEpoch(runtime)):Response {
 bindings.set(response,Object.freeze({catalog,actor,environment,runtime,epoch,write}));
 return response;
}

/** Recheck tenant role and runtime synchronously after the final native lookup. */
export function publishReadyEnvironmentResponse(catalog:Catalog,response:Response):Response {
 const bound=bindings.get(response);
 if(!bound)return response;
 if(bound.catalog!==catalog)return reply(403,{message:'Forbidden'});
 try {
  return catalog.withReadyEnvironment(bound.actor,bound.environment,bound.write,job=>
   job.runtime===bound.runtime&&catalog.runtimeEpoch(job.runtime)===bound.epoch
    ?response:reply(409,{message:'Environment is not ready'}));
 }catch(error){
  const message=error instanceof Error?error.message:'';
  return message==='Environment is not ready'?reply(409,{message}):reply(403,{message:'Forbidden'});
 }
}
