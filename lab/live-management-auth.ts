/** Disposable live-probe authenticator. All sessions and grants come from native Auth. */
import {createClient,type SupabaseClient,type Session,type User} from '@supabase/supabase-js';
import {createHmac,randomUUID} from 'node:crypto';
import {constants,openSync,closeSync,fstatSync,readFileSync,writeFileSync,fsyncSync,lstatSync,renameSync,unlinkSync} from 'node:fs';
import {dirname,resolve,join} from 'node:path';

const KEY='sb_publishable_sbarbase_local_management';
type Account={email:string;actor:string;factor:string;secret:string;access_token:string;refresh_token:string};
type Descriptor={schema:1;run:string;accounts:Account[]};
type Operation={end:number;controller:AbortController};
const clients=new WeakMap<SupabaseClient,{active?:Operation}>();
function need(ok:unknown):asserts ok {if(!ok)throw new Error('Native management probe authentication refused');}
function totp(secret:string){
 let bits='';for(const c of secret.toUpperCase().replace(/=+$/,'')){const n='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c);need(n>=0);bits+=n.toString(2).padStart(5,'0');}
 const key=Buffer.from((bits.match(/.{8}/g)??[]).map(x=>parseInt(x,2)));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const digest=createHmac('sha1',key).update(counter).digest(),offset=digest[19]!&15;
 return String((digest.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
export function liveManagementClient(base:string,key=KEY){
 const root=new URL(base.replace(/\/+$/,'')+'/management/');need(['http:','https:'].includes(root.protocol)&&!root.username&&!root.password&&!root.search&&!root.hash);
 const state:{active?:Operation}={};
 const client=createClient(root.href.replace(/\/$/,''),key,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:(async(input,init)=>{
  const url=new URL(input instanceof Request?input.url:String(input));need(url.origin===root.origin&&url.pathname.startsWith(root.pathname+'auth/v1/'));
  const remaining=state.active?state.active.end-performance.now():5000;need(remaining>0);
  const signals=[AbortSignal.timeout(Math.min(5000,remaining))];
  if(init?.signal)signals.push(init.signal);
  if(input instanceof Request)signals.push(input.signal);
  if(state.active)signals.push(state.active.controller.signal);
  return fetch(input,{...init,redirect:'error',signal:AbortSignal.any(signals)});
 }) as typeof fetch}});
 clients.set(client,state);return client;
}
/** Shared descriptor retains one genuine session, never an invented assurance level. */
export async function liveManagementLogin(client:SupabaseClient,base:string,credentials:{email:string;password:string},options:{fresh?:boolean;path?:string}={}):Promise<{data:{user:User;session:Session};error:{message:string}|null}>{
 const end=performance.now()+60000;const left=()=>{const n=end-performance.now();need(n>0);return n;};
 const clientState=clients.get(client);need(clientState&&!clientState.active);
 const path=options.path??process.env.SBARBASE_LIVE_AUTH_FILE;need(path&&resolve(path)===path);
 const parent=dirname(path);need(!lstatSync(parent).isSymbolicLink()&&(lstatSync(parent).mode&0o777)===0o700);
 for(let p=parent;;p=dirname(p)){need(!lstatSync(p).isSymbolicLink());if(p===dirname(p))break;}
 let lock:number|undefined;const lockPath=path+'.lock';
 while(lock===undefined){left();try{lock=openSync(lockPath,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600);}catch(e){if((e as NodeJS.ErrnoException).code!=='EEXIST')throw new Error('Native management probe lock refused');await Bun.sleep(Math.min(100,left()));}}
 const lockIdentity=fstatSync(lock);let temporary:string|undefined;
 const controller=new AbortController();clientState.active={end,controller};
 const pending=new Set<Promise<unknown>>();
 const bounded=async<T>(work:()=>Promise<T>,milliseconds=5500):Promise<T>=>{
  left();const operation=work();pending.add(operation);
  void operation.then(()=>pending.delete(operation),()=>pending.delete(operation));
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('Native management probe deadline expired'));},Math.min(milliseconds,left()));})]);}
  finally{if(timer)clearTimeout(timer);}
 };
 try{
  const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);let state:Descriptor;
  try{const before=fstatSync(fd);need(before.isFile()&&before.nlink===1&&(before.mode&0o777)===0o600&&before.size<=262144&&before.uid===process.getuid?.());const body=readFileSync(fd);const after=fstatSync(fd);need(body.length===before.size&&before.ino===after.ino&&before.size===after.size&&before.mtimeMs===after.mtimeMs&&before.ctimeMs===after.ctimeMs);state=JSON.parse(body.toString());}finally{closeSync(fd);}
  need(state.schema===1&&/^[a-f0-9-]{36}$/.test(state.run)&&Array.isArray(state.accounts)&&state.accounts.length<=32);
  need(new Set(state.accounts.map(a=>a.email)).size===state.accounts.length);
  let entry=state.accounts.find(a=>a.email===credentials.email),session:Session|undefined;
  if(entry&&!options.fresh){
   const saved=entry;
   need(typeof saved.secret==='string'&&typeof saved.factor==='string'&&typeof saved.actor==='string');
   const restored=await bounded(()=>client.auth.setSession({access_token:saved.access_token,refresh_token:saved.refresh_token}));
   if(!restored.error&&restored.data.session&&restored.data.user?.id===saved.actor)session=restored.data.session;
  }
  if(!session){const login=await bounded(()=>client.auth.signInWithPassword(credentials));need(!login.error&&login.data.session&&login.data.user);session=login.data.session;
   if(entry)need(entry.actor===login.data.user.id);
   else{
    const factors=await bounded(()=>client.auth.mfa.listFactors());need(!factors.error&&!factors.data.all.some(f=>f.status==='verified'));
    const factor=await bounded(()=>client.auth.mfa.enroll({factorType:'totp',friendlyName:'CI native '+state.run,issuer:'Sbarbase CI'}));need(!factor.error&&factor.data.type==='totp'&&factor.data.totp.secret);
    entry={email:credentials.email,actor:login.data.user.id,factor:factor.data.id,secret:factor.data.totp.secret,access_token:'',refresh_token:''};state.accounts.push(entry);
    // Persist the original native secret before verify so interrupted setup never enrolls a second factor.
    await publish();
   }
  }
  need(entry);const identified=await bounded(()=>client.auth.getUser());need(!identified.error&&identified.data.user.id===entry.actor);
  const available=await bounded(()=>client.auth.mfa.listFactors());need(!available.error&&available.data.all.some(f=>f.id===entry.factor&&f.factor_type==='totp'));
  const admission=async(token:string)=>bounded(async()=>{
   const response=await fetch(base.replace(/\/+$/,'')+'/management/v1/organizations',{headers:{authorization:'Bearer '+token},redirect:'error',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(Math.min(5000,left()))])});
   await response.body?.cancel();return {status:response.status,ok:response.ok};
  });
  const probe=await admission(session.access_token);
  if(options.fresh||probe.status===403){
   let verified=await bounded(()=>client.auth.mfa.challengeAndVerify({factorId:entry.factor,code:totp(entry.secret)}),11000);
   if(verified.error?.status===409){await Bun.sleep(Math.min(1100,left()));verified=await bounded(()=>client.auth.mfa.challengeAndVerify({factorId:entry.factor,code:totp(entry.secret)}),11000);}
   need(!verified.error&&verified.data.access_token);const current=await bounded(()=>client.auth.getSession());need(!current.error&&current.data.session);session=current.data.session;
  }else need(probe.ok);
  const admitted=await admission(session.access_token);need(admitted.ok);
  const actual=await bounded(()=>client.auth.getUser());need(!actual.error&&actual.data.user.id===entry.actor);
  entry.access_token=session.access_token;entry.refresh_token=session.refresh_token;await publish();
  return {data:{user:actual.data.user,session},error:null};
  async function publish(){left();temporary=join(parent,'.native-auth-'+randomUUID());const fd=openSync(temporary,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600);try{writeFileSync(fd,JSON.stringify(state));fsyncSync(fd);}finally{closeSync(fd);}left();renameSync(temporary!,path!);temporary=undefined;}
 }catch{throw new Error('Native management probe authentication refused');}
 finally{
  controller.abort();let settled=pending.size===0;
  if(!settled&&end>performance.now()){
   let timer:ReturnType<typeof setTimeout>|undefined;
   try{settled=await Promise.race([Promise.allSettled([...pending]).then(()=>true),new Promise<boolean>(resolve=>{timer=setTimeout(()=>resolve(false),end-performance.now());})]);}
   finally{if(timer)clearTimeout(timer);}
  }
  if(!settled){closeSync(lock);throw new Error('Native management probe operation unresolved; private lock retained');}
  clientState.active=undefined;
  if(temporary)unlinkSync(temporary);closeSync(lock);const current=lstatSync(lockPath);need(current.ino===lockIdentity.ino&&current.dev===lockIdentity.dev);unlinkSync(lockPath);
 }
}
