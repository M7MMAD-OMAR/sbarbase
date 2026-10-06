import {createClient} from '@supabase/supabase-js';
import {createHmac,randomBytes,randomUUID} from 'node:crypto';
import {existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {main,EXIT,type Deps} from './sbarbase';
import {managementPublishableKey} from './upstream-app';

const config=await Bun.file(process.argv[2]!).json();
const nativeRoot=new URL(config.endpoint),checks:{name:string;ok:boolean}[]=[],terminals:unknown[]=[];
if(nativeRoot.protocol!=='http:'||nativeRoot.pathname!=='/'||nativeRoot.username||nativeRoot.password)throw new Error('Invalid disposable endpoint');
function check(name:string,ok:unknown){checks.push({name,ok:!!ok});if(!ok)throw new Error('Failed: '+name);}
function jwt(role:string){const head=Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url');
 const body=Buffer.from(JSON.stringify({aud:'authenticated',role,exp:Math.floor(Date.now()/1000)+3600})).toString('base64url');
 return head+'.'+body+'.'+createHmac('sha256',config.jwt).update(head+'.'+body).digest('base64url');}
/** Fixture authenticator computes codes; original Auth validates every code. */
function code(secret:string){let bits='';for(const char of secret.replace(/=+$/,'')){
 const value='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(char);if(value<0)throw new Error('Invalid native secret');bits+=value.toString(2).padStart(5,'0');}
 const key=Buffer.from((bits.match(/.{8}/g)??[]).map(byte=>parseInt(byte,2))),counter=Buffer.alloc(8);
 counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const digest=createHmac('sha1',key).update(counter).digest(),offset=digest[19]!&15;
 return String((digest.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const native=(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
 if(new URL(String(input)).origin!==nativeRoot.origin)throw new Error('Unrelated upstream');
 return fetch(input,{...init,redirect:'error',signal:AbortSignal.timeout(5000)});
}) as typeof fetch;
const admin=createClient('http://cli-fixture.internal',jwt('service_role'),{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},
 global:{fetch:(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const url=new URL(String(input));if(url.origin!=='http://cli-fixture.internal'||!url.pathname.startsWith('/auth/v1/'))throw new Error('Invalid admin route');
  return native(new URL(url.pathname.slice('/auth/v1'.length)+url.search,nativeRoot),init);
 }) as typeof fetch}});
const root=join(config.directory,'cli-root'),upstream=join(root,'.lab/upstream');mkdirSync(upstream,{recursive:true});
const catalog=new Catalog(join(upstream,'control.sqlite')),keys=new KeyStore(join(upstream,'keys.sqlite'));
const handler=application(catalog,keys,{auth:nativeRoot.href,anonymousToken:jwt('anon'),publishableKey:managementPublishableKey},()=>undefined,native);
let terminalScenario='success';
let terminalRequests=0;
let terminalProtected=0;
const marker=join(config.directory,'native-verification.marker');
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:async(request)=>{
 terminalRequests++;
 if(new URL(request.url).pathname.startsWith('/management/v1/'))terminalProtected++;
 const response=await handler(request);
 if(terminalScenario==='verify-cancel'&&new URL(request.url).pathname.endsWith('/verify')&&response.ok){
  writeFileSync(marker,'native verification completed\n',{mode:0o600});
  const deadline=Date.now()+3000;
  while(!existsSync(marker+'.cancelled')&&Date.now()<deadline)await Bun.sleep(25);
  await Bun.sleep(100);
 }
 return response;
}});
writeFileSync(join(upstream,'server.json'),JSON.stringify({url:server.url.origin,pid:process.pid}));
const password=randomBytes(24).toString('base64url')+'A1!',email='cli-'+randomBytes(6).toString('hex')+'@example.test';
let secret='';
try{
 const created=await admin.auth.admin.createUser({email,password,email_confirm:true});check('original Auth provisions disposable CLI identity',!created.error&&!!created.data.user);
 const actor=created.data.user!.id,org=catalog.initializeInstallation(randomUUID(),actor,'CLI fixture'),project=catalog.createProject(actor,org,'shop');
 function harness(name:string,{wrong=false,tty=true,stdin=false}:{wrong?:boolean;tty?:boolean;stdin?:boolean}={}){
  const output:string[]=[],prompts:{question:string;hidden:boolean}[]=[],paths:string[]=[],tokens:string[]=[],codes:string[]=[],responses:{path:string;status:number}[]=[];let setup=false,cleared=false;
  const deps:Deps={root,env:{},run:async()=>{throw new Error('Unexpected child');},capture:async()=>{throw new Error('Unexpected child');},isTTY:tty,
   fetch:(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
    const url=new URL(String(input));if(url.origin!==server.url.origin)throw new Error('Unrelated CLI route');paths.push(url.pathname);
    const response=await handler(new Request(input,init));
    responses.push({path:url.pathname,status:response.status});
    if(url.pathname.endsWith('/token')||url.pathname.endsWith('/verify')){const data=await response.clone().json().catch(()=>({}));if(data.access_token)tokens.push(data.access_token);}
    return response;
   }) as typeof fetch,
   prompt:async(question,hidden=false)=>{prompts.push({question,hidden});if(question.includes('Password'))return password;
    if(question.includes('code')||question.includes('Code')){const good=code(secret),value=wrong?String((parseInt(good,10)+1)%1000000).padStart(6,'0'):good;codes.push(value);return value;}
    return '1';},
   stdin:async()=>password,setupAuthenticator:async(value:string)=>{secret=value;setup=true;return ()=>{cleared=true;};},
   out:text=>output.push(text),err:text=>output.push(text),pidAlive:pid=>pid===process.pid,inContainer:false,unitPath:join(root,'absent.service')};
  const argv=['add-environment',project,name,'--email',email,...(stdin?['--password-stdin']:[])];
  return {run:()=>main(argv,deps),output,prompts,paths,tokens,codes,responses,setup:()=>setup,cleared:()=>cleared};
 }
 const first=harness('enrolled');check('CLI first sign-in completes actual native enrollment',await first.run()===EXIT.ok);
 check('CLI delivers native setup only to private terminal hook',first.setup()&&first.cleared()&&!!secret);
 check('CLI enrollment performs original challenge and verify',first.paths.some(p=>p.endsWith('/challenge'))&&first.paths.some(p=>p.endsWith('/verify')));
 check('CLI verified session creates requested environment',catalog.listEnvironments(actor,project).some(e=>e.name==='enrolled'));
 check('CLI MFA and password prompts hide input',first.prompts.filter(p=>/Password|code/i.test(p.question)).every(p=>p.hidden));
 check('CLI output contains no password, setup secret, code or access token',![password,secret,...first.tokens,...first.codes].some(s=>s&&first.output.join('\n').includes(s)));
 check('CLI signs out the original native session after command',first.paths.at(-1)?.endsWith('/logout'));
 await Bun.sleep(1100);
 const later=harness('enrolled',{stdin:true});
 check('CLI existing authenticator verifies before expected duplicate-name refusal',await later.run()===EXIT.failed&&
  later.responses.some(r=>r.path.endsWith('/verify')&&r.status===200)&&later.responses.some(r=>r.path==='/management/v1/organizations'&&r.status===200)&&
  later.responses.some(r=>r.path.startsWith('/management/v1/projects/')&&r.path.endsWith('/environments')&&r.status===409));
 check('CLI later sign-in does not create another authenticator',!later.setup()&&!later.paths.includes('/management/auth/v1/factors'));
 check('CLI later verified command preserves existing environment conflict',catalog.listEnvironments(actor,project).filter(e=>e.name==='enrolled').length===1);
 await Bun.sleep(1100);
 const bad=harness('wrong-code',{wrong:true});check('CLI native wrong code refuses protected command',await bad.run()!==EXIT.ok&&!bad.paths.some(p=>p.startsWith('/management/v1')));
 check('CLI wrong code leaves catalog unchanged',!catalog.listEnvironments(actor,project).some(e=>e.name==='wrong-code'));
 check('CLI failed MFA signs out native password session',bad.paths.at(-1)?.endsWith('/logout'));
 const headless=harness('headless',{tty:false,stdin:true});check('CLI headless MFA refuses before authentication',await headless.run()!==EXIT.ok&&headless.paths.length===0);
 check('CLI failure messages expose no credentials or codes',![password,secret,...bad.tokens,...bad.codes].some(s=>s&&bad.output.join('\n').includes(s)));
 const launcher=join(config.directory,'terminal-launcher.ts');
 // Exercise real terminal dependencies with an explicit disposable root and exact origin guard.
 writeFileSync(launcher,`import {main,processDeps} from ${JSON.stringify(join(import.meta.dir,'sbarbase.ts'))};
const deps=processDeps(${JSON.stringify(root)});
const transport=deps.fetch;
deps.fetch=Object.assign((input,init)=>{
 const url=new URL(input instanceof Request?input.url:String(input));
 if(url.origin!==${JSON.stringify(server.url.origin)}||!url.pathname.startsWith('/management/'))throw new Error('Fixture origin refused');
 return transport(input,{...init,redirect:'error',signal:AbortSignal.any([...(init?.signal?[init.signal]:[]),AbortSignal.timeout(5000)])});
},{preconnect:()=>{}});
process.exit(await main(process.argv.slice(2),deps));\n`,{mode:0o600});
 for(const scenario of ['success','success-password-stdin','success-operator-file','prompt-cancel','verify-cancel','stty-failure','headless-password-stdin','headless-operator-file']){
  terminalScenario=scenario;
  terminalRequests=0;
  terminalProtected=0;
  const ttyEmail='terminal-'+randomBytes(6).toString('hex')+'@example.test';
  const ttyUser=await admin.auth.admin.createUser({email:ttyEmail,password,email_confirm:true});check('original Auth provisions terminal '+scenario+' identity',!ttyUser.error&&!!ttyUser.data.user);
  catalog.setMember(actor,org,ttyUser.data.user!.id,'owner');
  const terminalConfig=join(config.directory,'terminal-'+scenario+'.json'),name='terminal-'+scenario;
  writeFileSync(terminalConfig,JSON.stringify({root,script:launcher,email:ttyEmail,password,project,name,scenario,marker}),{mode:0o600});
  const terminalChild=Bun.spawn(['python3',join(import.meta.dir,'management-cli-terminal.py'),terminalConfig],{stdout:'pipe',stderr:'pipe'});
  const terminalResult=await new Response(terminalChild.stdout).json();
  terminals.push({scenario,...terminalResult});
  check('terminal '+scenario+' reports only sanitized observations',await terminalChild.exited===0&&terminalResult.total>=5);
  for(const observation of terminalResult.checks)check(observation.name,observation.ok);
  check('terminal '+scenario+' catalog mutation matches authorization',catalog.listEnvironments(actor,project).some(e=>e.name===name)===scenario.startsWith('success'));
  check('terminal '+scenario+' protected requests match authorization',scenario.startsWith('success')?terminalProtected>0:terminalProtected===0);
  if(scenario.startsWith('headless-'))check('actual '+scenario+' refuses before any native Auth request',terminalRequests===0);
 }
}catch(error){checks.push({name:error instanceof Error&&error.message.startsWith('Failed: ')?error.message:'CLI fixture runtime failed',ok:false});}
finally{server.stop(true);catalog.close();keys.close();}
process.stdout.write(JSON.stringify({total:checks.length,failed:checks.filter(c=>!c.ok).length,skipped:0,checks,terminal_observations:terminals})+'\n');
