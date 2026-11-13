import {chromium} from './mfa-browser/node_modules/playwright/index.mjs';
import {Catalog} from '../src/control/catalog';
import {studioProxy,studioHost,signStudio,STUDIO_COOKIE} from '../src/control/studio';
import {studioSmtpConfig} from '../src/control/studio-smtp';
import {isStudioSmtpAsset,STUDIO_SMTP_ASSET_VERSION} from '../src/control/studio-smtp-assets';
import {mkdtempSync,mkdirSync,rmSync,writeFileSync,readFileSync} from 'node:fs';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';
import {KINDS,readTemplates} from '../src/control/mail-templates';
/** Exercise a pinned native Studio UI against temporary local settings only.
 * SBAR_STUDIO_TEST_URL supplies original static Studio assets through a read-only URL.
 * No source request may mutate the configured Studio; SMTP writes terminate locally. */
const configured=process.env.SBAR_STUDIO_TEST_URL;
if(!configured)throw new Error('Set SBAR_STUDIO_TEST_URL to a read-only pinned Studio URL.');
const source=new URL(configured);
if(!['http:','https:'].includes(source.protocol)||source.username||source.password||source.pathname!=='/'||source.search||source.hash)
 throw new Error('SBAR_STUDIO_TEST_URL must be an HTTP origin without credentials, path, query or fragment.');
const a=resolve(process.env.SBAR_BROWSER_ARTIFACTS||'output/playwright/studio-smtp');mkdirSync(a,{recursive:true});
for(const name of ['native-browser-checks.json','native-browser-observations.json','native-browser-failure.txt'])rmSync(join(a,name),{force:true});
let blockedSourceMutations=0,staleAssetHits=0,versionedAssets=0;
const network=[],diagnostics=[];const checks=[];const check=(name,ok)=>{checks.push({name,passed:!!ok});if(!ok)throw new Error(name);};
const directory=mkdtempSync(join(tmpdir(),'native-smtp-')),c=new Catalog(':memory:');let browser,server;

try{
 const upstream=await fetch(new URL('/api/platform/auth/default/config',source),{signal:AbortSignal.timeout(10000)});
 if(upstream.status!==404)throw new Error('Expected the pinned original Studio config route to return 404.');
 void upstream.body?.cancel().catch(()=>{});
 const org=c.createOrganization('alice','SMTP isolation fixture'),project=c.createProject('alice',org,'SMTP fixture'),env=c.createEnvironment('alice',project,'test');const job=c.claimProvision();c.finishProvision(env,job.claim,true);const runtime=c.getProvision('alice',env).runtime,key=Buffer.alloc(32,21);
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:(actor,r)=>c.studioAllowed(actor,r),navigation:(...args)=>c.studioNavigation(...args),authConfig:studioSmtpConfig(c,directory,directory,0),transport:Object.assign(async(url,init)=>{
  if(!['GET','HEAD','OPTIONS'].includes(init?.method??'GET')){blockedSourceMutations++;return new Response('Source Studio is read-only in this isolated test',{status:405});}
  return fetch(url,init);
 },{preconnect:()=>{}}) as typeof fetch,upstream:()=>source.origin});
 server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:async request=>{
  const url=new URL(request.url);
  // Model a CDN entry created before the repair: its immutable canonical JavaScript
  // bypasses the origin proxy. Versioned URLs must reach the compatibility adapter.
  if(isStudioSmtpAsset(url.pathname)){
   if(!url.searchParams.has('sbarbase_smtp')){staleAssetHits++;return fetch(new URL(url.pathname,source),{decompress:false});}
   if(url.searchParams.get('sbarbase_smtp')===STUDIO_SMTP_ASSET_VERSION)versionedAssets++;
  }
  const response=await proxy(request);network.push({path:url.pathname,status:response.status,type:response.headers.get('content-type')});writeFileSync(a+'/native-network.json',JSON.stringify(network,null,2));return response;
 }});const origin='http://'+studioHost(runtime)+':'+server.port;
 const probe=await proxy(new Request(origin+'/project/default/auth/smtp',{headers:{accept:'text/html',cookie:STUDIO_COOKIE+'='+signStudio(key,{runtime,actor:'alice',epoch:0,kind:'session',expires:Date.now()+300000})}}));const probeText=await probe.text();writeFileSync(a+'/native-http-probe.json',JSON.stringify({length:probeText.length,scripts:(probeText.match(/<script/g)||[]).length,headers:Object.fromEntries([...probe.headers].filter(([key])=>key!=='set-cookie'))},null,2));
 browser=await chromium.launch({headless:true,executablePath:process.env.SBAR_BROWSER_EXECUTABLE||'/usr/bin/chromium-browser',args:['--no-sandbox']});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.addCookies([{name:STUDIO_COOKIE,value:signStudio(key,{runtime,actor:'alice',epoch:0,kind:'session',expires:Date.now()+300000}),url:origin,httpOnly:true,sameSite:'Lax'}]);
 const page=await context.newPage();const resets=[],responses=[],errors=[];page.on('request',r=>{if(r.url().includes('/templates/')&&r.url().endsWith('/reset'))resets.push(r.url());});page.on('pageerror',e=>{errors.push(e.message);diagnostics.push(e.message);writeFileSync(a+'/native-page-errors.json',JSON.stringify(diagnostics,null,2));});page.on('console',e=>{if(e.type()==='error'){diagnostics.push(e.text());writeFileSync(a+'/native-page-errors.json',JSON.stringify(diagnostics,null,2));}});page.on('response',r=>{if(r.url().includes('/api/platform/auth/default/config'))responses.push({method:r.request().method(),status:r.status()});});
 // Await the configuration response and native hydration effects, not unrelated Studio polling.
 const initialRead=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='GET'&&r.status()===200);const r=await page.goto(origin+'/project/default/auth/smtp',{waitUntil:'domcontentloaded'});check('native pinned SMTP page opens',r.status()===200);
 const toggle=page.getByRole('switch').first();await toggle.waitFor();await page.waitForFunction(()=>document.querySelector('[role="switch"]')?.getAttribute('aria-checked')==='false');await (await initialRead).finished();await page.waitForTimeout(1000);check('native form reads disabled backend state',responses.some(r=>r.method==='GET'&&r.status===200));
 await toggle.click();await page.getByLabel('Sender email address',{exact:true}).fill('noreply@example.com');await page.getByLabel('Sender name',{exact:true}).fill('Isolated fixture');await page.getByLabel('Host',{exact:true}).fill('smtp.example.com');await page.getByLabel('Port number',{exact:true}).fill('587');await page.getByLabel('Username',{exact:true}).fill('apikey');await page.getByLabel('Password',{exact:true}).fill('fixture-password-never-sent');
 const enabled=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='PATCH'&&r.status()===202);await page.getByRole('button',{name:'Save changes',exact:true}).click();await enabled;check('native form submits supported uppercase PATCH',true);
 var reloadRead=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='GET'&&r.status()===200);await page.reload({waitUntil:'domcontentloaded'});await reloadRead;await page.waitForTimeout(500);await page.getByLabel('Sender email address',{exact:true}).waitFor();check('reload restores sender',await page.getByLabel('Sender email address',{exact:true}).inputValue()==='noreply@example.com');check('reload restores enabled state',await toggle.getAttribute('aria-checked')==='true');check('stored password stays out of input',await page.getByLabel('Password',{exact:true}).inputValue()==='');
 await page.getByLabel('Sender name',{exact:true}).fill('Edited without password');const saved=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='PATCH'&&r.status()===202);await page.getByRole('button',{name:'Save changes',exact:true}).click();await saved;check('edit without password succeeds',JSON.parse(readFileSync(join(directory,runtime+'-mail.json'),'utf8')).pass==='fixture-password-never-sent');
 var reloadRead=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='GET'&&r.status()===200);await page.reload({waitUntil:'domcontentloaded'});await reloadRead;await page.waitForTimeout(500);check('edited sender persists',await page.getByLabel('Sender name',{exact:true}).inputValue()==='Edited without password');
 const template=JSON.stringify({version:1,token:'a'.repeat(64),templates:{[KINDS[0]]:{subject:'Isolated saved template',body:'<p>Preserved fixture</p>'}}});writeFileSync(join(directory,runtime+'-templates.json'),template,{mode:0o600});check('fixture contains a valid saved template override',!!readTemplates(directory,runtime)?.templates[KINDS[0]]);await toggle.click();await page.getByRole('button',{name:'Save changes',exact:true}).click();const dialog=page.getByRole('alertdialog');await dialog.waitFor();check('disable confirmation explains self-hosted behavior',(await dialog.innerText()).includes('Existing email templates will be kept.'));check('disable has no Cloud upgrade claim',!(await dialog.innerText()).includes('upgrade your plan'));
 const disabled=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='PATCH'&&r.status()===202);await dialog.getByRole('button',{name:'Disable custom SMTP',exact:true}).click();await disabled;var reloadRead=page.waitForResponse(r=>r.url().includes('/api/platform/auth/default/config')&&r.request().method()==='GET'&&r.status()===200);await page.reload({waitUntil:'domcontentloaded'});await reloadRead;await page.waitForTimeout(500);check('disable persists on reload',await toggle.getAttribute('aria-checked')==='false');check('disable does not fake template resets',resets.length===0);check('disable preserves existing template file',readFileSync(join(directory,runtime+'-templates.json'),'utf8')===template);check('native form has no unhandled application errors',errors.length===0);check('source Studio received no mutation requests',blockedSourceMutations===0);
 check('versioned native chunks bypass stale CDN entries',versionedAssets>=2);check('native chunks never load the stale canonical entries',staleAssetHits===0);
 await page.screenshot({path:a+'/native-smtp-disabled.png'});writeFileSync(a+'/native-browser-observations.json',JSON.stringify({checks,responses,errors,staleAssetHits,versionedAssets},null,2));
}catch(e){writeFileSync(a+'/native-browser-failure.txt',String(e));if(browser){const pages=browser.contexts().flatMap(c=>c.pages());if(pages[0]){await pages[0].screenshot({path:a+'/native-smtp-failed.png'});writeFileSync(a+'/native-smtp-failed.html',await pages[0].content());}}checks.push({name:String(e),passed:false});}
finally{if(browser)await browser.close();if(server)server.stop(true);c.close();rmSync(directory,{recursive:true,force:true});writeFileSync(a+'/native-browser-checks.json',JSON.stringify(checks,null,2));writeFileSync(a+'/native-source-safety.json',JSON.stringify({blockedSourceMutations,sourceRequestsReadOnly:true},null,2));}
console.log(JSON.stringify({total:checks.length,failed:checks.filter(x=>!x.passed).length,skipped:0}));if(checks.some(x=>!x.passed))process.exitCode=1;
