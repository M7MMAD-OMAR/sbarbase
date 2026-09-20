import {Catalog} from './catalog';
import type {ManagementIdentity} from './auth';

function reply(status:number,data:unknown) {
  return Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
}
class InputError extends Error {}
async function body(request:Request):Promise<{name:string}> {
  if(request.headers.get('content-type')?.split(';')[0]?.trim()!=='application/json'||!request.body)
    throw new InputError();
  const reader=request.body.getReader(),chunks:Uint8Array[]=[];let bytes=0;
  let timer:ReturnType<typeof setTimeout>|undefined;
  const expired=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{
    void reader.cancel().catch(()=>{});reject(new InputError());
  },5000);});
  try {
    while(true) {
      const chunk=await Promise.race([reader.read(),expired]);
      if(chunk.done) break;
      bytes+=chunk.value.length;
      if(bytes>4096) {void reader.cancel().catch(()=>{});throw new InputError();}
      chunks.push(chunk.value);
    }
    const parsed=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(!parsed||Array.isArray(parsed)||Object.keys(parsed).some(k=>k!=='name')||typeof parsed.name!=='string')
      throw new InputError();
    return parsed;
  } catch {throw new InputError();}
  finally {clearTimeout(timer);reader.releaseLock();}
}

/** Metadata API only. Organization bootstrap and transfer intentionally remain
 * internal until invitations and complete runtime access revocation are ready.
 */
export function managementHandler(catalog:Catalog,identify:ManagementIdentity) {
  return async(request:Request):Promise<Response>=>{
    const path=new URL(request.url).pathname;
    const match=path.match(/^\/management\/v1\/(organizations|projects|environments)\/([a-f0-9-]{36})\/(projects|environments|provision)$/);
    if(!match||!((match[1]==='organizations'&&match[3]==='projects')||
      (match[1]==='projects'&&match[3]==='environments')||
      (match[1]==='environments'&&match[3]==='provision'))) return reply(404,{message:'Unknown route'});
    if(!['GET','POST'].includes(request.method)||(match[1]==='environments'&&request.method!=='GET')) return reply(405,{message:'Method not allowed'});
    let actor:string|null;
    try {actor=await identify(request);} catch {return reply(503,{message:'Authentication unavailable'});}
    if(!actor) return reply(401,{message:'Authentication required'});
    const id=match[2];if(!id) return reply(404,{message:'Unknown route'});
    try {
      if(match[1]==='environments') {
        const job=catalog.getProvision(actor,id);
        return reply(200,{environment:job.environment,state:job.state,attempt:job.attempt});
      }
      if(request.method==='GET') return reply(200,{data:match[1]==='organizations'
        ?catalog.listProjects(actor,id):catalog.listEnvironments(actor,id)});
      const input=await body(request);
      const created=match[1]==='organizations'?catalog.createProject(actor,id,input.name)
        :catalog.createEnvironment(actor,id,input.name);
      return reply(match[1]==='organizations'?201:202,{id:created,state:match[1]==='organizations'?'metadata_only':'queued'});
    } catch(error) {
      if(error instanceof InputError||error instanceof Error&&error.message==='Invalid name')
        return reply(400,{message:'Invalid request'});
      if(error instanceof Error&&error.message==='Forbidden') return reply(403,{message:'Forbidden'});
      return reply(500,{message:'Management operation failed'});
    }
  };
}
