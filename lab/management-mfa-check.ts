import {createClient,type SupabaseClient} from '@supabase/supabase-js';
import {createHmac,randomBytes,randomUUID} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {recoverManagementFactor} from './management-factor-recovery';
import {browserCheck} from './management-mfa-browser';

const config=await Bun.file(process.argv[2]!).json();
const root=new URL(config.endpoint);
if(root.protocol!=='http:'||root.pathname!=='/'||root.username||root.password)throw new Error('Invalid disposable Auth endpoint');
const checks:{name:string;ok:boolean}[]=[];
function check(name:string,ok:unknown){checks.push({name,ok:!!ok});if(!ok)throw new Error('Failed: '+name);}
function jwt(secret:string,claims:object){const head=Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url');
 const body=Buffer.from(JSON.stringify({aud:'authenticated',exp:Math.floor(Date.now()/1000)+3600,...claims})).toString('base64url');
 return head+'.'+body+'.'+createHmac('sha256',secret).update(head+'.'+body).digest('base64url');}
const anon=jwt(config.jwt,{role:'anon'}),service=jwt(config.jwt,{role:'service_role'}),publicKey='sb_publishable_fixture_management';
/** Test authenticator only. Auth owns enrollment, code validation and session issuance. */
function authenticator(secret:string):string{
 const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits='';
 for(const char of secret.toUpperCase().replace(/=+$/,'')){const value=alphabet.indexOf(char);if(value<0)throw new Error('Invalid native enrollment secret');bits+=value.toString(2).padStart(5,'0');}
 const key=Buffer.from((bits.match(/.{8}/g)??[]).map(byte=>parseInt(byte,2)));
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const digest=createHmac('sha1',key).update(counter).digest(),offset=digest[19]!&15;
 return String((digest.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
const native=(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
 const target=new URL(String(input));
 if(target.origin!==root.origin)throw new Error('Fixture attempted unrelated upstream access');
 return fetch(target,{...init,redirect:'error',signal:AbortSignal.timeout(5000)});
}) as typeof fetch;
const adminTransport=(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
 const target=new URL(String(input));
 if(target.origin!=='http://fixture.internal'||!target.pathname.startsWith('/auth/v1/'))throw new Error('Unexpected fixture admin route');
 return native(new URL(target.pathname.slice('/auth/v1'.length)+target.search,root),init);
}) as typeof fetch;
const admin=createClient('http://fixture.internal',service,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:adminTransport}});
const catalogPath=config.directory+'/catalog.db',keyPath=config.directory+'/keys.db';
let catalog=new Catalog(catalogPath),keys=new KeyStore(keyPath);
let handler=application(catalog,keys,{auth:root.href,anonymousToken:anon,publishableKey:publicKey},()=>undefined,native);
const client=()=>createClient('http://console.fixture/management',publicKey,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>handler(new Request(input,init))) as typeof fetch}});
async function request(token:string,path='/management/v1/organizations',method='GET',body?:object){return handler(new Request('http://console.fixture'+path,{method,headers:{authorization:'Bearer '+token,apikey:publicKey,...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}));}
async function enroll(user:SupabaseClient,name:string){const result=await user.auth.mfa.enroll({factorType:'totp',friendlyName:name});
 check('native '+name+' enrollment returns original Auth factor and QR',!result.error&&!!result.data?.totp.qr_code&&!!result.data?.totp.secret);return result.data!;}
async function verify(user:SupabaseClient,factor:string,secret:string){let result=await user.auth.mfa.challengeAndVerify({factorId:factor,code:authenticator(secret)});
 if(result.error?.status===409){check('revocation refuses same-second native MFA regrant',true);await Bun.sleep(1100);result=await user.auth.mfa.challengeAndVerify({factorId:factor,code:authenticator(secret)});}
 check('native factor challenge and code verification issues session',!result.error&&!!result.data?.access_token);return result.data!.access_token;}
async function create(email:string,password:string){const result=await admin.auth.admin.createUser({email,password,email_confirm:true});check('private native identity provisioned',!result.error&&!!result.data.user);return result.data.user!;}
async function login(user:SupabaseClient,email:string,password:string){const result=await user.auth.signInWithPassword({email,password});check('original password login succeeds',!result.error&&!!result.data.session);return result.data.session!.access_token;}
try{
 const suffix=randomBytes(6).toString('hex'),password=randomBytes(24).toString('base64url')+'A1!';
 const browserOwner=await create('browser-'+suffix+'@example.test',password);
 await browserCheck({directory:config.directory,endpoint:root.href,anonymousToken:anon,email:browserOwner.email!,password,owner:browserOwner.id},native,authenticator,check);
 const owner=await create('owner-'+suffix+'@example.test',password),member=await create('member-'+suffix+'@example.test',password);
 const operator=client(),memberClient=client();
 const org=catalog.initializeInstallation(randomUUID(),owner.id,'Fixture operators');
 const otherOrg=catalog.createOrganization(owner.id,'Isolated organization');
 catalog.setMember(owner.id,org,member.id,'viewer');
 check('anonymous management request denied',(await request('')).status===401);
 check('public signup denied',(await operator.auth.signUp({email:'closed-'+suffix+'@example.test',password})).error!==null);
 for(const path of ['/management/auth/v1/admin/users','/management/auth/v1/recover','/management/auth/v1/verify','/management/auth/v1/otp'])
  check('unexposed authentication route denied: '+path,(await request('',path,'POST',{})).status===404);
 const bad=await operator.auth.signInWithPassword({email:owner.email!,password:'wrong-password'});
 check('original password failure remains failure',!!bad.error);
 const aal1=await login(operator,owner.email!,password);
 check('password session cannot bypass console MFA',(await request(aal1)).status===403);
 check('application realm signed JWT cannot enter management',(await request(jwt(randomBytes(32).toString('hex'),{sub:owner.id,role:'authenticated',aal:'aal2',session_id:randomUUID()}))).status===401);
 const first=await enroll(operator,'primary');
 const challenge=await operator.auth.mfa.challenge({factorId:first.id});check('native challenge created',!challenge.error);
 const wrong=String((parseInt(authenticator(first.totp.secret),10)+1)%1000000).padStart(6,'0');
 const badMfa=await operator.auth.mfa.verify({factorId:first.id,challengeId:challenge.data!.id,code:wrong});
 check('well-shaped incorrect code denied by original Auth',badMfa.error?.status===422);
 check('failed MFA leaves management protected',(await request(aal1)).status===403);
 let ownerToken=await verify(operator,first.id,first.totp.secret);
 check('native aal2 token reaches management',(await request(ownerToken)).status===200);
 check('old aal1 JWT remains denied after session upgrade',(await request(aal1)).status===403);
 const refreshed=await operator.auth.refreshSession();check('native refresh succeeds',!refreshed.error);
 ownerToken=refreshed.data.session!.access_token;
 check('refreshed native aal2 retains validated grant',(await request(ownerToken)).status===200);
 const secondSession=client(),secondAal1=await login(secondSession,owner.email!,password);
 check('another password session cannot borrow MFA grant',(await request(secondAal1)).status===403);
 const secondToken=await verify(secondSession,first.id,first.totp.secret);
 check('separately verified session reaches management',(await request(secondToken)).status===200);
 check('first verified session still valid',(await request(ownerToken)).status===200);
 const direct=createClient('http://fixture.internal',anon,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:adminTransport}});
 const directLogin=await direct.auth.signInWithPassword({email:owner.email!,password});check('direct private native password login succeeds',!directLogin.error);
 const directMfa=await direct.auth.mfa.challengeAndVerify({factorId:first.id,code:authenticator(first.totp.secret)});check('direct original Auth produces aal2 without proxy grant',!directMfa.error);
 check('actual native aal2 without proxy verification grant denied',(await request(directMfa.data!.access_token)).status===403);
 const spare=await enroll(operator,'spare');ownerToken=await verify(operator,spare.id,spare.totp.secret);
 check('last verified factor removal is not required for adding a spare',(await operator.auth.mfa.listFactors()).data?.totp.length===2);
 const removal=await operator.auth.mfa.unenroll({factorId:first.id});check('native replacement factor removal succeeds',!removal.error);
 check('factor removal revokes every actor grant',(await request(secondToken)).status===403&&(await request(ownerToken)).status===403);
 ownerToken=await verify(operator,spare.id,spare.totp.secret);
 const last=await operator.auth.mfa.unenroll({factorId:spare.id});check('last verified factor removal denied',!!last.error);
 check('denied last-factor removal preserves verified owner',(await request(ownerToken)).status===200);
 await login(memberClient,member.email!,password);
 const memberFactor=await enroll(memberClient,'member');let memberToken=await verify(memberClient,memberFactor.id,memberFactor.totp.secret);
 check('verified viewer reads own organization',(await request(memberToken,`/management/v1/organizations/${org}/projects`)).status===200);
 check('verified viewer denied other tenant',(await request(memberToken,`/management/v1/organizations/${otherOrg}/projects`)).status===403);
 check('verified viewer denied owner mutation',(await request(memberToken,`/management/v1/organizations/${org}/projects`,'POST',{name:'denied'})).status===403);
 catalog.setMember(owner.id,org,member.id,null);
 check('current membership revocation immediately enforced',(await request(memberToken,`/management/v1/organizations/${org}/projects`)).status===403);
 let refusedRecovery=false;
 try{await recoverManagementFactor(catalog,{auth:root.href,publishableKey:publicKey,serviceToken:service},{target:owner.id,factor:spare.id,reason:'lost_factor',operatorToken:memberToken,targetPassword:password},native);}catch{refusedRecovery=true;}
 check('tenant member cannot perform host factor recovery',refusedRecovery);
 catalog.setMember(owner.id,org,browserOwner.id,'owner');
 let demoted=false,deletedDuringDemotion=false,demotionRecoveryRefused=false;
 const demoting=(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const url=new URL(String(input));const response=await native(input,init);
  if(url.pathname===`/admin/users/${member.id}/factors`&&init?.method==='GET'){
   catalog.setMember(browserOwner.id,org,owner.id,'viewer');demoted=true;
  }
  if(url.pathname.endsWith('/'+memberFactor.id)&&init?.method==='DELETE')deletedDuringDemotion=true;
  return response;
 }) as typeof fetch;
 try{await recoverManagementFactor(catalog,{auth:root.href,publishableKey:publicKey,serviceToken:service},{target:member.id,factor:memberFactor.id,reason:'lost_factor',operatorToken:ownerToken,targetPassword:password},demoting);}catch{demotionRecoveryRefused=true;}
 check('actual native recovery stops after owner downgrade during await',demoted&&demotionRecoveryRefused&&!deletedDuringDemotion);
 check('downgraded recovery preserves native target factor',(await admin.auth.admin.mfa.listFactors({userId:member.id})).data?.factors.some(factor=>factor.id===memberFactor.id));
 check('downgraded recovery records terminal failed outcome',catalog.managementSecurityAudit(browserOwner.id).some(event=>event.action==='management.mfa.recovery_failed'&&event.actor===owner.id&&event.subject===member.id));
 catalog.setMember(browserOwner.id,org,owner.id,'owner');
 await recoverManagementFactor(catalog,{auth:root.href,publishableKey:publicKey,serviceToken:service},{target:member.id,factor:memberFactor.id,reason:'lost_factor',operatorToken:ownerToken,targetPassword:password},native);
 check('restricted owner recovery removes native target factor',(await admin.auth.admin.mfa.listFactors({userId:member.id})).data?.factors.length===0);
 check('recovered account old native session revoked',!!(await admin.auth.getUser(memberToken)).error);
 check('recovery cannot grant management access',(await request(memberToken)).status===401);
 const recoveredAal1=await login(memberClient,member.email!,password);
 check('recovered account password alone still denied',(await request(recoveredAal1)).status===403);
 const reenrolled=await enroll(memberClient,'replacement');memberToken=await verify(memberClient,reenrolled.id,reenrolled.totp.secret);
 check('recovered account must complete original MFA anew',(await request(memberToken)).status===200);
 const sharedSession=client();await login(sharedSession,member.email!,password);
 let rateHit=false;
 for(let attempt=0;attempt<35;attempt++){
  const result=await (attempt%2?sharedSession:memberClient).auth.mfa.challenge({factorId:reenrolled.id});
  if(result.error?.status===429){rateHit=true;break;}
 }
 check('server limits MFA attempts across sessions',rateHit);
 catalog.close();keys.close();catalog=new Catalog(catalogPath);keys=new KeyStore(keyPath);
 handler=application(catalog,keys,{auth:root.href,anonymousToken:anon,publishableKey:publicKey},()=>undefined,native);
 check('rate bounds persist across controller recreation',(await memberClient.auth.mfa.challenge({factorId:reenrolled.id})).error?.status===429);
 check('durable owner MFA grant persists across controller recreation',(await request(ownerToken)).status===200);
 let releaseBody!:()=>void,bodyEntered!:()=>void,sent=false;
 const bodyGate=new Promise<void>(resolve=>{releaseBody=resolve;}),entered=new Promise<void>(resolve=>{bodyEntered=resolve;});
 const streamed=new ReadableStream<Uint8Array>({async pull(controller){if(!sent){sent=true;controller.enqueue(new TextEncoder().encode('{"name":'));return;}
  bodyEntered();await bodyGate;controller.enqueue(new TextEncoder().encode('"Revoked mutation"}'));controller.close();}});
 const pending=handler(new Request('http://console.fixture/management/v1/organizations',{method:'POST',headers:{authorization:'Bearer '+ownerToken,'content-type':'application/json'},body:streamed}));
 await entered;catalog.managementSecurity.revoke(owner.id);releaseBody();
 check('actual native MFA revocation during body read refuses mutation',(await pending).status===403);
 check('MFA-revoked pending mutation created no organization',!catalog.listOrganizations(owner.id).some(org=>org.name==='Revoked mutation'));
 ownerToken=await verify(operator,spare.id,spare.totp.secret);
 let passwordRateHit=false;
 for(let attempt=0;attempt<80;attempt++){
  const response=await handler(new Request('http://console.fixture/management/auth/v1/token?grant_type=password',{method:'POST',headers:{apikey:publicKey,'content-type':'application/json','x-forwarded-for':`198.51.100.${attempt}`},body:JSON.stringify({email:'guess-'+attempt+'@example.test',password:'wrong-password'})}));
  if(response.status===429){passwordRateHit=true;break;}
 }
 check('server password admission cannot be bypassed with changing client headers',passwordRateHit);
 const logoutEpoch=catalog.managementSecurity.epoch(owner.id);
 const logout=await operator.auth.signOut({scope:'global'});check('native global signout succeeds',!logout.error);
 check('native proxy logout advances durable actor epoch',catalog.managementSecurity.epoch(owner.id)>logoutEpoch);
 check('native revoked owner session denied',(await request(ownerToken)).status===401);
}catch(error){const message=error instanceof Error?error.message:'';checks.push({name:message.startsWith('Failed: ')||message.startsWith('Browser check failed at ')?message:'Native fixture operation failed',ok:false});}
finally{catalog.close();keys.close();}
process.stdout.write(JSON.stringify({total:checks.length,failed:checks.filter(check=>!check.ok).length,skipped:0,checks})+'\n');
