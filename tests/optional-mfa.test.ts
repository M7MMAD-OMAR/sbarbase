import {test,expect} from 'bun:test';
import {Catalog,ENVIRONMENT_LIMIT} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {hasManagementAccess,type PasswordIdentity} from '../src/control/auth';
import {managementToken,sessionId,factorId,verifiedFactor} from './management-fixture';
const now=Math.floor(Date.now()/1000);
function fixture(){
 const catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');
 const org=catalog.initializeInstallation('bootstrap','owner','Install');
 let factors:PasswordIdentity['factors']=[];
 let duringLookup:(()=>void)|undefined;
 const transport=Object.assign(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const path=new URL(String(input)).pathname;
  if(path==='/token')return Response.json({access_token:managementToken('owner','aal1',sessionId,now),refresh_token:'fixture',expires_in:3600,user:{id:'owner',factors}});
  if(path==='/user'){duringLookup?.();return Response.json({id:'owner',factors});}
  if(init?.method==='DELETE'){factors=factors.filter(factor=>!path.endsWith(factor.id));return Response.json({id:factorId});}
  if(path==='/logout')return new Response(null,{status:204});
  return Response.json({message:'unavailable'},{status:404});
 },{preconnect:()=>{}}) as typeof fetch;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport);
 const call=(path:string,method='GET',body?:unknown,token=managementToken('owner','aal1',sessionId,now))=>handler(new Request('http://local/management/'+path,
  {method,headers:{apikey:'public',authorization:'Bearer '+token,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
 return {catalog,org,call,set factors(next:PasswordIdentity['factors']){factors=next;},set duringLookup(next:(()=>void)|undefined){duringLookup=next;},
  login:()=>call('auth/v1/token?grant_type=password','POST',{email:'owner@example.com',password:'fixture-password'}),close(){catalog.close();keys.close();}};
}
test('optional policy is durable, defaults required and only audited password sessions with no verified factors enter',async()=>{
 const f=fixture();try{
  expect(f.catalog.managementSecurity.policy().mfa_required).toBe(true);
  expect((await f.login()).status).toBe(200);
  expect((await f.call('v1/organizations')).status).toBe(403);
  f.catalog.setManagementMfaPolicy('owner',false);
  expect((await f.call('v1/organizations')).status).toBe(403);
  expect((await f.login()).status).toBe(200);
  expect((await f.call('v1/organizations')).status).toBe(200);
  f.factors=[{...verifiedFactor,factor_type:'phone'}];
  expect((await f.call('v1/organizations')).status).toBe(403);
 }finally{f.close();}
});
test('native logout revokes password grants even if native Auth keeps accepting the old token',async()=>{
 const f=fixture();try{f.catalog.setManagementMfaPolicy('owner',false);await f.login();
  expect((await f.call('auth/v1/logout?scope=local','POST')).status).toBe(204);
  expect((await f.call('v1/organizations')).status).toBe(403);
 }finally{f.close();}
});
test('policy toggles invalidate all actor epochs, password grants and saved session access',async()=>{
 const f=fixture();try{f.catalog.setManagementMfaPolicy('owner',false);await f.login();
  const epoch=f.catalog.managementSecurity.epoch('owner');
  f.catalog.setManagementMfaPolicy('owner',true);f.catalog.setManagementMfaPolicy('owner',false);
  expect(f.catalog.managementSecurity.epoch('owner')).toBeGreaterThan(epoch);
  expect((await f.call('v1/organizations')).status).toBe(403);
 }finally{f.close();}
});
test('password request notices a factor enrolled during its final native refresh',async()=>{
 const f=fixture();try{f.catalog.setManagementMfaPolicy('owner',false);await f.login();let count=0;
  f.duringLookup=()=>{if(++count===2)f.factors=[verifiedFactor];};
  expect((await f.call('v1/organizations')).status).toBe(403);
 }finally{f.close();}
});
test('policy endpoint requires current installation owner and a fresh locally granted native factor',async()=>{
 const f=fixture();try{f.factors=[verifiedFactor];const token=managementToken('owner','aal2',sessionId,now);
  expect((await f.call('auth/v1/policy','PATCH',{mfa_required:false},token)).status).toBe(403);
  f.catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
  expect((await f.call('auth/v1/policy','PATCH',{mfa_required:'false'},token)).status).toBe(400);
  const old=managementToken('owner','aal2',sessionId,now,{amr:[{method:'password',timestamp:now-1000},{method:'totp',timestamp:now-301}]});
  f.catalog.managementSecurity.grant('owner',sessionId,factorId,now-301,now+3600,0);
  expect((await f.call('auth/v1/policy','PATCH',{mfa_required:false},old)).status).toBe(403);
  f.catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
  expect((await f.call('auth/v1/policy','PATCH',{mfa_required:false},token)).status).toBe(200);
  expect(f.catalog.managementSecurity.policy().mfa_required).toBe(false);
  expect(f.catalog.managementSecurity.epoch('owner')).toBe(1);
  expect((await f.call('v1/organizations','GET',undefined,token)).status).toBe(403);
 }finally{f.close();}
});
test('optional policy allows an MFA verified account to remove its last factor then requires a new password login',async()=>{
 const f=fixture();try{f.catalog.managementSecurity.setPolicy(false);f.factors=[verifiedFactor];
  const token=managementToken('owner','aal2',sessionId,now);
  f.catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,f.catalog.managementSecurity.epoch('owner'));
  expect((await f.call('auth/v1/factors/'+factorId,'DELETE',undefined,token)).status).toBe(200);
  expect((await f.call('v1/organizations')).status).toBe(403);await f.login();
  expect((await f.call('v1/organizations')).status).toBe(200);
 }finally{f.close();}
});
test('project creation queues production atomically and capacity refusal rolls back metadata and audit',async()=>{
 const f=fixture();try{f.catalog.setManagementMfaPolicy('owner',false);await f.login();
  const response=await f.call(`v1/organizations/${f.org}/projects`,'POST',{name:'First'});
  expect(response.status).toBe(201);const result=await response.json();
  expect(result.state).toBe('queued');expect(f.catalog.listEnvironments('owner',result.id)).toMatchObject([{id:result.environment,name:'production',state:'queued'}]);
  expect((await f.call(`v1/organizations/${f.org}/projects`,'POST',{name:'First'})).status).toBe(409);
  for(let i=1;i<ENVIRONMENT_LIMIT;i++)f.catalog.createEnvironment('owner',result.id,'extra'+i);
  expect((await f.call(`v1/organizations/${f.org}/projects`,'POST',{name:'Full'})).status).toBe(409);
  expect(f.catalog.listProjects('owner',f.org).map(project=>project.name)).toEqual(['First']);
 }finally{f.close();}
});
test('password access never accepts stale aal2 or an unaudited session',()=>{
 const f=fixture();try{f.catalog.setManagementMfaPolicy('owner',false);
  const identity:PasswordIdentity={actor:'owner',session:sessionId,aal:'aal1',expiresAt:now+100,passwordAt:now-5,verifiedAt:0,factors:[]};
  expect(hasManagementAccess(identity,f.catalog.managementSecurity)).toBe(false);
  f.catalog.managementSecurity.grantPassword('owner',sessionId,now-5,now+100,f.catalog.managementSecurity.epoch('owner'),f.catalog.managementSecurity.policy().epoch);
  expect(hasManagementAccess(identity,f.catalog.managementSecurity)).toBe(true);
  expect(hasManagementAccess({...identity,aal:'aal2',verifiedAt:now},f.catalog.managementSecurity)).toBe(false);
 }finally{f.close();}
});
