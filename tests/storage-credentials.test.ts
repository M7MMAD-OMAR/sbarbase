import {expect,test} from 'bun:test';
import {createHash,createHmac,randomBytes} from 'node:crypto';
import {mkdtempSync,writeFileSync,chmodSync,symlinkSync,linkSync,rmSync,renameSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {storageCredentialIssuer,type StorageIssuerOptions} from '../src/control/storage-credentials';
import {readStorageCredentialPolicies,validateStorageCredentialPolicies,type StorageCredentialPolicy} from '../src/control/storage-credentials-private';
import {createGateway} from '../src/gateway/handler';
import {application} from '../src/control/application';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
const ENV='e_0123456789abcdef01234567',OTHER='e_aaaaaaaaaaaaaaaaaaaaaaaa',NOW=1800000000;
const ROLE='fanio_0123456789abcdef01234567_original_reader';
const bootstrap=()=> 'sb_storage_refresh_'+randomBytes(32).toString('base64url');
const hash=(value:string)=>createHash('sha256').update(value).digest('hex');
const secret='isolated-runtime-signing-secret-not-a-real-credential';
function setup() {
 const key=bootstrap();
 const record:StorageCredentialPolicy={environment:ENV,purpose:'originals',role:ROLE,generation:'g1',secretSha256:hash(key),enabled:true,notBefore:NOW-60,expiresAt:NOW+86400};
 let records=[record],epoch:number|null=1,signing=secret,time=NOW,rate=true,policyReads=0,keyReads=0;
 const options:StorageIssuerOptions={policies:()=>{policyReads++;return validateStorageCredentialPolicies({version:1,credentials:records});},epoch:()=>epoch,secret:()=>{keyReads++;return signing;},rate:()=>rate,host:()=> 'issuer.fixture',now:()=>time};
 const issue=storageCredentialIssuer(options);
 const request=(changes:{key?:string;environment?:string;purpose?:string;url?:string;method?:string;headers?:Record<string,string>;body?:string}={})=>new Request(changes.url??`https://issuer.fixture/${changes.environment??ENV}/storage-credentials/v1/${changes.purpose??'originals'}`,{
  method:changes.method??'POST',headers:{authorization:'Bearer '+(changes.key??key),...changes.headers},...(changes.body===undefined?{}:{body:changes.body}),
 });
 return {issue,request,key,record,options,setRecords:(value:StorageCredentialPolicy[])=>{records=value;},setEpoch:(value:number|null)=>{epoch=value;},setSigning:(value:string)=>{signing=value;},setTime:(value:number)=>{time=value;},setRate:(value:boolean)=>{rate=value;},reads:()=>({policyReads,keyReads})};
}
test('issuer returns only exact role audience with 1-hour expiry and 30-minute refresh',async()=>{
 const f=setup(),response=await f.issue(f.request()),body=await response.json();
 expect(response.status).toBe(200);expect(response.headers.get('cache-control')).toBe('private, no-store');
 expect(Object.keys(body).sort()).toEqual(['accessToken','environment','expiresAt','generation','purpose','refreshAfter']);
 const [header,payload,signature]=body.accessToken.split('.');
 expect(signature).toBe(createHmac('sha256',secret).update(header+'.'+payload).digest('base64url'));
 const claims=JSON.parse(Buffer.from(payload,'base64url').toString());
 expect(claims).toMatchObject({role:ROLE,aud:'sbarbase:storage',exp:NOW+3600,iat:NOW,storage_credential_generation:'g1'});
 expect(body).toMatchObject({environment:ENV,purpose:'originals',generation:'g1',expiresAt:NOW+3600,refreshAfter:NOW+1800});
 expect(JSON.stringify(body)).not.toContain(secret);expect(JSON.stringify(body)).not.toContain(f.key);
});
test('only exact environment and purpose can use the hashed bootstrap key',async()=>{
 const f=setup();
 for(const changes of [{key:bootstrap()},{environment:OTHER},{purpose:'evidence'}])expect((await f.issue(f.request(changes))).status).toBe(401);
 expect(f.reads().keyReads).toBe(0);
});
test('method, host, query, browser and body injection never reach signing custody',async()=>{
 const f=setup();
 const attempts:Parameters<typeof f.request>[0][]=[{method:'GET'},{url:`https://other.fixture/${ENV}/storage-credentials/v1/originals`},{url:`https://issuer.fixture/${ENV}/storage-credentials/v1/originals?role=service_role`},{headers:{host:'other.fixture'}},{headers:{origin:'https://browser.fixture'}},{headers:{'sec-fetch-site':'same-origin'}},{body:'{"role":"service_role","ttl":31536000,"environment":"other"}'}];
 for(const changes of attempts){
  expect([400,405]).toContain((await f.issue(f.request(changes))).status);
 }
 expect(f.reads().keyReads).toBe(0);
});
test('global and credential rate refusal precede signing',async()=>{
 const f=setup();f.setRate(false);
 expect((await f.issue(f.request())).status).toBe(429);expect(f.reads().policyReads).toBe(1);
 let admissions=0;f.options.rate=()=>++admissions===1;
 expect((await f.issue(f.request())).status).toBe(429);expect(f.reads().keyReads).toBe(0);
});
test('rotation overlap works, revoked and expired bootstrap replay cannot mint',async()=>{
 const f=setup(),nextKey=bootstrap(),next={...f.record,secretSha256:hash(nextKey),generation:'g2'};
 f.setRecords([f.record,next]);
 expect((await f.issue(f.request())).status).toBe(200);expect((await f.issue(f.request({key:nextKey}))).status).toBe(200);
 f.setRecords([next]);expect((await f.issue(f.request())).status).toBe(401);
 f.setTime(next.expiresAt);expect((await f.issue(f.request({key:nextKey}))).status).toBe(401);
 f.setTime(NOW);f.setRecords([{...next,notBefore:NOW+1}]);expect((await f.issue(f.request({key:nextKey}))).status).toBe(401);
});
test('intended authenticated refresh never returns a cached JWT and does not widen its role',async()=>{
 const f=setup(),one=await (await f.issue(f.request())).json(),two=await (await f.issue(f.request())).json();
 expect(one.accessToken).not.toBe(two.accessToken);expect(one.expiresAt).toBe(two.expiresAt);
});
test('runtime absence, pause or epoch change prevents credential publication',async()=>{
 const f=setup();f.setEpoch(null);expect((await f.issue(f.request())).status).toBe(503);expect(f.reads().keyReads).toBe(0);
 let calls=0;f.options.epoch=()=>++calls;
 expect((await f.issue(f.request())).status).toBe(503);
});
test('policy revocation and signing-key rotation observed before publication fail closed',async()=>{
 const f=setup();let reads=0;
 f.options.policies=()=>validateStorageCredentialPolicies({version:1,credentials:++reads===1?[f.record]:[]});
 expect((await f.issue(f.request())).status).toBe(503);
 f.options.policies=()=>validateStorageCredentialPolicies({version:1,credentials:[f.record]});let keys=0;
 f.options.secret=()=>++keys===1?secret:'rotated-signing-secret-never-published';
 expect((await f.issue(f.request())).status).toBe(503);
});
test('issuer misconfiguration cannot mint browser, service or privileged PostgreSQL roles',()=>{
 const f=setup();
 for(const role of ['anon','authenticated','service_role','postgres','authenticator','pg_read_all_data','supabase_admin'])expect(()=>validateStorageCredentialPolicies({version:1,credentials:[{...f.record,role}]})).toThrow();
 expect(()=>validateStorageCredentialPolicies({version:1,credentials:[f.record,f.record]})).toThrow();
});
test('private policy loader refuses unsafe mode, symlink, hardlink, oversized and malformed files',()=>{
 const f=setup(),root=mkdtempSync(join(tmpdir(),'storage-policy-')),path=join(root,'policy.json');
 try {
  const content=JSON.stringify({version:1,credentials:[f.record]});writeFileSync(path,content,{mode:0o600});
  expect(readStorageCredentialPolicies(path).credentials[0]!.generation).toBe('g1');
  chmodSync(path,0o644);expect(()=>readStorageCredentialPolicies(path)).toThrow();chmodSync(path,0o600);
  symlinkSync(path,join(root,'symbolic'));expect(()=>readStorageCredentialPolicies(join(root,'symbolic'))).toThrow();
  linkSync(path,join(root,'hard'));expect(()=>readStorageCredentialPolicies(path)).toThrow();rmSync(join(root,'hard'));
  writeFileSync(path,'x'.repeat(65537));expect(()=>readStorageCredentialPolicies(path)).toThrow();
  writeFileSync(path,'{');expect(()=>readStorageCredentialPolicies(path)).toThrow();
 } finally {rmSync(root,{recursive:true,force:true});}
});
test('private policy loader sees atomic rotation immediately and has no stale TTL cache',()=>{
 const f=setup(),root=mkdtempSync(join(tmpdir(),'storage-policy-')),path=join(root,'policy.json');
 try {
  writeFileSync(path,JSON.stringify({version:1,credentials:[f.record]}),{mode:0o600});
  expect(readStorageCredentialPolicies(path).credentials).toHaveLength(1);
  writeFileSync(path+'.new',JSON.stringify({version:1,credentials:[]}),{mode:0o600});renameSync(path+'.new',path);
  expect(readStorageCredentialPolicies(path).credentials).toHaveLength(0);
 } finally {rmSync(root,{recursive:true,force:true});}
});
test('actual application injects issuer narrowly and keeps it absent for unchanged callers',async()=>{
 const f=setup(),catalog=new Catalog(':memory:'),keys=new KeyStore(':memory:');let upstream=0;
 const args=[catalog,keys,{auth:'http://realm.fixture',anonymousToken:'anon',publishableKey:'public'},()=>undefined,(async()=>{upstream++;return new Response('unexpected');}) as unknown as typeof fetch] as const;
 try {
  expect((await application(...args,undefined,undefined,undefined,undefined,undefined,undefined,{},f.issue)(f.request())).status).toBe(200);
  expect((await application(...args)(f.request())).status).toBe(404);expect(upstream).toBe(0);
 } finally {catalog.close();keys.close();}
});
test('issued token works through actual Storage gateway and cannot call REST or Auth',async()=>{
 const f=setup(),issued=await (await f.issue(f.request())).json();let calls=0;
 const gateway=createGateway(new Map([[ENV,{auth:'http://auth.fixture',rest:'http://rest.fixture',storage:{url:'http://storage.fixture',tenantHost:ENV+'.storage.internal'},keys:['publishable'],anonymousToken:'anon',enabled:true}]]),(async(_url,init)=>{
  calls++;const token=new Headers(init?.headers).get('authorization')!.slice(7),parts=token.split('.');
  expect(parts[2]).toBe(createHmac('sha256',secret).update(parts[0]+'.'+parts[1]).digest('base64url'));
  return new Response('verified native boundary');
 }) as typeof fetch);
 const request=(path:string)=>new Request('https://issuer.fixture/'+ENV+'/'+path,{headers:{apikey:'publishable',authorization:'Bearer '+issued.accessToken}});
 expect((await gateway(request('storage/v1/object/authenticated/private/object'))).status).toBe(200);
 for(const path of ['auth/v1/user','rest/v1/private_table','functions/v1/webhook'])expect((await gateway(request(path))).status).toBe(403);
 expect(calls).toBe(1);
});

test('invalid public requests cannot spend authenticated issuer budgets',async()=>{
 const f=setup();let spent=0;
 f.options.rate=()=>{spent++;return true;};
 for(let i=0;i<1201;i++)expect((await f.issue(new Request(`https://issuer.fixture/${ENV}/storage-credentials/v1/originals`,{method:'POST'}))).status).toBe(401);
 expect((await f.issue(f.request({key:bootstrap()}))).status).toBe(401);expect(spent).toBe(0);
 expect((await f.issue(f.request())).status).toBe(200);expect(spent).toBe(2);
});
test('cold-isolate refresh budget admits100 concurrent clients with frozen per-minute limits',async()=>{
 const f=setup(),counts=new Map<string,number>();
 f.options.rate=(bucket,limit,window)=>{
  expect(window).toBe(60000);expect(limit).toBe(bucket==='storage-issuer:global'?1200:600);
  const count=(counts.get(bucket)??0)+1;counts.set(bucket,count);return count<=limit;
 };
 const results=await Promise.all(Array.from({length:100},()=>f.issue(f.request())));
 expect(results.every(r=>r.status===200)).toBe(true);
 for(let i=100;i<600;i++)expect((await f.issue(f.request())).status).toBe(200);
 expect((await f.issue(f.request())).status).toBe(429);
});

test('issuer policy binds both supported purposes to exact environment runtime roles',()=>{
 const f=setup();
 for(const record of [{...f.record,purpose:'admin'},{...f.record,role:ENV+'_storage'},{...f.record,role:'unrelated_private_reader'},{...f.record,role:'fanio_aaaaaaaaaaaaaaaaaaaaaaaa_original_reader'},{...f.record,purpose:'evidence'}])expect(()=>validateStorageCredentialPolicies({version:1,credentials:[record]})).toThrow();
 expect(validateStorageCredentialPolicies({version:1,credentials:[{...f.record,purpose:'evidence',role:'fanio_0123456789abcdef01234567_evidence_runtime'}]}).credentials[0]!.purpose).toBe('evidence');
});

test('bootstrap hashes cannot be shared across purposes or environments',()=>{
 const f=setup();
 for(const other of [{...f.record,purpose:'evidence',role:'fanio_0123456789abcdef01234567_evidence_runtime'},{...f.record,environment:OTHER,role:'fanio_aaaaaaaaaaaaaaaaaaaaaaaa_original_reader'}])expect(()=>validateStorageCredentialPolicies({version:1,credentials:[f.record,other]})).toThrow();
 const key=bootstrap();expect(validateStorageCredentialPolicies({version:1,credentials:[f.record,{...f.record,generation:'g2',secretSha256:hash(key)}]}).credentials).toHaveLength(2);
});
