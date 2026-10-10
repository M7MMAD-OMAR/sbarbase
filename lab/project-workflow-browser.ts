import {chromium} from './mfa-browser/node_modules/playwright/index.mjs';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {studioProxy} from '../src/control/studio';
import {managementToken,sessionId,factorId,challengeId,verifiedFactor} from '../tests/management-fixture';
import {mkdtempSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
const directory=mkdtempSync(join(tmpdir(),'sbarbase-project-browser-'));
const artifacts=resolve('output/playwright/project-workflow');mkdirSync(artifacts,{recursive:true});
const catalog=new Catalog(join(directory,'catalog.sqlite')),keys=new KeyStore(join(directory,'keys.sqlite'));
const owner='66666666-6666-4666-8666-666666666666',organization=catalog.initializeInstallation('fixture',owner,'sbarah');
catalog.managementSecurity.setPolicy(false);
let factors:typeof verifiedFactor[]=[],startRequests=0,failStart=false,failStatus=false,rejectProject=false;
const user=()=>({id:owner,email:'owner@example.com',is_anonymous:false,factors});
const session=(aal='aal1')=>({access_token:managementToken(owner,aal,sessionId),refresh_token:'browser-fixture',expires_in:3600,token_type:'bearer',user:user()});
const transport=Object.assign(async(input:RequestInfo|URL,init?:RequestInit)=>{
 const path=new URL(String(input)).pathname;
 if(path==='/user')return Response.json(user());
 if(path==='/token')return Response.json(session());
 if(path==='/logout')return new Response(null,{status:204});
 if(path==='/factors'){factors=[{...verifiedFactor,status:'unverified'}];return Response.json({id:factorId,type:'totp',totp:{secret:'FIXTURE',qr_code:'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>',uri:'otpauth://fixture'}});}
 if(path.endsWith('/challenge'))return Response.json({id:challengeId});
 if(path.endsWith('/verify')){factors=[verifiedFactor];return Response.json(session('aal2'));}
 return Response.json({message:'Fixture upstream unavailable'},{status:404});
},{preconnect:()=>{}}) as typeof fetch;
const studioKey=Buffer.alloc(32,7);
const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'fixture',publishableKey:'sb_publishable_sbarbase_local_management'},
 ()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',anonymousToken:'anon',keys:[],enabled:true}),transport,()=>studioKey);
const proxy=studioProxy({key:()=>studioKey,allowed:(actor,runtime)=>catalog.studioAllowed(actor,runtime),epoch:actor=>catalog.managementSecurity.epoch(actor),
 runtimeEpoch:runtime=>catalog.runtimeEpoch(runtime),navigation:(...args)=>catalog.studioNavigation(...args),selection:(actor,id)=>catalog.studio(actor,id),upstream:()=> 'http://studio.invalid',
 transport:Object.assign(async()=>new Response('<h1>Fixture Supabase Studio</h1>',{headers:{'content-type':'text/html'}}),{preconnect:()=>{}}) as typeof fetch});
const publicRoot=resolve('.lab/ui');
const server=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(request){
 const url=new URL(request.url),path=url.pathname;
 if(url.hostname!=='127.0.0.1')return proxy(request);
 if(path.startsWith('/management/')){
  if(request.method==='GET'&&/\/studio$/.test(path)&&failStatus){failStatus=false;return Response.json({error:'Temporarily unavailable'},{status:503});}
  if(request.method==='POST'&&/\/projects$/.test(path)&&rejectProject){rejectProject=false;return Response.json({error:'Temporarily unavailable'},{status:503});}
  const response=await handler(request);
  if(request.method==='POST'&&/\/studio$/.test(path)&&response.ok){
   startRequests++;const environment=path.split('/')[4]!,runtime=catalog.studio(owner,environment).runtime;
   setTimeout(()=>{(catalog as unknown as {db:{query:(sql:string)=>{run:(...args:unknown[])=>void}}}).db.query('UPDATE studio_sessions SET state=?,failure=? WHERE runtime=?').run(failStart?'failed':'running',failStart?'Fixture start failed':null,runtime);},50);
  }
  if(request.method==='DELETE'&&/\/studio$/.test(path)&&response.ok){
   const environment=path.split('/')[4]!,runtime=catalog.studio(owner,environment).runtime;
   setTimeout(()=>{(catalog as unknown as {db:{query:(sql:string)=>{run:(...args:unknown[])=>void}}}).db.query('UPDATE studio_sessions SET state=? WHERE runtime=?').run('stopped',runtime);},50);
  }
  return response;
 }
 const file=resolve(publicRoot,path==='/'?'index.html':'.'+path);
 if(!file.startsWith(publicRoot+'/'))return new Response(null,{status:404});
 const content=Bun.file(file);return await content.exists()?new Response(content):new Response(null,{status:404});
}});
let browser;let total=0,failed=0;const checks:string[]=[];
function check(name:string,value:unknown){total++;if(!value){throw new Error(name);}checks.push(name);}
try{
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1704,height:850},colorScheme:'dark'});
 const page=await context.newPage();const faults:string[]=[];page.on('pageerror',error=>faults.push(error.message));
 await page.goto(`http://127.0.0.1:${server.port}`);
 await page.getByLabel('Email address').fill('owner@example.com');await page.getByLabel('Password',{exact:true}).fill('browser-fixture-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByRole('heading',{name:'Projects',exact:true}).waitFor();
 check('password sign-in enters console without forced MFA enrollment',!await page.getByRole('heading',{name:'Set up an authenticator'}).count());
 await page.getByRole('button',{name:'New project',exact:true}).click();
 check('project form explains automatic production',await page.getByText('Production is created automatically. Your Supabase dashboard opens in a new tab when ready.').isVisible());
 await page.getByLabel('Project name',{exact:true}).fill('Tdabar');const createdTabPromise=context.waitForEvent('page');await page.getByLabel('Project name',{exact:true}).press('Enter');const createdTab=await createdTabPromise;
 await page.getByRole('heading',{name:'Tdabar',exact:true}).waitFor();
 const project=catalog.listProjects(owner,organization)[0]!,environment=catalog.listEnvironments(owner,project.id)[0]!;
 check('created project is selected and production exists',environment.name==='production'&&environment.state==='queued');
 const dashboard=page.getByRole('button',{name:'Open Supabase dashboard',exact:true});await dashboard.waitFor();
 check('dashboard button is visible and disabled during provisioning',await dashboard.isVisible()&&await dashboard.isDisabled());
 const job=catalog.claimProvision()!;await page.getByText('Provisioning',{exact:true}).waitFor();
 await page.screenshot({path:join(artifacts,'production-provisioning.png'),fullPage:true});
 check('automatic opening is explained and can be cancelled',await page.getByRole('button',{name:'Stay in console',exact:true}).isVisible());
 check('advanced project actions are collapsed',!await page.getByRole('button',{name:'Rename project',exact:true}).isVisible());
 catalog.finishProvision(job.environment,job.claim!,true);
 await createdTab.getByRole('heading',{name:'Fixture Supabase Studio'}).waitFor();
 check('new project automatically starts and opens native Studio while retaining the console',startRequests===1&&createdTab.url().includes('/project/default')&&!createdTab.url().includes('/__sbarbase/workspace')&&page.url().includes('127.0.0.1'));
 check('native dashboard is a full page without an embedded frame',await createdTab.locator('iframe').count()===0);
 const studioUrl=createdTab.url();await createdTab.close();
 const existingDashboard=page.getByRole('button',{name:'Open Supabase dashboard',exact:true});await existingDashboard.waitFor();
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 check('existing project stays in the console with dashboard access',await existingDashboard.isEnabled()&&page.url().includes('127.0.0.1'));
 const popupPromise=context.waitForEvent('page');await existingDashboard.click();const popup=await popupPromise;
 await popup.getByRole('heading',{name:'Fixture Supabase Studio'}).waitFor();
 check('one click reopens native Studio using a signed ticket',startRequests===1&&popup.url().includes('/project/default'));
 await popup.close();await page.screenshot({path:join(artifacts,'production-ready.png'),fullPage:true});
 await page.getByRole('button',{name:'Connection details',exact:true}).click();
 await page.getByRole('heading',{name:'Supabase dashboard',exact:true}).waitFor();
 check('environment connection also exposes dashboard access',await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).isVisible());
 await page.getByRole('button',{name:'Database connection',exact:true}).click();
 await page.getByRole('button',{name:'Stop dashboard',exact:true}).click();
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 failStart=true;const failedPopupPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).click();
 const failedPopup=await failedPopupPromise;await failedPopup.waitForEvent('close');
 check('failed dashboard start closes the pending tab and shows recovery',await page.getByText('The dashboard could not start. Try again.',{exact:true}).isVisible());
 failStart=false;const retryPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).click();
 const retried=await retryPromise;await retried.getByRole('heading',{name:'Fixture Supabase Studio'}).waitFor();await retried.close();
 check('dashboard retries after a worker failure',startRequests===3);
 await page.getByRole('button',{name:'Stop dashboard',exact:true}).click();
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 failStatus=true;const outagePromise=context.waitForEvent('page');await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).click();
 const outagePopup=await outagePromise;await outagePopup.waitForEvent('close');
 const statusRetry=page.getByRole('button',{name:'Retry dashboard status',exact:true});
 check('transient status failure closes pending tab and exposes local retry',await statusRetry.isVisible());
 await statusRetry.click();await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 const recoveredPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).click();
 const recovered=await recoveredPromise;await recovered.getByRole('heading',{name:'Fixture Supabase Studio'}).waitFor();await recovered.close();
 check('local status retry restores dashboard access after a transient failure',startRequests===4);
 await page.getByRole('button',{name:'Overview',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 check('mobile page has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
 await page.screenshot({path:join(artifacts,'connection-mobile.png'),fullPage:true});
 await page.getByRole('button',{name:'Projects',exact:true}).first().click();await page.getByRole('button',{name:'New project',exact:true}).click();
 await page.getByLabel('Project name',{exact:true}).fill('Keep in console');const cancelTabPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Create',exact:true}).click();const cancelTab=await cancelTabPromise;
 await page.getByRole('heading',{name:'Keep in console',exact:true}).waitFor();await page.getByRole('button',{name:'Stay in console',exact:true}).click();
 const cancelledJob=catalog.claimProvision()!;catalog.finishProvision(cancelledJob.environment,cancelledJob.claim!,true);
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 check('cancel automatic opening keeps the project ready in the console',page.url().includes('127.0.0.1')&&startRequests===4&&cancelTab.isClosed());
 await page.getByText('Project settings',{exact:true}).first().click();
 check('advanced project actions remain accessible on request',await page.getByRole('button',{name:'Rename project',exact:true}).isVisible());
 await page.getByRole('button',{name:'Projects',exact:true}).first().click();await page.getByRole('button',{name:'New project',exact:true}).click();
 await page.getByLabel('Project name',{exact:true}).fill('Closed tab');const closedTabPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Create',exact:true}).click();const closedTab=await closedTabPromise;await closedTab.close();
 await page.getByRole('heading',{name:'Closed tab',exact:true}).waitFor();const closedJob=catalog.claimProvision()!;catalog.finishProvision(closedJob.environment,closedJob.claim!,true);
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.includes('Open Supabase dashboard')&&!button.disabled));
 check('closing the waiting tab cancels automatic Studio opening',page.url().includes('127.0.0.1')&&startRequests===4);
 await page.getByRole('button',{name:'Projects',exact:true}).first().click();await page.getByRole('button',{name:'New project',exact:true}).click();
 await page.evaluate(()=>{(window as unknown as {savedOpen:typeof window.open}).savedOpen=window.open;window.open=()=>null;});await page.getByLabel('Project name',{exact:true}).fill('Blocked tab');await page.getByRole('button',{name:'Create',exact:true}).click();
 await page.getByRole('heading',{name:'Blocked tab',exact:true}).waitFor();const blockedJob=catalog.claimProvision()!;catalog.finishProvision(blockedJob.environment,blockedJob.claim!,true);
 await page.getByText('The dashboard is ready. Choose Open Supabase dashboard to open it.',{exact:true}).waitFor();
 check('popup blocking prepares the dashboard and keeps an actionable console fallback',page.url().includes('127.0.0.1')&&startRequests===5&&await page.getByRole('button',{name:'Open Supabase dashboard',exact:true}).isEnabled());
 await page.evaluate(()=>{window.open=(window as unknown as {savedOpen:typeof window.open}).savedOpen;});
 await page.getByRole('button',{name:'Projects',exact:true}).first().click();await page.getByRole('button',{name:'New project',exact:true}).click();
 const beforeProjects=catalog.listProjects(owner,organization).length;rejectProject=true;
 await page.getByLabel('Project name',{exact:true}).fill('Refused project');const refusedTabPromise=context.waitForEvent('page');await page.getByRole('button',{name:'Create',exact:true}).click();const refusedTab=await refusedTabPromise;if(!refusedTab.isClosed())await refusedTab.waitForEvent('close');
 check('creation failure closes the pending tab without a partial project',catalog.listProjects(owner,organization).length===beforeProjects&&await page.getByLabel('Project name',{exact:true}).isVisible());
 await page.getByRole('button',{name:'Account security',exact:true}).click();
 await page.getByText('MFA is optional. You can sign in with your password or add an authenticator.').waitFor();
 check('optional account security permits enrollment',await page.getByText('MFA is optional. You can sign in with your password or add an authenticator.').isVisible());
 await page.getByRole('button',{name:'Add authenticator',exact:true}).click();await page.getByRole('button',{name:'Set up authenticator',exact:true}).click();
 await page.getByLabel('Six digit code').fill('123456');await page.getByRole('button',{name:'Verify code',exact:true}).click();
 await page.getByRole('heading',{name:'Account security',exact:true}).waitFor();
 check('enrollment verifies and optional policy permits last-factor removal',await page.getByRole('button',{name:'Remove',exact:true}).isEnabled());
 check('first verified enrollment revokes prior password Studio cookies',(await context.request.get(studioUrl)).status()===401);
 await page.setViewportSize({width:1704,height:850});await page.screenshot({path:join(artifacts,'account-security.png'),fullPage:true});
 await page.getByRole('button',{name:'Require MFA',exact:true}).click();await page.getByLabel('Six digit code').fill('123456');
 await page.getByRole('button',{name:'Verify code',exact:true}).click();await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();
 check('fresh owner verification changes installation policy and ends sign-in',catalog.managementSecurity.policy().mfa_required===true);
 check('policy change revokes the previously opened Studio cookie',(await context.request.get(studioUrl)).status()===401);
 const requiredContext=await browser.newContext({viewport:{width:1704,height:850}}),requiredPage=await requiredContext.newPage();factors=[];
 await requiredPage.goto(`http://127.0.0.1:${server.port}`);await requiredPage.getByLabel('Email address').fill('owner@example.com');await requiredPage.getByLabel('Password',{exact:true}).fill('browser-fixture-password');await requiredPage.getByRole('button',{name:'Sign in',exact:true}).click();
 await requiredPage.getByRole('heading',{name:'Set up an authenticator',exact:true}).waitFor();
 check('required policy routes an unenrolled account to setup',await requiredPage.getByRole('button',{name:'Set up authenticator',exact:true}).isVisible());await requiredContext.close();
 check('browser has no unhandled application errors',faults.length===0);
}catch(error){failed++;checks.push(String(error));}
finally{await browser?.close();server.stop(true);catalog.close();keys.close();rmSync(directory,{recursive:true,force:true});}
console.log(JSON.stringify({total,failed,skipped:0,checks}));if(failed)process.exitCode=1;
