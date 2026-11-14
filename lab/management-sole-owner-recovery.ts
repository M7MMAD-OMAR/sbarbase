/** Root-local B5 recovery for an installation with exactly one owner. Never import in a server. */
import {createClient,type SupabaseClient} from '@supabase/supabase-js';
import {createHmac,randomUUID} from 'node:crypto';
import {constants,openSync,closeSync,readFileSync,writeFileSync,fsyncSync,fstatSync,lstatSync,mkdirSync,realpathSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {Catalog} from '../src/control/catalog';

const PRIVATE='/var/lib/sbarbase/sole-owner-recovery';
const KEY='sb_publishable_sbarbase_local_management';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
export type SoleOwnerRecovery={target:string;factor:string;targetPassword:string;reason:'lost_factor'|'compromised_factor';resumeReceipt?:string};
type Realm={checkout:string;auth:string;key:string;serviceToken:string;process:string;controller:string;controllerProcess:string};
type Setup={receipt:string;target:string;oldFactor:string;factor:string;secret:string;uri:string;realm:string};
function need(value:unknown):asserts value {if(!value)throw new Error('Sole-owner recovery refused');}
function root(){need(process.geteuid?.()===0);}
function json(path:string){return JSON.parse(readFileSync(path,'utf8'));}
/** Discover installation authority from the fixed host installation, never stdin or environment. */
function installed():Realm {
 root();
 const unit=Bun.spawnSync(['/usr/bin/systemctl','show','sbarbase.service','--property=WorkingDirectory,User,MainPID'],{env:{PATH:'/usr/bin:/bin'},stdout:'pipe',stderr:'pipe',timeout:5000});
 need(unit.exitCode===0);const fields=Object.fromEntries(unit.stdout.toString().trim().split('\n').map(line=>{const position=line.indexOf('=');return [line.slice(0,position),line.slice(position+1)];}));
 const ROOT=fields.WorkingDirectory;need(ROOT&&ROOT.startsWith('/')&&realpathSync(ROOT)===ROOT&&fields.User==='sbarbase'&&/^[1-9][0-9]*$/.test(fields.MainPID??''));
 need(realpathSync(fileURLToPath(import.meta.url))===ROOT+'/lab/management-sole-owner-recovery.ts');
 need(realpathSync('/proc/'+fields.MainPID+'/cwd')===ROOT);
 const server=json(ROOT+'/.lab/upstream/server.json');
 need(Number.isSafeInteger(server.pid)&&server.pid>0&&/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(server.url));
 need(realpathSync('/proc/'+server.pid+'/cwd')===ROOT);
 const command=readFileSync('/proc/'+server.pid+'/cmdline','utf8').split('\0');
 need(command.some((part:string)=>part==='lab/upstream-server.ts'||part===ROOT+'/lab/upstream-server.ts'));
 const supervisor=json(ROOT+'/.lab/upstream/supervisor.json');need(supervisor.serverPid===server.pid&&supervisor.pid===Number(fields.MainPID));
 // The existing reader validates the retained original container's image, ownership, full Auth
 // configuration, published endpoint and process identity. It executes as the installation uid
 // because its existing private-file contract intentionally rejects foreign file owners.
 const script="import sys,json;sys.path.insert(0,sys.argv[1]+'/lab');import lifecycle_auth_realm as r;import durable_runtime as n;v=r.realm();v['serviceToken']=n.token(r.private_json(n.runtime_files.runtime_secret_path_for_tool(n.PRIVATE))['management']['jwt'],'service_role');print(json.dumps(v))";
 const result=Bun.spawnSync(['/usr/sbin/runuser','-u','sbarbase','--','/usr/bin/python3','-c',script,ROOT],{cwd:ROOT,env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/home/sbarbase'},stdout:'pipe',stderr:'pipe',timeout:10000});
 need(result.exitCode===0);const realm=JSON.parse(result.stdout.toString());
 need(typeof realm.auth==='string'&&/^http:\/\/[0-9.]+:[0-9]+\/?$/.test(realm.auth)&&typeof realm.key==='string'&&typeof realm.serviceToken==='string'&&typeof realm.process==='string');
 return {...realm,checkout:ROOT,controller:server.url,controllerProcess:server.pid+':'+readFileSync('/proc/'+server.pid+'/stat','utf8').split(') ')[1]!.split(' ')[19]};
}
function privateFolder(){
 root();
 for(const directory of ['/var','/var/lib','/var/lib/sbarbase']){
  if(directory==='/var/lib/sbarbase')try{mkdirSync(directory,{mode:0o700});}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
  const info=lstatSync(directory);need(info.isDirectory()&&!info.isSymbolicLink()&&info.uid===0&&(info.mode&0o022)===0);
 }
 try{mkdirSync(PRIVATE,{mode:0o700});}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
 const info=lstatSync(PRIVATE);need(info.isDirectory()&&!info.isSymbolicLink()&&info.uid===0&&(info.mode&0o777)===0o700);
}
function artifact(receipt:string,suffix:string,value?:unknown):any {
 need(UUID.test(receipt)&&['request','setup','session'].includes(suffix));privateFolder();
 const path=PRIVATE+'/'+receipt+'.'+suffix+'.json';
 const fd=openSync(path,(value===undefined?constants.O_RDONLY:constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL)|constants.O_NOFOLLOW|constants.O_NONBLOCK,0o600);
 try{
  const info=fstatSync(fd);need(info.isFile()&&info.uid===0&&info.nlink===1&&(info.mode&0o777)===0o600&&info.size<=65536);
  if(value!==undefined){writeFileSync(fd,JSON.stringify(value));fsyncSync(fd);const parent=openSync(PRIVATE,constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW);try{fsyncSync(parent);}finally{closeSync(parent);}return value;}
  return JSON.parse(readFileSync(fd,'utf8'));
 }finally{closeSync(fd);}
}
function optionalArtifact(receipt:string,suffix:string){try{return artifact(receipt,suffix);}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return null;throw error;}}
function code(secret:string){
 need(/^[A-Z2-7]+=*$/i.test(secret));let bits='';
 for(const character of secret.toUpperCase().replace(/=+$/,''))bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(character).toString(2).padStart(5,'0');
 const key=Buffer.from((bits.match(/.{8}/g)??[]).map(byte=>parseInt(byte,2))),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const digest=createHmac('sha1',key).update(counter).digest(),offset=digest[19]!&15;
 return String((digest.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
function client(key:string,transport:typeof fetch){return createClient('http://sole-owner-recovery.internal',key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:transport}});}
/** Actual OS root proof is mandatory even for direct module callers. Host root remains trusted. */
export async function recoverSoleManagementOwner(input:SoleOwnerRecovery):Promise<{receipt:string;factor:string;oldFactor:string}> {
 root();need(input&&Object.keys(input).every(key=>['target','factor','targetPassword','reason','resumeReceipt'].includes(key))&&UUID.test(input.target)&&UUID.test(input.factor)&&
  ['lost_factor','compromised_factor'].includes(input.reason)&&typeof input.targetPassword==='string'&&input.targetPassword.length>0&&input.targetPassword.length<=1024&&
  (input.resumeReceipt===undefined||UUID.test(input.resumeReceipt)));
 const realm=installed(),binding=JSON.stringify(realm),receipt=input.resumeReceipt??randomUUID();
 const catalog=new Catalog(realm.checkout+'/.lab/upstream/control.sqlite'),security=catalog.managementSecurity;
 let lease:string|null=null,latched=false,user:SupabaseClient|undefined;
 const native=(async(resource,init)=>{
  const url=new URL(resource instanceof Request?resource.url:String(resource));
  need(url.origin==='http://sole-owner-recovery.internal'&&url.pathname.startsWith('/auth/v1/'));
  if(init?.method&&init.method!=='GET')await authorized();
  return fetch(new URL(url.pathname.slice('/auth/v1'.length)+url.search,realm.auth),{...init,redirect:'error',signal:AbortSignal.timeout(5000)});
 }) as typeof fetch;
 const proxy=(async(resource,init)=>{
  const url=new URL(resource instanceof Request?resource.url:String(resource));
  need(url.origin==='http://sole-owner-recovery.internal'&&url.pathname.startsWith('/auth/v1/'));
  root();catalog.assertSoleManagementOwner(input.target);need(JSON.stringify(installed())===binding);
  return fetch(realm.controller+'/management'+url.pathname+url.search,{...init,redirect:'error',signal:AbortSignal.timeout(5000)});
 }) as typeof fetch;
 const admin=client(realm.serviceToken,native);
 async function authorized(){root();catalog.assertSoleManagementOwner(input.target);need(JSON.stringify(installed())===binding&&lease&&security.renew(input.target,lease));}
 async function password(proof:SupabaseClient,email:string){await authorized();const result=await proof.auth.signInWithPassword({email,password:input.targetPassword});need(!result.error&&result.data.user?.id===input.target&&result.data.session);return result.data.session;}
 try{
  catalog.assertSoleManagementOwner(input.target);lease=security.lease(input.target);need(lease);await authorized();
  if(input.resumeReceipt){
   need(!optionalArtifact(receipt,'session'));const request=artifact(receipt,'request');
   need(request.target===input.target&&request.factor===input.factor&&request.reason===input.reason&&request.realm===realm.process);
  }
  const found=await admin.auth.admin.getUserById(input.target);need(!found.error&&found.data.user?.id===input.target&&found.data.user.email);
  user=client(realm.key,native);const session=await password(user,found.data.user.email);
  // A live controller must advertise the admission latch. Files from a newer checkout cannot
  // prove the already-running controller enforces it.
  await authorized();const policy=await fetch(realm.controller+'/management/auth/v1/policy',{headers:{apikey:KEY,authorization:'Bearer '+session.access_token},redirect:'error',signal:AbortSignal.timeout(5000)});
  need(policy.ok&&(await policy.json() as any).sole_owner_recovery==='durable-latch-v1');await authorized();
  const before=await admin.auth.admin.mfa.listFactors({userId:input.target});need(!before.error);
  let setup=optionalArtifact(receipt,'setup') as Setup|null;
  if(setup)need(setup.receipt===receipt&&setup.target===input.target&&setup.oldFactor===input.factor&&setup.realm===realm.process&&UUID.test(setup.factor)&&typeof setup.secret==='string'&&typeof setup.uri==='string');
  if(!input.resumeReceipt)need(before.data.factors.some(factor=>factor.id===input.factor&&factor.factor_type==='totp'&&factor.status==='verified'));
  await authorized();
  if(!input.resumeReceipt)artifact(receipt,'request',{receipt,target:input.target,factor:input.factor,reason:input.reason,realm:realm.process});
  catalog.beginSoleOwnerRecovery(input.target,input.factor,input.reason,receipt,!!input.resumeReceipt);latched=true;
  await authorized();const logout=await user.auth.signOut({scope:'global'});need(!logout.error);
  await authorized();
  if(before.data.factors.some(factor=>factor.id===input.factor)){
   const removed=await admin.auth.admin.mfa.deleteFactor({userId:input.target,id:input.factor});need(!removed.error);
  }else need(input.resumeReceipt);
  await password(user,found.data.user.email);
  if(!setup){
   await authorized();const factors=await admin.auth.admin.mfa.listFactors({userId:input.target});need(!factors.error);
   // An enrollment whose response was lost has no recoverable seed. Preserve it and the latch
   // for diagnosis rather than silently deleting a factor or creating another one.
   need(!factors.data.factors.some(factor=>factor.friendly_name==='Host recovery '+receipt));
   await authorized();const enrolled=await user.auth.mfa.enroll({factorType:'totp',friendlyName:'Host recovery '+receipt,issuer:'Sbarbase'});
   need(!enrolled.error&&enrolled.data.type==='totp'&&enrolled.data.totp.secret);
   setup=artifact(receipt,'setup',{receipt,target:input.target,oldFactor:input.factor,factor:enrolled.data.id,secret:enrolled.data.totp.secret,uri:enrolled.data.totp.uri,realm:realm.process}) as Setup;
  }
  await authorized();let verified=await user.auth.mfa.challengeAndVerify({factorId:setup.factor,code:code(setup.secret)});
  if(verified.error){await Bun.sleep(30000-Date.now()%30000+100);await authorized();verified=await user.auth.mfa.challengeAndVerify({factorId:setup.factor,code:code(setup.secret)});}
  need(!verified.error&&verified.data.access_token);await authorized();
  const identity=await user.auth.getUser(),factors=await admin.auth.admin.mfa.listFactors({userId:input.target});
  need(!identity.error&&identity.data.user.id===input.target&&!factors.error&&
   factors.data.factors.some(factor=>factor.id===setup!.factor&&factor.factor_type==='totp'&&factor.status==='verified')&&
   !factors.data.factors.some(factor=>factor.id===input.factor)&&identity.data.user.factors?.some(factor=>factor.id===setup!.factor&&factor.status==='verified'));
  await authorized();catalog.prepareSoleOwnerRecovery(input.target,setup.factor,receipt);
  security.release(input.target,lease);lease=null;
  // All management authority is obtained from the existing proxy's native verification route.
  // Its own lease protects each native operation; it cannot publish a grant while latched.
  const admitted=client(KEY,proxy);const login=await admitted.auth.signInWithPassword({email:found.data.user.email,password:input.targetPassword});
  need(!login.error&&login.data.user?.id===input.target);await Bun.sleep(30000-Date.now()%30000+100);
  root();catalog.assertSoleManagementOwner(input.target);need(JSON.stringify(installed())===binding&&security.recovering(input.target));
  const admission=await admitted.auth.mfa.challengeAndVerify({factorId:setup.factor,code:code(setup.secret)});need(!admission.error&&admission.data.access_token);
  const nativeIdentity=await admitted.auth.getUser();need(!nativeIdentity.error&&nativeIdentity.data.user.id===input.target);
  const check=await fetch(realm.controller+'/management/auth/v1/policy',{headers:{apikey:KEY,authorization:'Bearer '+admission.data.access_token},redirect:'error',signal:AbortSignal.timeout(5000)});
  need(check.ok&&(await check.json() as any).authorized===true);
  root();catalog.assertSoleManagementOwner(input.target);need(JSON.stringify(installed())===binding&&!security.recovering(input.target));
  artifact(receipt,'session',{receipt,target:input.target,factor:setup.factor,access_token:admission.data.access_token,refresh_token:admission.data.refresh_token});
  return {receipt,factor:setup.factor,oldFactor:input.factor};
 }catch{
  if(latched){
   // A final proxy failure also leaves durable denial on the same resumable receipt.
   catalog.finishSoleOwnerRecovery(input.target,input.factor,receipt,'failed');
  }
  throw new Error('Sole-owner recovery refused or failed; inspect the private receipt and security audit');
 }finally{if(lease)security.release(input.target,lease);catalog.close();}
}
if(import.meta.main){
 try{root();need(process.argv.length===2&&!process.stdin.isTTY);const body=await Bun.stdin.text();need(Buffer.byteLength(body)<=8192);const result=await recoverSoleManagementOwner(JSON.parse(body));process.stdout.write(JSON.stringify(result)+'\n');}
 catch{process.stderr.write('Sole-owner recovery refused or failed. Inspect the private receipt and security audit.\n');process.exitCode=1;}
}
