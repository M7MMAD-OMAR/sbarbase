import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managementPasswordIdentity} from '../src/control/auth';
import {managementToken,sessionId,factorId,otherFactorId,challengeId,verifiedFactor} from './management-fixture';

const now=Math.floor(Date.now()/1000);
const realm={auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'};
const nextSession='55555555-5555-4555-8555-555555555555';
function request(path:string,token:string,method='GET',payload?:unknown){
 return new Request('http://local/management/'+path,{method,headers:{apikey:'public',authorization:'Bearer '+token,
  ...(payload===undefined?{}:{'content-type':'application/json'})},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
}
function fixture(catalog:Catalog,keys:KeyStore,options:{factors?:typeof verifiedFactor[];verifyStatus?:number;logoutStatus?:number;logoutThrows?:boolean}={}){
 let factors=options.factors??[verifiedFactor];const calls:string[]=[];
 const transport=(async(input,init)=>{
  const url=new URL(String(input));calls.push(url.pathname);
  if(url.pathname==='/user'||url.pathname==='/auth/v1/user'){
   const token=new Headers(init?.headers).get('authorization')?.slice(7)??'';
   if(token==='invalid')return Response.json({msg:'Invalid token'},{status:401});
   const claims=JSON.parse(Buffer.from(token.split('.')[1]!,'base64url').toString('utf8'));
   return Response.json({id:claims.sub,is_anonymous:false,factors,user_metadata:{aal:'aal2'}});
  }
  if(url.pathname.endsWith('/verify')){
   if(options.verifyStatus)return Response.json({message:'Invalid code'},{status:options.verifyStatus});
   factors=factors.map(factor=>({...factor,status:'verified'}));
   const token=new Headers(init?.headers).get('authorization')!.slice(7);
   const claims=JSON.parse(Buffer.from(token.split('.')[1]!,'base64url').toString('utf8'));
   return Response.json({access_token:managementToken(claims.sub,'aal2',claims.session_id,now),refresh_token:'refresh',expires_in:3600,
    user:{id:claims.sub,factors}});
  }
  if(url.pathname==='/logout'){
   if(options.logoutThrows)throw new Error('Private native logout failure');
   return options.logoutStatus?Response.json({message:'Logout refused'},{status:options.logoutStatus}):new Response(null,{status:204});
  }
  if(init?.method==='DELETE'){factors=factors.filter(factor=>!url.pathname.endsWith(factor.id));return Response.json({id:factorId});}
  if(url.pathname==='/factors')return Response.json({id:otherFactorId,type:'totp',totp:{secret:'native-secret',qr_code:'native-qr',uri:'native-uri'}});
  return Response.json({id:challengeId});
 }) as typeof fetch;
 return {handler:application(catalog,keys,realm,()=>undefined,transport),transport,calls,setFactors:(next:typeof factors)=>{factors=next;}};
}

test('native password session cannot call any protected management API before proxy MFA verification',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const {handler}=fixture(catalog,keys),password=managementToken('owner','aal1',sessionId,now),nativeOnly=managementToken('owner','aal2',sessionId,now);
  for(const path of ['v1/organizations','v1/updates','v1/environments/'+factorId+'/connection','v1/environments/'+factorId+'/signing-key/rotate']){
   expect((await handler(request(path,password))).status).toBe(403);
   expect((await handler(request(path,nativeOnly))).status).toBe(403);
  }
  const verify=await handler(request('auth/v1/factors/'+factorId+'/verify',password,'POST',{challenge_id:challengeId,code:'123456'}));
  expect(verify.status).toBe(200);
  const token=(await verify.json()).access_token;
  expect((await handler(request('v1/organizations',token))).status).toBe(200);
  expect((await handler(request('v1/organizations',managementToken('owner','aal2',nextSession,now)))).status).toBe(403);
 }finally{catalog.close();keys.close();}
});

test('password claims require native validation, native password AMR, bounded session age and session id',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const {transport}=fixture(catalog,keys);
  const password=managementPasswordIdentity('http://realm.invalid','public',transport);
  for(const token of ['invalid',managementToken('owner','aal2',sessionId,now,{amr:[{method:'otp',timestamp:now}]}),
   managementToken('owner','aal2',sessionId,now,{amr:[{method:'password',timestamp:now-43201},{method:'totp',timestamp:now}]}),
   managementToken('owner','aal2',sessionId,now,{session_id:'spoof'}),managementToken('owner','aal2',sessionId,now,{exp:now-1})]){
   expect(await password(request('v1/organizations',token))).toBeNull();
  }
  const retained=await password(request('v1/organizations',managementToken('owner','aal1',sessionId,now)));
  expect(retained?.aal).toBe('aal1');expect(retained?.expiresAt).toBe(now+3600);
  expect(retained?.passwordAt).toBe(now-5);
 }finally{catalog.close();keys.close();}
});

test('existing MFA blocks password-only enrollment, factor substitution and removal of the last verified factor',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const {handler,calls}=fixture(catalog,keys),password=managementToken('owner','aal1',sessionId,now),mfa=managementToken('owner','aal2',sessionId,now);
  expect((await handler(request('auth/v1/factors',password,'POST',{factor_type:'totp'}))).status).toBe(403);
  expect((await handler(request('auth/v1/factors/'+otherFactorId+'/verify',password,'POST',{challenge_id:challengeId,code:'123456'}))).status).toBe(403);
  catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
  expect((await handler(request('auth/v1/factors/'+factorId,mfa,'DELETE'))).status).toBe(403);
  expect(calls.every(path=>path==='/user')).toBe(true);
  expect((await handler(request('auth/v1/factors',mfa,'POST',{factor_type:'phone'}))).status).toBe(400);
 }finally{catalog.close();keys.close();}
});

test('native factor removal revokes every session, and stale aal2 tokens with a removed factor fail closed',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const {handler,setFactors}=fixture(catalog,keys,{factors:[verifiedFactor,{...verifiedFactor,id:otherFactorId}]}),mfa=managementToken('owner','aal2',sessionId,now),other=managementToken('owner','aal2',nextSession,now);
  catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
  catalog.managementSecurity.grant('owner',nextSession,otherFactorId,now,now+3600,0);
  expect((await handler(request('auth/v1/factors/'+factorId,mfa,'DELETE'))).status).toBe(200);
  expect((await handler(request('v1/organizations',mfa))).status).toBe(403);
  expect((await handler(request('v1/organizations',other))).status).toBe(403);
  // A direct native/admin removal is also detected through the current factor list.
  catalog.managementSecurity.grant('owner',sessionId,otherFactorId,now,now+3600,catalog.managementSecurity.epoch('owner'));
  setFactors([]);expect((await handler(request('v1/organizations',mfa))).status).toBe(403);
 }finally{catalog.close();keys.close();}
});

test('verification attempts share durable actor rate budgets across sessions and controller restarts',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'management-mfa-')),path=join(directory,'catalog.sqlite');
 const keys=new KeyStore(':memory:');let catalog=new Catalog(path);
 try{
  let {handler}=fixture(catalog,keys,{verifyStatus:400});
  for(let index=0;index<10;index++)expect((await handler(request('auth/v1/factors/'+factorId+'/verify',managementToken('owner','aal1',index%2?nextSession:sessionId,now),'POST',{challenge_id:challengeId,code:'111111'}))).status).toBe(400);
  catalog.close();catalog=new Catalog(path);handler=fixture(catalog,keys,{verifyStatus:400}).handler;
  expect((await handler(request('auth/v1/factors/'+factorId+'/verify',managementToken('owner','aal1',nextSession,now),'POST',{challenge_id:challengeId,code:'111111'}))).status).toBe(429);
 }finally{catalog.close();keys.close();rmSync(directory,{recursive:true,force:true});}
});

test('failed MFA native transport and closed rate store cannot grant management access',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const transport=(async()=>{throw new Error('private native diagnostic');}) as unknown as typeof fetch;
 const handler=application(catalog,keys,realm,()=>undefined,transport),token=managementToken('owner','aal1',sessionId,now);
 expect((await handler(request('auth/v1/user',token))).status).toBe(503);
 catalog.close();
 const result=await handler(request('auth/v1/token?grant_type=password',token,'POST',{email:'owner@example.com',password:'strong-password'}));
 expect(result.status).toBe(503);expect(await result.text()).not.toContain('private');keys.close();
});

test('durable mutation lease serializes removals and survives reopening until its bounded expiry',()=>{
 const directory=mkdtempSync(join(tmpdir(),'management-lease-')),path=join(directory,'catalog.sqlite');let catalog=new Catalog(path);
 try{
  const nonce=catalog.managementSecurity.lease('owner',1000)!;expect(nonce).toBeString();
  expect(catalog.managementSecurity.holds('owner',nonce,1001)).toBe(true);expect(catalog.managementSecurity.holds('owner','wrong',1001)).toBe(false);
  expect(catalog.managementSecurity.holds('owner',nonce,61000)).toBe(false);
  expect(catalog.managementSecurity.lease('owner',1001)).toBeNull();
  catalog.close();catalog=new Catalog(path);
  expect(catalog.managementSecurity.lease('owner',60999)).toBeNull();
  catalog.managementSecurity.release('owner','wrong-nonce');expect(catalog.managementSecurity.lease('owner',60999)).toBeNull();
  const next=catalog.managementSecurity.lease('owner',61000)!;expect(next).not.toBe(nonce);
  catalog.managementSecurity.release('owner',nonce);expect(catalog.managementSecurity.lease('owner',61001)).toBeNull();
  catalog.managementSecurity.release('owner',next);expect(catalog.managementSecurity.lease('owner',61001)).toBeString();
 }finally{catalog.close();rmSync(directory,{recursive:true,force:true});}
});

test('password rate budget is shared by email, ignores untrusted client addresses and denies extra native grants',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');let calls=0;
 const handler=application(catalog,keys,realm,()=>undefined,(async()=>{calls++;return Response.json({message:'Invalid credentials'},{status:400});}) as unknown as typeof fetch);
 try{
  for(let index=0;index<11;index++){
   const login=request('auth/v1/token?grant_type=password','public','POST',{email:index%2?' OWNER@example.com ':'owner@example.com',password:'incorrect'});
   login.headers.set('x-forwarded-for','10.0.0.'+index);
   expect((await handler(login)).status).toBe(index<10?400:429);
  }
  expect(calls).toBe(10);
  expect((await handler(request('auth/v1/token?grant_type=pkce','public','POST',{auth_code:'code'}))).status).toBe(400);
  expect((await handler(request('auth/v1/verify','public','POST',{type:'recovery',token:'code'}))).status).toBe(404);
 }finally{catalog.close();keys.close();}
});

test('MFA grants cannot survive a concurrent recovery epoch and owner-only audit omits secrets',()=>{
 const catalog=new Catalog(':memory:');
 try{
  const org=catalog.initializeInstallation('bootstrap-op','operator','Install');catalog.setMember('operator',org,'admin','admin');
  const actor='66666666-6666-4666-8666-666666666666';
  expect(()=>catalog.revokeManagementMfa('admin',actor,'lost_factor')).toThrow('Forbidden');
  expect(()=>catalog.managementSecurityAudit('admin')).toThrow('Forbidden');
  const epoch=catalog.managementSecurity.epoch(actor),receipt=catalog.revokeManagementMfa('operator',actor,'lost_factor');
  expect(catalog.managementSecurity.grant(actor,sessionId,factorId,now,now+3600,epoch)).toBe(false);
  expect(catalog.managementSecurity.grant(actor,sessionId,factorId,Math.floor(Date.now()/1000),now+3600,catalog.managementSecurity.epoch(actor))).toBe(false);
  catalog.finishManagementMfaRecovery('operator',actor,'completed',receipt);
  expect(catalog.managementSecurityAudit('operator').map(event=>event.action)).toEqual(['management.mfa.recovery_completed','management.mfa.recovery_requested']);
  expect(JSON.stringify(catalog.managementSecurityAudit('operator'))).not.toContain('secret');
 }finally{catalog.close();}
});

test('every native logout scope invalidates all management sessions before and after success or failure',async()=>{
 for(const scope of ['global','local','others'])for(const failure of ['none','response','transport']){
  const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
  try{
   catalog.initializeInstallation('bootstrap-op','owner','Installation');
   const {handler}=fixture(catalog,keys,{logoutStatus:failure==='response'?500:undefined,logoutThrows:failure==='transport'});
   catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);catalog.managementSecurity.grant('owner',nextSession,factorId,now,now+3600,0);
   const token=managementToken('owner','aal2',sessionId,now),other=managementToken('owner','aal2',nextSession,now);
   expect((await handler(request('auth/v1/logout?scope='+scope,token,'POST'))).status).toBe(failure==='none'?204:failure==='response'?500:503);
   expect(catalog.managementSecurity.epoch('owner')).toBe(2);
   expect((await handler(request('v1/organizations',token))).status).toBe(403);expect((await handler(request('v1/organizations',other))).status).toBe(403);
   const audit=catalog.managementSecurityAudit('owner');
   expect(audit.map(event=>event.action)).toEqual(['management.mfa.logout_'+(failure==='none'?'succeeded':'failed'),'management.mfa.logout_requested']);
   expect(audit.every(event=>event.detail.scope===scope)).toBe(true);
  }finally{catalog.close();keys.close();}
 }
});

test('unexpected logout and unenroll bodies are refused before native calls or grant revocation',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const {handler,calls}=fixture(catalog,keys,{factors:[verifiedFactor,{...verifiedFactor,id:otherFactorId}]}),token=managementToken('owner','aal2',sessionId,now);
  catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
  for(const [path,method] of [['auth/v1/logout?scope=local','POST'],['auth/v1/factors/'+factorId,'DELETE']]){
   for(const payload of [{},{unexpected:'x'.repeat(10000)}])expect((await handler(request(path!,token,method,payload))).status).toBe(400);
  }
  expect(calls).toEqual([]);expect(catalog.managementSecurity.epoch('owner')).toBe(0);
  expect((await handler(request('v1/organizations',token))).status).toBe(200);
 }finally{catalog.close();keys.close();}
});

test('recovery terminal audit needs its one-use pending receipt and survives initiator demotion',()=>{
 const directory=mkdtempSync(join(tmpdir(),'management-receipt-')),path=join(directory,'catalog.sqlite'),target='66666666-6666-4666-8666-666666666666';let catalog=new Catalog(path);
 try{
  const org=catalog.initializeInstallation('bootstrap','owner','Installation');catalog.setMember('owner',org,'replacement','owner');
  const receipt=catalog.revokeManagementMfa('owner',target,'lost_factor');catalog.setMember('replacement',org,'owner','viewer');
  catalog.close();catalog=new Catalog(path);
  expect(()=>catalog.assertManagementRecoveryOwner('owner')).toThrow('Forbidden');
  expect(()=>catalog.revokeManagementMfa('owner',target,'lost_factor')).toThrow('Forbidden');
  expect(()=>catalog.finishManagementMfaRecovery('replacement',target,'failed',receipt)).toThrow('Invalid recovery receipt');
  expect(()=>catalog.finishManagementMfaRecovery('owner',otherFactorId,'failed',receipt)).toThrow('Invalid recovery receipt');
  expect(()=>catalog.finishManagementMfaRecovery('owner',target,'failed',otherFactorId)).toThrow('Invalid recovery receipt');
  catalog.finishManagementMfaRecovery('owner',target,'failed',receipt);
  expect(()=>catalog.finishManagementMfaRecovery('owner',target,'completed',receipt)).toThrow('Invalid recovery receipt');
  const events=catalog.managementSecurityAudit('replacement');expect(events[0]?.action).toBe('management.mfa.recovery_failed');
  expect(events[0]?.actor).toBe('owner');expect(events.every(event=>event.detail.receipt===receipt)).toBe(true);
 }finally{catalog.close();rmSync(directory,{recursive:true,force:true});}
});

function suspendedRequest(path:string,token:string,payload:unknown){
 let reading!:()=>void,resume!:()=>void;
 const read=new Promise<void>(resolve=>{reading=resolve;}),continued=new Promise<void>(resolve=>{resume=resolve;});
 const stream=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new TextEncoder().encode(' '));},async pull(controller){reading();await continued;controller.enqueue(new TextEncoder().encode(JSON.stringify(payload)));controller.close();}});
 return {request:new Request('http://local/management/'+path,{method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json'},body:stream}),read,resume};
}

test('a streamed management body cannot mutate after local grant revocation and scope stays isolated',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const org=catalog.initializeInstallation('bootstrap','owner','Installation');catalog.setMember('owner',org,'other-owner','owner');
  catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);catalog.managementSecurity.grant('other-owner',nextSession,factorId,now,now+3600,0);
  const {handler}=fixture(catalog,keys),pending=suspendedRequest('v1/organizations',managementToken('owner','aal2',sessionId,now),{name:'Must not exist'});
  const result=handler(pending.request);await pending.read;catalog.managementSecurity.revoke('owner');
  expect((await handler(request('v1/organizations',managementToken('other-owner','aal2',nextSession,now),'POST',{name:'Other account works'}))).status).toBe(201);
  pending.resume();const denied=await result;expect(denied.status).toBe(403);expect((await denied.json()).code).toBe('mfa_required');
  expect(catalog.listOrganizations('owner').map(org=>org.name)).toEqual(['Installation']);
  expect(catalog.createOrganization('worker','Private worker has no HTTP scope')).toBeString();
 }finally{catalog.close();keys.close();}
});

test('a streamed management body rechecks current membership before project mutation',async()=>{
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 try{
  const org=catalog.createOrganization('owner','Tenant');catalog.setMember('owner',org,'member','admin');
  catalog.managementSecurity.grant('member',sessionId,factorId,now,now+3600,0);
  const {handler}=fixture(catalog,keys),pending=suspendedRequest('v1/organizations/'+org+'/projects',managementToken('member','aal2',sessionId,now),{name:'Must not exist'});
  const result=handler(pending.request);await pending.read;catalog.setMember('owner',org,'member','viewer');pending.resume();
  expect((await result).status).toBe(403);expect(catalog.listProjects('owner',org)).toEqual([]);
 }finally{catalog.close();keys.close();}
});
