import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {application} from '../src/control/application';
import {KeyStore} from '../src/control/keys';
import {managementToken,sessionId,factorId,challengeId,verifiedFactor} from './management-fixture';
import {recoverSoleManagementOwner} from '../lab/management-sole-owner-recovery';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';

const target='11111111-1111-4111-8111-111111111111',factor='22222222-2222-4222-8222-222222222222',receipt='33333333-3333-4333-8333-333333333333';
function fixture(){const catalog=new Catalog(':memory:');const organization=catalog.initializeInstallation('bootstrap',target,'Installation');return {catalog,organization};}
test('sole owner guards reject zero or two current bootstrap owners',()=>{
 const {catalog,organization}=fixture();try{
  catalog.assertSoleManagementOwner(target);catalog.setMember(target,organization,'other','owner');
  expect(()=>catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt)).toThrow('Sole');
  catalog.setMember(target,organization,'other','viewer');
  (catalog as unknown as {db:{query(sql:string):{run():void}}}).db.query("DELETE FROM memberships WHERE role='owner'").run();
  expect(()=>catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt)).toThrow('Sole');
 }finally{catalog.close();}
});
test('durable failed host receipt denies all grants even with optional MFA and no native factors',()=>{
 const {catalog}=fixture();try{
  const security=catalog.managementSecurity,now=Math.floor(Date.now()/1000);security.setPolicy(false);
  expect(security.grantPassword(target,'old',now,now+100,0,security.policy().epoch)).toBe(true);
  catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt);
  expect(security.tokenEpoch(target)).toBe(1);expect(security.passwordGranted(target,'old',now)).toBe(false);
  expect(security.grantPassword(target,'new',now,now+100,security.epoch(target),security.policy().epoch)).toBe(false);
  expect(security.grant(target,'mfa',factor,now+1,now+100,security.epoch(target))).toBe(false);
  catalog.finishSoleOwnerRecovery(target,factor,receipt,'failed');expect(security.recovering(target)).toBe(true);
  expect(()=>catalog.beginSoleOwnerRecovery(target,factor,'lost_factor','44444444-4444-4444-8444-444444444444')).toThrow('receipt');
  expect(()=>catalog.beginSoleOwnerRecovery(target,factor,'compromised_factor',receipt,true)).toThrow('receipt');
  catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt,true);catalog.prepareSoleOwnerRecovery(target,factor,receipt);expect(catalog.grantManagementMfa(target,'restored',factor,now+1,now+100,security.epoch(target))).toBe(true);
  expect(security.recovering(target)).toBe(false);expect(security.policy().mfa_required).toBe(false);
  const audit=catalog.managementSecurityAudit(target);expect(audit.filter(row=>row.action.includes('sole_owner')).every(row=>row.actor==='system:host')).toBe(true);
  expect(JSON.stringify(audit)).not.toContain('secret');
 }finally{catalog.close();}
});
test('failed recovery survives reopen and ordinary two-person recovery remains distinct',()=>{
 const directory=mkdtempSync(join(tmpdir(),'sole-latch-')),path=join(directory,'catalog.sqlite');let catalog=new Catalog(path);
 try{
  catalog.initializeInstallation('bootstrap',target,'Installation');catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt);catalog.close();catalog=new Catalog(path);
  expect(catalog.managementSecurity.recovering(target)).toBe(true);
  catalog.finishSoleOwnerRecovery(target,factor,receipt,'failed');catalog.close();catalog=new Catalog(path);
  expect(catalog.managementSecurity.recovering(target)).toBe(true);
  expect(()=>catalog.finishManagementMfaRecovery(target,target,'completed',receipt)).toThrow('receipt');
 }finally{catalog.close();rmSync(directory,{recursive:true,force:true});}
});
test('non-root module and CLI refuse before parsing credentials or host discovery',async()=>{
 if(process.geteuid?.()===0)return;
 await expect(recoverSoleManagementOwner({target,factor,targetPassword:'private-test-password',reason:'lost_factor'})).rejects.toThrow('refused');
 const result=Bun.spawnSync([process.execPath,'lab/management-sole-owner-recovery.ts'],{stdin:Buffer.from(JSON.stringify({target,factor,targetPassword:'private-test-password',reason:'lost_factor'})),stdout:'pipe',stderr:'pipe'});
 expect(result.exitCode).toBe(1);expect(result.stdout.toString()).toBe('');expect(result.stderr.toString()).not.toContain('private-test-password');
});

// Isolated subprocess mocks only the OS installation contract, not the native operation order.
// These tests do not prove actual OS root admission or native Auth integration.
const simulation=String.raw`
import {mock} from 'bun:test';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const scenario=process.argv[1],source=process.argv[2],root=fs.mkdtempSync(join(tmpdir(),'sole-engine-'));
const target='11111111-1111-4111-8111-111111111111',old='22222222-2222-4222-8222-222222222222',replacement='55555555-5555-4555-8555-555555555555';
const nativeFs={...fs};let calls=[],catalog,organization,fail=true;
fs.mkdirSync(root+'/.lab/upstream',{recursive:true});
fs.writeFileSync(root+'/.lab/upstream/server.json',JSON.stringify({url:'http://127.0.0.1:7001',pid:22}));
fs.writeFileSync(root+'/.lab/upstream/supervisor.json',JSON.stringify({pid:11,serverPid:22}));
const map=p=>String(p).startsWith('/var/lib/sbarbase')?root+'/private'+String(p).slice('/var/lib/sbarbase'.length):p;
mock.module('node:fs',()=>({...nativeFs,
 realpathSync(p){if(String(p).startsWith('/proc/'))return root;if(String(p)===source)return root+'/lab/management-sole-owner-recovery.ts';return nativeFs.realpathSync(p);},
 readFileSync(p,...a){if(String(p).endsWith('/cmdline'))return 'bun\0lab/upstream-server.ts\0';if(String(p).endsWith('/stat'))return '22 (bun) '+Array(30).fill('1').join(' ');return nativeFs.readFileSync(typeof p==='number'?p:map(p),...a);},
 mkdirSync(p,...a){return nativeFs.mkdirSync(map(p),...a);},
 openSync(p,...a){return nativeFs.openSync(map(p),...a);},
 lstatSync(p){const info=nativeFs.lstatSync(map(p));Object.defineProperty(info,'uid',{value:0});return info;},
 fstatSync(fd){const info=nativeFs.fstatSync(fd);Object.defineProperty(info,'uid',{value:0});return info;}
}));
process.geteuid=()=>0;
Bun.spawnSync=(args)=>({exitCode:0,stdout:Buffer.from(args[0].includes('systemctl')?'WorkingDirectory='+root+'\nUser=sbarbase\nMainPID=11\n':JSON.stringify({auth:'http://127.0.0.1:9999',key:'anon',serviceToken:'service',process:scenario==='realm'&&calls.length>3?'changed':'native-original'}))});
Bun.sleep=async()=>{};
let factors=[{id:old,status:'verified',factor_type:'totp',friendly_name:'old'}],verified=false;
const session=(aal='aal1')=>({access_token:'header.'+Buffer.from(JSON.stringify({sub:target,exp:Math.floor(Date.now()/1000)+3600,aal})).toString('base64url')+'.signature',refresh_token:'test-refresh',token_type:'bearer',expires_in:3600,user:{id:target,email:'owner@example.test',factors}});
const response=(value,status=200)=>Response.json(value,{status});
globalThis.fetch=async(resource,init)=>{
 const url=new URL(String(resource)),path=url.pathname,method=init?.method??'GET';calls.push(method+' '+path);
 if(scenario==='membership'&&path==='/token')catalog.setMember(target,organization,'other','owner');
 if(scenario==='lease'&&path==='/token')catalog.managementSecurity.release(target,leaseNonce());
 if(path.endsWith('/policy'))return response({sole_owner_recovery:scenario==='old-controller'?undefined:'durable-latch-v1',authorized:verified});
 if(path.includes('/admin/users/')&&path.endsWith('/factors'))return response(factors);
 if(path.includes('/admin/users/')&&method==='GET')return response({id:target,email:'owner@example.test',factors});
 if(path.includes('/admin/users/')&&method==='DELETE'){
  if(scenario==='delete'&&fail)return response({message:'native private error' },500);
  factors=factors.filter(f=>f.id!==old);return response({id:old});
 }
 if(path.endsWith('/token'))return scenario==='password'?response({message:'invalid password'},400):response(session());
 if(path.endsWith('/logout'))return new Response(null,{status:204});
 if(path.endsWith('/factors')&&method==='POST'){
  if(scenario==='enroll'&&fail)return response({message:'native private error'},500);
  factors.push({id:replacement,factor_type:'totp',status:'unverified',friendly_name:JSON.parse(String(init.body)).friendly_name});
  if(scenario==='lost-response'&&fail)throw new Error('native enrollment response lost');
  return response({id:replacement,type:'totp',totp:{secret:'JBSWY3DPEHPK3PXP',uri:'otpauth://totp/private-new-setup',qr_code:'private-qr'}});
 }
 if(path.endsWith('/challenge'))return response({id:'77777777-7777-4777-8777-777777777777',expires_at:9999999999});
 if(path.endsWith('/verify')){
  if(scenario==='verify'&&fail)return response({message:'native verification failure'},400);
  if(scenario==='proxy-fail'&&path.startsWith('/management')&&fail)return response({message:'proxy native failure'},400);
  factors=factors.map(f=>({...f,status:'verified'}));if(path.startsWith('/management')){verified=catalog.grantManagementMfa(target,'native-session',replacement,Math.floor(Date.now()/1000)+1,Math.floor(Date.now()/1000)+3600,catalog.managementSecurity.epoch(target));if(!verified)return response({message:'grant denied'},409);}return response(session('aal2'));
 }
 if(path.endsWith('/user'))return response({id:target,email:'owner@example.test',factors});
 return response({message:'unexpected request'},500);
};
function leaseNonce(){return catalog['db'].query('SELECT nonce FROM management_mfa_lease WHERE actor=?').get(target)?.nonce;}
const {Catalog}=await import(source.replace('/lab/management-sole-owner-recovery.ts','/src/control/catalog.ts'));
catalog=new Catalog(root+'/.lab/upstream/control.sqlite');organization=catalog.initializeInstallation('bootstrap',target,'Installation');catalog.close();
const {recoverSoleManagementOwner}=await import(source);
// Reopen a second connection for concurrent membership and lease changes.
catalog=new Catalog(root+'/.lab/upstream/control.sqlite');
const input={target,factor:scenario==='factor'?'88888888-8888-4888-8888-888888888888':old,targetPassword:'PRIVATE-PASSWORD',reason:'lost_factor'};
let success=false,error='',retrySuccess=false;
try{await recoverSoleManagementOwner(input);success=true;}catch(e){error=e.message;}
let requestFile;try{requestFile=nativeFs.readdirSync(root+'/private/sole-owner-recovery').find(p=>p.endsWith('.request.json'));}catch{}
if(['delete','enroll','verify','proxy-fail','lost-response'].includes(scenario)&&requestFile){fail=false;try{await recoverSoleManagementOwner({...input,resumeReceipt:requestFile.split('.')[0]});retrySuccess=true;}catch(e){error=e.message;}}
let replayDenied=false;if(success&&requestFile){try{await recoverSoleManagementOwner({...input,resumeReceipt:requestFile.split('.')[0]});}catch{replayDenied=true;}}
const audit=catalog.managementSecurityAudit(target);
process.stdout.write(JSON.stringify({success,retrySuccess,replayDenied,error,calls,latch:catalog.managementSecurity.recovering(target),tokenEpoch:catalog.managementSecurity.tokenEpoch(target),audit}));
catalog.close();nativeFs.rmSync(root,{recursive:true,force:true});
`;
function simulate(scenario:string){
 const result=Bun.spawnSync([process.execPath,'--eval',simulation,scenario,resolve('lab/management-sole-owner-recovery.ts')],{stdout:'pipe',stderr:'pipe',timeout:15000});
 expect(result.stderr.toString()).toBe('');expect(result.exitCode).toBe(0);const value=JSON.parse(result.stdout.toString());return value;
}
test('root-contract simulation performs exact native replacement then existing proxy admission and refuses replay',()=>{
 const result=simulate('success');expect(result.success).toBe(true);expect(result.replayDenied).toBe(true);expect(result.latch).toBe(false);
 const calls=result.calls as string[];expect(calls.indexOf('POST /logout')).toBeLessThan(calls.indexOf('DELETE /admin/users/'+target+'/factors/'+factor));
 expect(calls).toContain('POST /management/auth/v1/factors/55555555-5555-4555-8555-555555555555/verify');
 expect(JSON.stringify(result.audit)).not.toContain('PRIVATE-PASSWORD');expect(JSON.stringify(result.audit)).not.toContain('JBSWY3DPEHPK3PXP');
});
for(const scenario of ['password','factor','old-controller','membership','realm','lease'])test('root-contract simulation refuses '+scenario+' before native deletion',()=>{
 const result=simulate(scenario);expect(result.success).toBe(false);expect(result.calls.some((call:string)=>call.startsWith('DELETE'))).toBe(false);
});
for(const scenario of ['delete','enroll','verify','proxy-fail'])test('root-contract simulation resumes '+scenario+' failure without re-enrolling a saved replacement',()=>{
 const result=simulate(scenario);expect(result.success).toBe(false);expect(result.retrySuccess).toBe(true);expect(result.latch).toBe(false);
 if(['verify','proxy-fail'].includes(scenario))expect(result.calls.filter((call:string)=>call==='POST /factors').length).toBe(1);
});

test('live application proxy refuses latched password and MFA grant publication under optional policy',async()=>{
 const {catalog}=fixture(),keys=new KeyStore(':memory:'),security=catalog.managementSecurity,now=Math.floor(Date.now()/1000);
 let factors=[verifiedFactor];
 const transport=(async(resource,init)=>{
  const path=new URL(String(resource)).pathname;
  if(path==='/user')return Response.json({id:target,is_anonymous:false,factors});
  if(path==='/token')return Response.json({access_token:managementToken(target,'aal1',sessionId,now),user:{id:target}});
  if(path.endsWith('/verify'))return Response.json({access_token:managementToken(target,'aal2',sessionId,now),user:{id:target,factors}});
  return Response.json({id:challengeId});
 }) as typeof fetch;
 const handler=application(catalog,keys,{auth:'http://native.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport);
 function request(path:string,token:string,method='GET',body?:unknown){return new Request('http://local/management/'+path,{method,headers:{apikey:'public',authorization:'Bearer '+token,...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});}
 try{
  security.setPolicy(false);security.grant(target,sessionId,factorId,now,now+3600,0);
  const old=managementToken(target,'aal2',sessionId,now);
  expect((await handler(request('v1/organizations',old))).status).toBe(200);
  catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt);
  expect((await handler(request('v1/organizations',old))).status).toBe(403);
  const password=managementToken(target,'aal1',sessionId,now);
  const policy=await handler(request('auth/v1/policy',password));expect(policy.status).toBe(200);
  expect(await policy.json()).toMatchObject({sole_owner_recovery:'durable-latch-v1',recovery_pending:true,authorized:false,mfa_required:false});
  const verify=await handler(request('auth/v1/factors/'+factorId+'/verify',password,'POST',{challenge_id:challengeId,code:'123456'}));
  expect(verify.status).toBe(409);expect((await handler(request('v1/organizations',old))).status).toBe(403);
  factors=[];
  expect((await handler(request('auth/v1/token?grant_type=password',password,'POST',{email:'owner@example.test',password:'private'}))).status).toBe(503);
  expect((await handler(request('v1/organizations',password))).status).toBe(403);
 }finally{catalog.close();keys.close();}
});

test('lost native enrollment response preserves factor and durable denial instead of deleting or duplicating it',()=>{
 const result=simulate('lost-response');expect(result.success).toBe(false);expect(result.retrySuccess).toBe(false);expect(result.latch).toBe(true);
 expect(result.calls.filter((call:string)=>call==='POST /factors').length).toBe(1);
});
test('armed recovery stays latched until the exact proxy factor grant commits, and stale grants roll back completion',()=>{
 const {catalog}=fixture(),security=catalog.managementSecurity,now=Math.floor(Date.now()/1000);
 try{
  catalog.beginSoleOwnerRecovery(target,factor,'lost_factor',receipt);catalog.prepareSoleOwnerRecovery(target,factor,receipt);
  expect(security.recovering(target)).toBe(true);
  expect(security.grant(target,'direct',factor,now+1,now+100,security.epoch(target))).toBe(false);
  expect(catalog.grantManagementMfa(target,'wrong',factorId,now+1,now+100,security.epoch(target))).toBe(false);
  expect(()=>catalog.grantManagementMfa(target,'stale',factor,now-1,now+100,security.epoch(target))).toThrow('admission');
  expect(security.recovering(target)).toBe(true);
  expect(catalog.grantManagementMfa(target,'exact',factor,now+1,now+100,security.epoch(target))).toBe(true);
  expect(security.recovering(target)).toBe(false);
 }finally{catalog.close();}
});
