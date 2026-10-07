import {expect,test,spyOn} from 'bun:test';
import {mkdtempSync,rmSync,readdirSync,readFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {DEFAULT_SETTINGS} from '../src/control/updates';
import {managementToken,sessionId,factorId,verifiedFactor,otherFactorId} from './management-fixture';

/** Real application and Catalog composition, with an explicitly synthetic native Auth transport.
 * The fixture signature and realm establish Source regressions only, never native acceptance. */
function fixture(){
 const previous=process.cwd(),directory=mkdtempSync(join(tmpdir(),'native-refresh-'));
 process.chdir(directory);
 const catalog=new Catalog(join(directory,'catalog.sqlite')),keys=new KeyStore(':memory:');
 const organization=catalog.initializeInstallation('bootstrap','owner','Installation');
 catalog.setMember('owner',organization,'member','admin');
 const project=catalog.createProject('owner',organization,'Project');
 const environment=catalog.createEnvironment('owner',project,'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const now=Math.floor(Date.now()/1000),token=managementToken('owner','aal2',sessionId,now),bearer='Bearer '+token;
 catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
 let factors=[verifiedFactor],status=200,nativeActor='owner';
 const lookups:{url:string;bearer:string|null}[]=[];
 const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const headers=new Headers(init?.headers);lookups.push({url:String(input),bearer:headers.get('authorization')});
  expect(String(input)).toBe('http://realm.invalid/user');expect(headers.get('authorization')).toBe(bearer);
  return status===200?Response.json({id:nativeActor,factors}):Response.json({msg:'Invalid session'},{status});
 },{preconnect(){}}) as typeof fetch;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport);
 const passwordPath=join(directory,'.secrets/upstream/database',job.runtime+'.json');
 const grantCurrent=()=>{
  expect(catalog.managementSecurity.epoch('owner')).toBe(0);
  expect(catalog.managementSecurity.granted('owner',sessionId,now,[factorId])).toBe(true);
 };
 const snapshot=()=>JSON.stringify({organizations:catalog.listOrganizations('owner'),members:catalog.listMembers('owner',organization),
  environments:catalog.listEnvironments('owner',project),invitations:catalog.listInvitations('owner',organization),
  database:catalog.databaseAccess('owner',environment),functions:catalog.functions('owner',environment),
  signIn:catalog.signIn('owner',environment),realtime:catalog.realtime('owner',environment)});
 const files=()=>{
  const values:Record<string,string>={};
  const visit=(relative:string)=>{for(const entry of readdirSync(join(directory,relative),{withFileTypes:true})){
   const path=join(relative,entry.name);if(entry.isDirectory())visit(path);
   else if(!path.startsWith('catalog.sqlite'))values[path]=readFileSync(join(directory,path)).toString('base64');
  }};visit('');return values;
 };
 return {catalog,keys,organization,project,environment,handler,passwordPath,lookups,bearer,now,grantCurrent,snapshot,files,
  removeFactor(){factors=[];},native401(){status=401;},changeActor(){nativeActor='other';},
  replaceFactor(){factors=[{...verifiedFactor,id:otherFactorId}];},unverifyFactor(){factors=[{...verifiedFactor,status:'unverified'}];},
  close(){catalog.close();keys.close();process.chdir(previous);rmSync(directory,{recursive:true,force:true});}};
}
type Fixture=ReturnType<typeof fixture>;
type Mutation={name:string;method:string;path:(f:Fixture)=>string;input:unknown};
const mutations:Mutation[]=[
 {name:'database password PUT',method:'PUT',path:f=>`environments/${f.environment}/database`,input:{enabled:true}},
 {name:'provisioning POST',method:'POST',path:f=>`projects/${f.project}/environments`,input:{name:'second'}},
 {name:'organization rename',method:'PATCH',path:f=>`organizations/${f.organization}`,input:{name:'Changed'}},
 {name:'membership PUT',method:'PUT',path:f=>`organizations/${f.organization}/members/member`,input:{role:'viewer'}},
 {name:'sign-in settings',method:'PUT',path:f=>`environments/${f.environment}/sign-in`,input:{site_url:'https://after.example'}},
 {name:'function deployment',method:'PUT',path:f=>`environments/${f.environment}/functions/example`,input:{files:{'index.ts':'export const value=1;'}}},
 {name:'function secrets',method:'PUT',path:f=>`environments/${f.environment}/function-secrets`,input:{secrets:{DUMMY:'after'}}},
 {name:'functions enabled',method:'PUT',path:f=>`environments/${f.environment}/functions`,input:{enabled:true}},
 {name:'realtime enabled',method:'PUT',path:f=>`environments/${f.environment}/realtime`,input:{enabled:true}},
 {name:'gateway share',method:'PUT',path:f=>`environments/${f.environment}/share`,input:{share:1}},
 {name:'invitation POST',method:'POST',path:f=>`organizations/${f.organization}/invitations`,input:{email:'member@example.test',role:'viewer'}},
 {name:'updates settings',method:'PUT',path:()=>`updates/settings`,input:DEFAULT_SETTINGS},
 {name:'deployment preparation',method:'PUT',path:f=>`environments/${f.environment}/deployment-setup`,input:{version:1,
  profile:'public',public_url:'https://api.example.org',ipv4:['8.8.8.8'],ipv6:[],provider_access:true,tls:'managed',
  acme_email:'operator@example.org',certificate_ref:null,site_url:'https://app.example.org',
  redirect_urls:['https://app.example.org/return'],smtp_ref:'environment-mail'}},
];
async function delayed(f:Fixture,mutation:Mutation,change:(incoming:Request)=>void){
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(value){controller=value;}});
 const incoming=new Request('http://local/management/v1/'+mutation.path(f),{method:mutation.method,
  headers:{authorization:f.bearer,'content-type':'application/json','if-match':'0'},body,duplex:'half'} as RequestInit);
 const response=f.handler(incoming);
 for(let turn=0;turn<200&&!incoming.body!.locked;turn++)await Promise.resolve();
 expect(incoming.body!.locked).toBe(true);
 // Permit the original single lookup when this same regression is composed onto the frozen base.
 // The refusal and unchanged-effect assertions must discriminate the defect before lookup counts do.
 expect(f.lookups.length).toBeGreaterThanOrEqual(1);expect(f.lookups.length).toBeLessThanOrEqual(2);
 change(incoming);controller.enqueue(new TextEncoder().encode(JSON.stringify(mutation.input)));controller.close();
 return response;
}
for(const mutation of mutations)for(const revocation of ['native factor removal','native 401'] as const)
 test(`${mutation.name} refuses ${revocation} after body await with unchanged durable grant`,async()=>{
  const f=fixture();try{
   const before=f.snapshot(),files=f.files();
   const response=await delayed(f,mutation,()=>revocation==='native factor removal'?f.removeFactor():f.native401());
   expect(response.status).toBe(403);expect((await response.json()).code).toBe('mfa_required');
   f.grantCurrent();expect(f.snapshot()).toBe(before);expect(f.files()).toEqual(files);
   expect(existsSync(f.passwordPath)).toBe(false);expect(f.lookups).toHaveLength(3);
  }finally{f.close();}
 });
for(const mutation of mutations.slice(0,2))test(`${mutation.name} continues with the original current native session after body await`,async()=>{
 const f=fixture();try{
  const response=await delayed(f,mutation,()=>{});expect(response.status).toBe(202);
  const published=await response.json(),data=published.data;f.grantCurrent();expect(f.lookups).toHaveLength(4);
  if(mutation.method==='PUT'){
   expect(data.password).toMatch(/^[A-Za-z0-9_-]{32}$/);
   expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(data.password);
   expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('on');
  }else{expect(f.catalog.listEnvironments('owner',f.project)).toHaveLength(2);expect(published.id).toBeTruthy();}
 }finally{f.close();}
});
for(const revocation of ['native factor removal','native 401'] as const)test(`database secret publication refuses ${revocation} during response await`,async()=>{
 const f=fixture(),json=Response.json.bind(Response);let secret:string|undefined,changed=false;
 const hook=spyOn(Response,'json').mockImplementation((data,init)=>{
  const response=json(data,init),password=(data as any)?.data?.password;
  if(typeof password==='string'&&!secret){secret=password;queueMicrotask(()=>{
   if(revocation==='native factor removal')f.removeFactor();else f.native401();changed=true;
  });}return response;
 });
 try{
  const mutation=mutations[0]!,incoming=new Request('http://local/management/v1/'+mutation.path(f),{method:'PUT',
   headers:{authorization:f.bearer,'content-type':'application/json'},body:JSON.stringify(mutation.input)});
  const response=await f.handler(incoming);expect(changed).toBe(true);expect(secret).toBeTruthy();
  expect(response.status).toBe(403);expect(await response.text()).not.toContain(secret!);f.grantCurrent();
  // This mutation was authorized before the native change; final publication must withhold its secret.
  expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(secret);expect(f.lookups).toHaveLength(4);
 }finally{hook.mockRestore();f.close();}
});
for(const change of ['actor','replacement factor','unverified factor','bearer session'] as const)
 test(`database mutation preserves original ${change} binding after body await`,async()=>{
  const f=fixture();try{
   const before=f.snapshot(),files=f.files();
   const response=await delayed(f,mutations[0]!,incoming=>{
    if(change==='actor')f.changeActor();else if(change==='replacement factor')f.replaceFactor();
    else if(change==='unverified factor')f.unverifyFactor();
    else incoming.headers.set('authorization','Bearer '+managementToken('owner','aal2',otherFactorId,f.now));
   });
   expect(response.status).toBe(403);f.grantCurrent();expect(f.snapshot()).toBe(before);expect(f.files()).toEqual(files);
   expect(existsSync(f.passwordPath)).toBe(false);expect(f.lookups).toHaveLength(change==='bearer session'?2:3);
  }finally{f.close();}
 });
