import {chromium} from './mfa-browser/node_modules/playwright/index.mjs';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {join,resolve} from 'node:path';

/** Fresh profile, real console, real original Auth. Credentials never enter exported artifacts. */
export async function browserCheck(config:{directory:string;endpoint:string;anonymousToken:string;email:string;password:string;owner:string},
 transport:typeof fetch,authenticator:(secret:string)=>string,check:(name:string,ok:unknown)=>void){
 const catalog=new Catalog(join(config.directory,'browser-catalog.db')),keys=new KeyStore(join(config.directory,'browser-keys.db'));
 catalog.initializeInstallation('browser-fixture',config.owner,'Browser fixture');
 const handler=application(catalog,keys,{auth:config.endpoint,anonymousToken:config.anonymousToken,publishableKey:'sb_publishable_sbarbase_local_management'},()=>undefined,transport);
 const publicRoot=resolve('.lab/ui');let passwordToken='',secret='';
 const server=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(request){
  const path=new URL(request.url).pathname;
  if(path.startsWith('/management/')){
   const response=await handler(request);
   if(path==='/management/auth/v1/token'&&new URL(request.url).searchParams.get('grant_type')==='password'&&response.ok)
    passwordToken=(await response.clone().json()).access_token;
   if(path==='/management/auth/v1/factors'&&response.ok)secret=(await response.clone().json()).totp.secret;
   return response;
  }
  const file=resolve(publicRoot,path==='/'?'index.html':'.'+path);
  if(file!==publicRoot&&!file.startsWith(publicRoot+'/'))return new Response(null,{status:404});
  const content=Bun.file(file);return await content.exists()?new Response(content):new Response(null,{status:404});
 }});
 let browser,phase='launch';
 try{
  const executablePath=process.env.CHROMIUM_BIN||Bun.which('google-chrome')||Bun.which('chromium')||Bun.which('chromium-browser');
  if(!executablePath)throw new Error('Install a Chromium browser or set CHROMIUM_BIN');
  browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--disable-extensions','--disable-component-update','--renderer-process-limit=1']});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  // No request leaves the isolated fixture's loopback web origin.
  await context.route('**/*',route=>new URL(route.request().url()).origin===server.url.origin?route.continue():route.abort());
  const page=await context.newPage();
  phase='password login';
  await page.goto(server.url.href);
  await page.getByLabel('Email address').fill(config.email);
  await page.getByLabel('Password',{exact:true}).fill(config.password);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('heading',{name:'Set up an authenticator'}).waitFor();
  check('actual browser password login is gated before management',!!passwordToken&&(await handler(new Request(server.url+'management/v1/organizations',{headers:{authorization:'Bearer '+passwordToken}}))).status===403);
  await page.screenshot({path:join(config.directory,'mfa-enrollment.png')});
  phase='enrollment';
  await page.getByRole('button',{name:'Set up authenticator',exact:true}).click();
  await page.getByLabel('Six digit code').waitFor();
  check('actual browser renders native QR enrollment',!!secret&&await page.getByAltText('Authenticator setup QR code').isVisible());
  phase='invalid code';
  await page.getByLabel('Six digit code').fill('12');
  await page.getByRole('button',{name:'Verify code',exact:true}).click();
  await page.getByText('Enter the six digit code from your authenticator.').waitFor();
  check('actual browser invalid code has linked accessible error',await page.getByLabel('Six digit code').getAttribute('aria-invalid')==='true');
  await page.screenshot({path:join(config.directory,'mfa-invalid-code.png'),mask:[page.getByAltText('Authenticator setup QR code'),page.locator('details')]});
  const wrong=String((parseInt(authenticator(secret),10)+1)%1000000).padStart(6,'0');
  await page.getByLabel('Six digit code').fill(wrong);
  await page.getByRole('button',{name:'Verify code',exact:true}).click();
  await page.getByText('The code was not accepted. Use the current code from your authenticator.').waitFor();
  check('actual browser presents native wrong-code failure',await page.getByLabel('Six digit code').getAttribute('aria-invalid')==='true');
  phase='native verify';
  await page.getByLabel('Six digit code').fill(authenticator(secret));
  await page.getByRole('button',{name:'Verify code',exact:true}).click();
  await page.getByRole('navigation',{name:'Main'}).waitFor();
  check('actual browser native MFA opens console',await page.getByRole('button',{name:'Projects',exact:true}).isVisible());
  phase='account security';
  await page.getByRole('button',{name:'Account security',exact:true}).click();
  await page.getByRole('heading',{name:'Account security',exact:true}).waitFor();
  check('actual browser preserves last-factor protection',await page.getByRole('button',{name:'Remove',exact:true}).isDisabled());
  await page.screenshot({path:join(config.directory,'mfa-account-security.png')});
  phase='subsequent sign in';
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await page.getByRole('heading',{name:'Welcome back'}).waitFor();
  await page.getByLabel('Email address').fill(config.email);
  await page.getByLabel('Password',{exact:true}).fill(config.password);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('heading',{name:'Verify your sign in'}).waitFor();
  check('actual browser later sign-in challenges existing authenticator',await page.getByLabel('Six digit code').isVisible());
  await page.screenshot({path:join(config.directory,'mfa-challenge.png')});
  await page.getByLabel('Six digit code').fill(authenticator(secret));
  await page.getByLabel('Six digit code').press('Enter');
  await page.getByRole('navigation',{name:'Main'}).waitFor();
  check('actual browser keyboard verification succeeds',await page.getByRole('button',{name:'Account security',exact:true}).isVisible());
 }catch(error){throw new Error('Browser check failed at '+phase+' ('+(error instanceof Error?error.name:'unknown')+')');
 }finally{await browser?.close();await server.stop(true);catalog.close();keys.close();}
}
