import {createHash} from 'node:crypto';
import {reply,hasManagementMfa,type PasswordIdentity} from './auth';
import type {Catalog} from './catalog';

export type ManagementRealm={auth:string;anonymousToken:string;publishableKey:string};
const UUID='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
class InvalidBody extends Error{}
/** Only original Auth password, refresh, user and native TOTP factor contracts are reachable. */
export function managementAuth(catalog:Catalog,realm:ManagementRealm,
 identify:(request:Request)=>Promise<PasswordIdentity|null>,transport:typeof fetch) {
 const security=catalog.managementSecurity;
 const limited=()=>reply(429,{message:'Too many authentication attempts. Try again later.'});
 const unavailable=()=>reply(503,{message:'Authentication unavailable'});
 async function body(request:Request):Promise<Record<string,unknown>> {
  if(!request.headers.get('content-type')?.toLowerCase().startsWith('application/json'))throw new InvalidBody('Invalid body');
  const reader=request.body?.getReader();if(!reader)return {};
  let bytes=0;const chunks:Uint8Array[]=[];
  try {
   const timeout=AbortSignal.timeout(5000);
   const stalled=new Promise<never>((_,reject)=>timeout.addEventListener('abort',()=>reject(new InvalidBody('Body unavailable')),{once:true}));
   while(true){
    const item=await Promise.race([reader.read(),stalled]);
    if(item.done)break;bytes+=item.value.byteLength;if(bytes>8192)throw new InvalidBody('Body too large');chunks.push(item.value);
   }
   const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));
   if(!value||typeof value!=='object'||Array.isArray(value))throw new InvalidBody('Invalid body');return value;
  }catch{throw new InvalidBody('Invalid body');}finally{void reader.cancel().catch(()=>{});}
 }
 return async(request:Request):Promise<Response>=>{
  const url=new URL(request.url),path=url.pathname.slice('/management/auth/v1'.length);
  const factor=path.match(new RegExp('^/factors/('+UUID+')(?:/(challenge|verify))?$'));
  const route=path==='/factors'?'enroll':factor?(factor[2]??'unenroll'):path.slice(1);
  const methods:Record<string,string>={token:'POST',user:'GET',logout:'POST',settings:'GET',enroll:'POST',challenge:'POST',verify:'POST',unenroll:'DELETE'};
  if(!methods[route]||(!factor&&!['/token','/user','/logout','/settings','/factors'].includes(path)))return reply(404,{message:'Unknown authentication route'});
  if(request.method!==methods[route])return reply(405,{message:'Method not allowed'});
  if(request.headers.get('apikey')!==realm.publishableKey)return reply(401,{message:'Invalid API key'});
  if(['user','settings','logout','unenroll'].includes(route)&&request.body)return reply(400,{message:'This authentication route does not accept a body'});
  let identity:PasswordIdentity|null=null,payload:Record<string,unknown>|undefined,epoch=0,revoke=false,lease:string|null=null;
  try {
  try {
   if(!security.rate('auth:global',600,60000))return limited();
   if(route==='token'){
    const grant=url.searchParams.get('grant_type');
    if(url.searchParams.size!==1||!['password','refresh_token'].includes(grant??''))return reply(400,{message:'Unsupported grant type'});
    payload=await body(request);
    if(grant==='password'){
     if(Object.keys(payload).some(key=>!['email','password','gotrue_meta_security'].includes(key))||
       typeof payload.email!=='string'||payload.email.length>254||typeof payload.password!=='string'||!payload.password||payload.password.length>4096)return reply(400,{message:'Invalid sign in request'});
     const account=createHash('sha256').update(payload.email.trim().toLowerCase()).digest('hex');
     if(!security.rate('auth:password:global',60,60000)||!security.rate('auth:password:'+account,10,600000))return limited();
    }else if(Object.keys(payload).some(key=>key!=='refresh_token')||typeof payload.refresh_token!=='string'||!payload.refresh_token||payload.refresh_token.length>4096)return reply(400,{message:'Invalid refresh request'});
    else if(!security.rate('auth:refresh',120,60000))return limited();
   }else{
    const allowedQuery=route==='logout'&&url.searchParams.size===1&&['global','local','others'].includes(url.searchParams.get('scope')??'');
    if(url.search&&!allowedQuery)return reply(400,{message:'Invalid authentication query'});
    if(route!=='settings'){
     identity=await identify(request);if(!identity)return reply(401,{message:'Password authentication required'});
     if(!security.rate('auth:actor:'+identity.actor,120,60000))return limited();
     if(['enroll','verify','unenroll'].includes(route)){
      lease=security.lease(identity.actor);if(!lease)return reply(409,{message:'Another account security operation is in progress',code:'mfa_operation_pending'});
      const fresh=await identify(new Request(request.url,{headers:request.headers}));
      if(!fresh||fresh.actor!==identity.actor||fresh.session!==identity.session)return reply(401,{message:'Password authentication required'});
      identity=fresh;
     }
     epoch=security.epoch(identity.actor);
    }
    if(route==='logout')catalog.revokeManagementLogout(identity!.actor,'requested',(url.searchParams.get('scope')??'global') as 'global'|'local'|'others');
    if(route==='enroll'||route==='challenge'||route==='verify'){
     payload=await body(request);
     const owned=factor&&identity!.factors.find(entry=>entry.id===factor[1]&&entry.factor_type==='totp');
     if(factor&&!owned)return reply(403,{message:'Factor unavailable'});
     if(route==='enroll'){
      if(identity!.factors.some(entry=>entry.status==='verified')&&!hasManagementMfa(identity!,security))return reply(403,{message:'MFA verification required',code:'mfa_required'});
      if(payload.factor_type!=='totp'||Object.keys(payload).some(key=>!['factor_type','friendly_name','issuer'].includes(key))||
       (payload.friendly_name!==undefined&&(typeof payload.friendly_name!=='string'||payload.friendly_name.length>100))||
       (payload.issuer!==undefined&&(typeof payload.issuer!=='string'||payload.issuer.length>100)))return reply(400,{message:'Invalid enrollment request'});
     }else if(route==='challenge'){
      if(Object.keys(payload).some(key=>!['factorId'].includes(key)))return reply(400,{message:'Invalid challenge request'});
      payload={};
     }else if(Object.keys(payload).some(key=>!['challenge_id','code'].includes(key))||typeof payload.challenge_id!=='string'||
      !new RegExp('^'+UUID+'$').test(payload.challenge_id)||typeof payload.code!=='string'||!/^\d{6}$/.test(payload.code))return reply(400,{message:'Invalid verification request'});
     const limits={enroll:[5,3600000],challenge:[30,600000],verify:[10,600000]} as const;
     if(!security.rate('auth:'+route+':'+identity!.actor,limits[route][0],limits[route][1]))return limited();
    }
    if(route==='unenroll'){
     const owned=identity!.factors.find(entry=>entry.id===factor![1]&&entry.factor_type==='totp');
     if(!owned)return reply(403,{message:'Factor unavailable'});
     if(owned.status==='verified'&&(!hasManagementMfa(identity!,security)||
       !identity!.factors.some(entry=>entry.id!==owned.id&&entry.status==='verified'&&entry.factor_type==='totp')))
       return reply(403,{message:'Verify another authenticator before removing this one'});
     if(!security.rate('auth:unenroll:'+identity!.actor,5,3600000))return limited();
     revoke=owned.status==='verified';
     if(revoke)security.revoke(identity!.actor);catalog.recordManagementFactor(identity!.actor,'removal_requested',owned.id);
    }
   }
  }catch(error){return error instanceof InvalidBody?reply(400,{message:'Invalid authentication body'}):unavailable();}
  let response:Response;
  try {
   if(lease&&!security.holds(identity!.actor,lease))throw new Error('Security lease expired');
   const headers=new Headers({'apikey':realm.anonymousToken,'authorization':request.headers.get('authorization')??'Bearer '+realm.anonymousToken});
   if(payload!==undefined)headers.set('content-type','application/json');
   response=await transport(new URL(path+url.search,realm.auth),{method:request.method,headers,redirect:'error',
    signal:AbortSignal.timeout(5000),...(payload===undefined?{}:{body:JSON.stringify(payload)})});
   if(lease&&!security.holds(identity!.actor,lease))throw new Error('Security lease expired');
   if(response.ok&&route==='verify'){
    const result=await response.clone().json() as {access_token?:string};
    if(typeof result.access_token!=='string')return unavailable();
    const checked=await identify(new Request(request.url,{headers:{authorization:'Bearer '+result.access_token}}));
    if(lease&&!security.holds(identity!.actor,lease))throw new Error('Security lease expired');
    if(!checked||checked.actor!==identity!.actor||checked.session!==identity!.session||checked.aal!=='aal2'||!checked.verifiedAt||
      !checked.factors.some(entry=>entry.id===factor![1]&&entry.status==='verified'&&entry.factor_type==='totp'))return unavailable();
    if(!catalog.grantManagementMfa(checked.actor,checked.session,factor![1]!,checked.verifiedAt,checked.passwordAt+43200,epoch))
     return reply(409,{message:'Wait for a new authenticator code and verify again',code:'mfa_retry'});
   }
   if(response.ok&&route==='enroll'){
    const result=await response.clone().json() as {id?:string};
    if(typeof result.id!=='string')return unavailable();catalog.recordManagementFactor(identity!.actor,'enrolled',result.id);
   }
   if(route==='unenroll'){
    if(revoke)security.revoke(identity!.actor);
    catalog.recordManagementFactor(identity!.actor,response.ok?'removed':'removal_failed',factor![1]!);
   }
   if(route==='logout')catalog.revokeManagementLogout(identity!.actor,response.ok?'succeeded':'failed',(url.searchParams.get('scope')??'global') as 'global'|'local'|'others');
  }catch{
   if(route==='unenroll')try{if(revoke)security.revoke(identity!.actor);catalog.recordManagementFactor(identity!.actor,'removal_failed',factor![1]!);}catch{}
   if(route==='logout')try{catalog.revokeManagementLogout(identity!.actor,'failed',(url.searchParams.get('scope')??'global') as 'global'|'local'|'others');}catch{}
   return unavailable();
  }
  const headers=new Headers(response.headers);headers.set('cache-control','no-store');headers.set('x-content-type-options','nosniff');
  for(const name of [...headers.keys()])if(name.startsWith('access-control-')||name==='set-cookie')headers.delete(name);
  return new Response(response.body,{status:response.status,headers});
  }finally{if(lease&&identity)try{security.release(identity.actor,lease);}catch{}}
 };
}
