import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {AccessTokenStore,TokenRefusal,tokenEpoch,type TokenScope} from './access-tokens';
import {boundedJsonBody,InputError} from './http';
import {requireCurrentManagement} from './management-context';

/** A person's access tokens, from the console (docs/guides/agents.md). Only a signed-in person
 * reaches these routes: a token cannot make, list or revoke tokens.
 *
 * GET    /access-tokens          the caller's tokens (never the tokens themselves)
 * POST   /access-tokens          {"name", "scope"?: {"kind": "all"|"organization"|"project", "id"?}, "read_only"?, "days"?}
 *                                 answers the token once
 * DELETE /access-tokens/{id}     revoke one
 * POST   /access-tokens/revoke-all */
export function accessTokenHandler(catalog:Catalog,tokens:AccessTokenStore,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  const match=path.match(/^\/management\/v1\/access-tokens(?:\/([a-f0-9-]{36}|revoke-all))?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const item=match[1];
  const allowed=!item?['GET','POST']:item==='revoke-all'?['POST']:['DELETE'];
  if(!allowed.includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   if(request.method==='GET'&&!item)return reply(200,{data:tokens.list(actor)});
   if(item==='revoke-all'){
    const count=tokens.revokeAll(actor);
    catalog.recordAccessToken(actor,'access_token.revoked',actor,{count});
    return reply(200,{revoked:count});
   }
   if(item){
    const record=tokens.get(actor,item);
    if(!record||!tokens.revoke(actor,item))return reply(404,{message:'Active token not found'});
    catalog.recordAccessToken(actor,'access_token.revoked',record.scope.kind==='all'?actor:record.scope.id,{token:item});
    return reply(200,{revoked:true});
   }
   let input:Record<string,unknown>;
   try{input=await boundedJsonBody(request,['name'],['scope','read_only','days']);}
   catch(error){if(error instanceof InputError)return reply(400,{message:'Send JSON with a "name", and "scope", "read_only" and "days" if you want them'});throw error;}
   const scope=(input.scope??{kind:'all'}) as Record<string,unknown>;
   if(!scope||typeof scope!=='object'||!['all','organization','project'].includes(scope.kind as string)||
     scope.kind==='all'&&scope.id!==undefined||scope.kind!=='all'&&typeof scope.id!=='string')
    return reply(400,{message:'"scope" is {"kind": "all"}, {"kind": "organization", "id"} or {"kind": "project", "id"}'});
   if(input.read_only!==undefined&&typeof input.read_only!=='boolean')return reply(400,{message:'"read_only" is true or false'});
   const days=input.days??90;
   // A token reaches only what its owner can reach now; a scope outside that is refused here and
   // would see nothing later anyway.
   if(scope.kind==='organization'&&!catalog.listOrganizations(actor).some(organization=>organization.id===scope.id))
    return reply(404,{message:'Organization not found'});
   if(scope.kind==='project'){
    try{catalog.listEnvironments(actor,scope.id as string);}catch{return reply(404,{message:'Project not found'});}
   }
   requireCurrentManagement();
   const issued=tokens.issue(actor,tokenEpoch(catalog.managementSecurity)(actor),{name:String(input.name).trim(),scope:scope as TokenScope,
    read_only:input.read_only===true,days:days as number});
   try{
    catalog.recordAccessToken(actor,'access_token.created',scope.kind==='all'?actor:scope.id as string,
     {token:issued.data.id,scope:scope.kind as string,read_only:issued.data.read_only,days:days as number});
   }catch(error){tokens.revoke(actor,issued.data.id);throw error;}
   return reply(201,{data:issued.data,token:issued.token});
  } catch(error) {
   if(error instanceof TokenRefusal)return reply(400,{message:error.message});
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
