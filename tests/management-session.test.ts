import {test,expect,afterEach} from 'bun:test';
import {managementSessionSeconds,DEFAULT_MANAGEMENT_SESSION_SECONDS} from '../src/control/management-session';
import {Catalog} from '../src/control/catalog';
import {hasManagementAccess} from '../src/control/auth';
import {managementPasswordIdentity} from '../src/control/auth';
import {managementToken,sessionId,verifiedFactor} from './management-fixture';

const original=process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS;
afterEach(()=>{if(original===undefined)delete process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS;else process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS=original;});

test('the default is thirty days when nothing is set',()=>{
 delete process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS;
 expect(managementSessionSeconds()).toBe(2592000);
 expect(DEFAULT_MANAGEMENT_SESSION_SECONDS).toBe(2592000);
});

test('values inside the range are used exactly, including both ends',()=>{
 for(const [value,seconds] of [['3600',3600],['86400',86400],['604800',604800],['2592000',2592000]] as const)
  expect(managementSessionSeconds(value)).toBe(seconds);
});

test('anything malformed or out of range falls back to the default',()=>{
 for(const value of ['','  ','abc','1e5','-86400','3599','2592001','99999999','086400 ',' 86400','86400.5','0x10000','١٢٣٤٥٦٧'])
  expect(managementSessionSeconds(value)).toBe(2592000);
});

test('the environment variable is read on each call',()=>{
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='7200';expect(managementSessionSeconds()).toBe(7200);
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='nonsense';expect(managementSessionSeconds()).toBe(2592000);
});

/** A signed-in password session whose password is `age` seconds old, with a token that is still unexpired. */
async function identityAged(age:number){
 const now=Math.floor(Date.now()/1000);
 const token='eyJhbGciOiJIUzI1NiJ9.'+Buffer.from(JSON.stringify({sub:'owner',session_id:sessionId,exp:now+3600,aal:'aal2',
  amr:[{method:'password',timestamp:now-age},{method:'totp',timestamp:now-age+5}]})).toString('base64url')+'.fixture';
 const transport=(async()=>Response.json({id:'owner',is_anonymous:false,factors:[verifiedFactor]})) as unknown as typeof fetch;
 return managementPasswordIdentity('http://realm.invalid','public',transport)(new Request('http://x/',{headers:{authorization:'Bearer '+token}}));
}

test('the default keeps a sign-in for thirty days and a shorter cap ends it sooner',async()=>{
 delete process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS;
 expect((await identityAged(50000))?.actor).toBe('owner');
 expect(await identityAged(2592000+60)).toBeNull();
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='43200';
 expect(await identityAged(50000)).toBeNull();
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='2592000';
 expect((await identityAged(50000))?.actor).toBe('owner');
 expect((await identityAged(2000000))?.actor).toBe('owner');
 expect(await identityAged(2592000+60)).toBeNull();
});

test('a lowered cap shortens sessions and an invalid value never lengthens them',async()=>{
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='3600';
 expect(await identityAged(1000)).not.toBeNull();
 expect(await identityAged(4000)).toBeNull();
 process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='99999999999';
 expect(await identityAged(2592000+60)).toBeNull();
});


test('remembered optional password sessions obey the configured cap and durable revocation',()=>{
 const catalog=new Catalog(':memory:');
 try{
  catalog.initializeInstallation('bootstrap','owner','Install');catalog.setManagementMfaPolicy('owner',false);
  const now=Math.floor(Date.now()/1000),passwordAt=now-50000;
  const identity={actor:'owner',session:sessionId,aal:'aal1' as const,expiresAt:now+3600,passwordAt,verifiedAt:0,factors:[]};
  const security=catalog.managementSecurity;
  security.grantPassword('owner',sessionId,passwordAt,now+86400,security.epoch('owner'),security.policy().epoch);
  process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='43200';expect(hasManagementAccess(identity,security)).toBe(false);
  process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS='2592000';expect(hasManagementAccess(identity,security)).toBe(true);
  security.revoke('owner');expect(hasManagementAccess(identity,security)).toBe(false);
 }finally{catalog.close();}
});
