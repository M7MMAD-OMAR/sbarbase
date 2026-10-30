import {test,expect} from 'bun:test';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {signInHandler} from '../src/control/sign-in';
import {authConfigView,REGISTRY} from '../src/control/auth-config';

const github={site_url:'https://a.example.com',redirect_urls:['https://a.example.com/**'],signup:false,anonymous:true,
 session:{access_token_seconds:1800,timebox_seconds:7200,inactivity_seconds:null},
 providers:{github:{enabled:true,client_id:'Iv1.alpha',secret:'ALPHA-SECRET-VALUE',url:''},
  azure:{enabled:true,client_id:'az',secret:'AZURE-SECRET-VALUE',url:'https://login.microsoftonline.com/tenant/v2.0'}}};

const field=(view:ReturnType<typeof authConfigView>,key:string)=>[...view.pages.flatMap(page=>page.fields),...view.providers.flatMap(provider=>provider.fields)]
 .find(entry=>entry.key===key)!;

test('an environment without saved settings shows the pinned defaults, marked as defaults',()=>{
 const view=authConfigView(null,'e_'+'a'.repeat(24),'http://localhost');
 expect(view.configured).toBe(false);
 expect(field(view,'SITE_URL')).toMatchObject({value:'http://localhost',from_default:true});
 expect(field(view,'DISABLE_SIGNUP')).toMatchObject({value:false,from_default:true});
 expect(field(view,'SESSIONS_TIMEBOX')).toMatchObject({value:null,from_default:true,null_text:'No limit'});
 // No default is claimed for the access token lifetime: the pinned Auth applies its own.
 expect(field(view,'JWT_EXP')).toMatchObject({value:null,null_text:'Auth default'});
 // The key is DISABLE_SIGNUP; the label asks the positive question, so the field says to invert it.
 expect(field(view,'DISABLE_SIGNUP')).toMatchObject({invert:true,label:'Allow new users to sign up'});
 expect(field(view,'EXTERNAL_GITHUB_ENABLED')).toMatchObject({value:false,from_default:true});
 expect(view.callback_url).toBe('http://localhost/e_'+'a'.repeat(24)+'/auth/v1/callback');
});

test('saved settings appear under the platform key names, and a secret is never in the view',()=>{
 const settings={revision:3,...github} as never;
 const view=authConfigView(settings,'e_'+'b'.repeat(24),'http://localhost');
 expect(view.configured).toBe(true);
 expect(field(view,'SITE_URL')).toMatchObject({value:'https://a.example.com',from_default:false});
 expect(field(view,'URI_ALLOW_LIST').value).toEqual(['https://a.example.com/**']);
 // The platform key is DISABLE_SIGNUP; the saved flag is `signup`, so the value is inverted.
 expect(field(view,'DISABLE_SIGNUP').value).toBe(true);
 expect(field(view,'EXTERNAL_ANONYMOUS_USERS_ENABLED').value).toBe(true);
 expect(field(view,'JWT_EXP').value).toBe(1800);
 expect(field(view,'SESSIONS_INACTIVITY_TIMEOUT')).toMatchObject({value:null,from_default:true});
 expect(field(view,'EXTERNAL_GITHUB_CLIENT_ID').value).toBe('Iv1.alpha');
 expect(field(view,'EXTERNAL_GITHUB_SECRET')).toEqual({key:'EXTERNAL_GITHUB_SECRET',label:'secret',type:'secret',secret_set:true,from_default:false});
 expect(field(view,'EXTERNAL_GOOGLE_SECRET').secret_set).toBe(false);
 expect(field(view,'EXTERNAL_AZURE_URL').value).toBe('https://login.microsoftonline.com/tenant/v2.0');
 expect(field(view,'EXTERNAL_GITHUB_REDIRECT_URI').value).toBe(view.callback_url);
 expect(JSON.stringify(view)).not.toContain('SECRET-VALUE');
 // Providers that name no server of their own have no URL field.
 expect(view.providers.find(provider=>provider.id==='github')!.fields.some(entry=>entry.key==='EXTERNAL_GITHUB_URL')).toBe(false);
 expect(view.providers.find(provider=>provider.id==='keycloak')!.fields.some(entry=>entry.key==='EXTERNAL_KEYCLOAK_URL')).toBe(true);
});

test('the registry lists the providers the saved settings accept, no more and no fewer',async()=>{
 const {PROVIDERS,PROVIDER_URL}=await import('../src/control/sign-in');
 expect(Object.keys(REGISTRY.providers).sort()).toEqual([...PROVIDERS].sort());
 for(const name of PROVIDERS)expect(REGISTRY.providers[name]!.url!=='none').toBe(name in PROVIDER_URL);
 for(const name of PROVIDERS)expect((REGISTRY.providers[name]!.url==='required')).toBe(PROVIDER_URL[name]===true);
});

test('the route is read only, owner and admin only, and each environment shows only its own settings',async()=>{
 const catalog=new Catalog(':memory:');
 const directory=mkdtempSync(join(tmpdir(),'auth-config-'));
 try {
  const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
  const project=catalog.createProject('alice',org,'P');
  const first=catalog.createEnvironment('alice',project,'production'),second=catalog.createEnvironment('alice',project,'staging');
  for(let i=0;i<2;i++){const job=catalog.claimProvision()!;catalog.finishProvision(job.environment,job.claim!,true);}
  const handler=signInHandler(catalog,async request=>request.headers.get('authorization'),directory,0);
  const url=(environment:string,leaf:string)=>`http://local/management/v1/environments/${environment}/${leaf}`;
  const get=(actor:string,environment:string,leaf='auth-config',method='GET')=>handler(new Request(url(environment,leaf),{method,headers:{authorization:actor}}));
  expect((await get('carol',first)).status).toBe(403);
  expect((await get('bob',first)).status).toBe(403);
  expect((await get('alice',first,'auth-config','PUT')).status).toBe(405);
  expect((await get('alice',first,'auth-config','POST')).status).toBe(405);
  const save=await handler(new Request(url(first,'sign-in'),{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify(
   {site_url:'https://a.example.com',providers:{github:{enabled:true,client_id:'Iv1.alpha',secret:'ALPHA-SECRET-VALUE'}}})}));
  expect(save.status).toBe(202);
  type Answer={data:{state:string;config:ReturnType<typeof authConfigView>}};
  const one=await (await get('alice',first)).json() as Answer;
  const two=await (await get('alice',second)).json() as Answer;
  expect(one.data.state).toBe('pending');
  expect(field(one.data.config,'EXTERNAL_GITHUB_ENABLED').value).toBe(true);
  expect(field(one.data.config,'EXTERNAL_GITHUB_SECRET').secret_set).toBe(true);
  expect(two.data.state).toBe('unconfigured');
  expect(two.data.config.configured).toBe(false);
  expect(field(two.data.config,'EXTERNAL_GITHUB_ENABLED').value).toBe(false);
  expect(field(two.data.config,'EXTERNAL_GITHUB_CLIENT_ID').value).toBe('');
  expect(field(two.data.config,'SITE_URL').value).toBe('http://localhost');
  const other=catalog.getProvision('alice',second).runtime,own=catalog.getProvision('alice',first).runtime;
  expect(two.data.config.callback_url).toContain(other);expect(two.data.config.callback_url).not.toContain(own);
  const text=JSON.stringify(one)+JSON.stringify(two);
  expect(text).not.toContain('ALPHA-SECRET-VALUE');
  expect(JSON.stringify(two)).not.toContain('Iv1.alpha');
 } finally {catalog.close();}
});

test('a disabled provider is shown as saved but not used, and a file without providers still reads',()=>{
 const off={revision:1,site_url:'https://a.example.com',redirect_urls:[],signup:true,anonymous:false,
  session:{access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null},
  providers:{google:{enabled:false,client_id:'g-id',secret:'G-SECRET',url:''}}} as never;
 const view=authConfigView(off,'e_'+'d'.repeat(24),'http://localhost');
 expect(field(view,'EXTERNAL_GOOGLE_ENABLED')).toMatchObject({value:false});
 expect(field(view,'EXTERNAL_GOOGLE_ENABLED').inactive).toBeUndefined();
 expect(field(view,'EXTERNAL_GOOGLE_CLIENT_ID')).toMatchObject({value:'g-id',inactive:true});
 expect(field(view,'EXTERNAL_GOOGLE_SECRET')).toMatchObject({secret_set:true,inactive:true});
 expect(JSON.stringify(view)).not.toContain('G-SECRET');
 const bare=authConfigView({revision:1,site_url:'https://a.example.com',redirect_urls:[],signup:true,anonymous:false} as never,'e_'+'d'.repeat(24),'http://localhost');
 expect(field(bare,'EXTERNAL_GITHUB_ENABLED').value).toBe(false);
});

test('the simple settings appear on their pages with hints, units and bounds, and a value saved is shown',()=>{
 const settings={revision:1,site_url:'https://a.example.com',redirect_urls:[],signup:true,anonymous:false,manual_linking:false,
  session:{access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null},config:{MFA_MAX_ENROLLED_FACTORS:25,SESSIONS_SINGLE_PER_USER:true},providers:{}} as never;
 const view=authConfigView(settings,'e_'+'e'.repeat(24),'http://localhost');
 expect(view.pages.map(page=>page.id)).toEqual(['providers','url-configuration','sessions','protection','mfa','hooks','sso','audit']);
 expect(field(view,'MFA_MAX_ENROLLED_FACTORS')).toMatchObject({value:25,from_default:false,store:'config',min:1});
 expect(field(view,'SESSIONS_SINGLE_PER_USER')).toMatchObject({value:true,store:'config'});
 expect(field(view,'SESSIONS_SINGLE_PER_USER').from_default).toBe(false);
 expect(view.pages.flatMap(page=>page.fields).some(entry=>entry.key==='RATE_LIMIT_WEB3')).toBe(false);
 expect(field(view,'PASSWORD_MIN_LENGTH').hint).toContain('rejected');
});
