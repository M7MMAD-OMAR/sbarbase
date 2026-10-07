import {expect,test} from 'bun:test';
import {existsSync,mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {managementToken,sessionId,factorId,otherFactorId,challengeId,verifiedFactor} from './management-fixture';

/** Source-only transport: native verification deliberately returns the same immutable fixture JWT.
 * This exercises the actual Auth producer's permitted state transition. It does not establish
 * whether the retained native Auth emits that result, or removes a factor outside this controller. */
function fixture(){
 const previous=process.cwd(),root=mkdtempSync(join(tmpdir(),'initial-factor-'));process.chdir(root);
 const catalog=new Catalog(join(root,'catalog.sqlite')),keys=new KeyStore(':memory:');
 const organization=catalog.initializeInstallation('bootstrap','owner','Installation');
 const project=catalog.createProject('owner',organization,'Project');
 const environment=catalog.createEnvironment('owner',project,'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const now=Math.floor(Date.now()/1000),token=managementToken('owner','aal2',sessionId,now),bearer='Bearer '+token;
 let factors=[verifiedFactor,{...verifiedFactor,id:otherFactorId}],users=0,verifications=0;
 catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
 const transport=Object.assign(async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const url=new URL(String(input));expect(url.origin).toBe('http://realm.invalid');
  expect(new Headers(init?.headers).get('authorization')).toBe(bearer);
  if(url.pathname==='/user'){users++;return Response.json({id:'owner',factors});}
  expect(url.pathname).toBe(`/factors/${otherFactorId}/verify`);expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({challenge_id:challengeId,code:'123456'});
  verifications++;return Response.json({access_token:token});
 },{preconnect(){}}) as typeof fetch;
 const handler=application(catalog,keys,{auth:'http://realm.invalid',anonymousToken:'anon',publishableKey:'public'},()=>undefined,transport);
 const granted=(factor:string)=>catalog.managementSecurity.granted('owner',sessionId,now,[factor]);
 const passwordPath=join(root,'.secrets/upstream/database',job.runtime+'.json');
 return {catalog,handler,environment,project,bearer,passwordPath,granted,
  get users(){return users;},get verifications(){return verifications;},
  removeOriginal(){factors=[{...verifiedFactor,id:otherFactorId}];},removeOther(){factors=[verifiedFactor];},
  async verifyOther(){
   const response=await handler(new Request(`http://local/management/auth/v1/factors/${otherFactorId}/verify`,{
    method:'POST',headers:{authorization:bearer,apikey:'public','content-type':'application/json'},
    body:JSON.stringify({challenge_id:challengeId,code:'123456'})}));
   expect(response.status).toBe(200);expect((await response.json()).access_token).toBe(token);
   expect(verifications).toBe(1);expect(granted(factorId)).toBe(false);expect(granted(otherFactorId)).toBe(true);
   expect(catalog.managementSecurity.epoch('owner')).toBe(0);
  },close(){catalog.close();keys.close();process.chdir(previous);rmSync(root,{recursive:true,force:true});}};
}
type Fixture=ReturnType<typeof fixture>;
const mutations=[
 {name:'database password',method:'PUT',path:(f:Fixture)=>`environments/${f.environment}/database`,input:{enabled:true}},
 {name:'provisioning',method:'POST',path:(f:Fixture)=>`projects/${f.project}/environments`,input:{name:'second'}},
] as const;
async function delayed(f:Fixture,mutation:typeof mutations[number]){
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(value){controller=value;}});
 const request=new Request('http://local/management/v1/'+mutation.path(f),{method:mutation.method,
  headers:{authorization:f.bearer,'content-type':'application/json'},body,duplex:'half'} as RequestInit);
 const response=f.handler(request);
 for(let turn=0;turn<200&&!request.body!.locked;turn++)await Promise.resolve();
 expect(request.body!.locked).toBe(true);expect(f.users).toBe(2);
 expect(f.granted(factorId)).toBe(true);expect(f.granted(otherFactorId)).toBe(false);
 return {response,finish(){controller.enqueue(new TextEncoder().encode(JSON.stringify(mutation.input)));controller.close();}};
}
for(const mutation of mutations)for(const removal of ['original removed','original retained'] as const)
 test(`${mutation.name} cannot inherit an initially ungranted factor after producer grant with ${removal}`,async()=>{
  const f=fixture();try{
   const pending=await delayed(f,mutation);
   // The actual managementAuth route replaces the single grant using native-checked immutable claims.
   await f.verifyOther();if(removal==='original removed')f.removeOriginal();pending.finish();
   const response=await pending.response;expect(response.status).toBe(403);
   expect((await response.json()).code).toBe('mfa_required');
   expect(f.granted(otherFactorId)).toBe(true);expect(f.catalog.managementSecurity.epoch('owner')).toBe(0);
   expect(existsSync(f.passwordPath)).toBe(false);expect(existsSync(f.passwordPath+'.pending')).toBe(false);
   expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('off');
   expect(f.catalog.listEnvironments('owner',f.project)).toHaveLength(1);
  }finally{f.close();}
 });
for(const mutation of mutations)test(`${mutation.name} retains its initially granted factor when only an ungranted factor disappears`,async()=>{
 const f=fixture();try{
  const pending=await delayed(f,mutation);f.removeOther();pending.finish();
  const response=await pending.response;expect(response.status).toBe(202);
  const data=await response.json();expect(f.granted(factorId)).toBe(true);expect(f.granted(otherFactorId)).toBe(false);
  expect(f.catalog.managementSecurity.epoch('owner')).toBe(0);expect(f.verifications).toBe(0);expect(f.users).toBe(4);
  if(mutation.method==='PUT'){
   expect(data.data.password).toMatch(/^[A-Za-z0-9_-]{32}$/);
   expect(JSON.parse(readFileSync(f.passwordPath,'utf8')).password).toBe(data.data.password);
  }else{expect(data.id).toBeTruthy();expect(f.catalog.listEnvironments('owner',f.project)).toHaveLength(2);}
 }finally{f.close();}
});
