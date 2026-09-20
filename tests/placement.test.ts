import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managedGateway} from '../src/gateway/managed';
import {validatePlacement} from '../src/control/placement';

function fixture(path:string){
 const catalog=new Catalog(path),keys=new KeyStore(':memory:');
 const org=catalog.createOrganization('owner','O'),project=catalog.createProject('owner',org,'P');
 const environment=catalog.createEnvironment('owner',project,'E'),job=catalog.claimProvision()!;
 catalog.finishProvision(environment,job.claim!,true);return {catalog,keys,runtime:job.runtime};
}
const target={auth:'http://target-auth',rest:'http://target-rest'};
test('maintenance and staged placement survive reopen, stale writers cannot unpause',()=>{
 const dir=mkdtempSync(join(tmpdir(),'sbar-routing-'));const path=join(dir,'control.sqlite');
 const f=fixture(path);const second=new Catalog(path);
 try{
  expect(f.catalog.changeRuntimeRouting(f.runtime,0,'pause')).toBe(1);
  expect(second.runtimeRouting(f.runtime).maintenance).toBe(true);
  expect(()=>second.changeRuntimeRouting(f.runtime,0,'resume')).toThrow('Stale');
  expect(second.changeRuntimeRouting(f.runtime,1,'stage',target)).toBe(2);
  f.catalog.close();const reopened=new Catalog(path);
  try{
   expect(reopened.runtimeRouting(f.runtime)).toEqual({revision:2,maintenance:true,placement:target});
   expect(()=>reopened.changeRuntimeRouting(f.runtime,1,'resume')).toThrow('Stale');
   expect(reopened.changeRuntimeRouting(f.runtime,2,'resume')).toBe(3);
   expect(second.runtimeRouting(f.runtime).maintenance).toBe(false);
  }finally{reopened.close();}
 }finally{second.close();f.keys.close();rmSync(dir,{recursive:true,force:true});}
});

test('gateway fails closed during durable maintenance and uses staged target after resume',async()=>{
 const {catalog,keys,runtime}=fixture(':memory:');const key=keys.issue(runtime);let calls:string[]=[];
 const configured=()=>({auth:'http://old-auth',rest:'http://old-rest',storage:{url:'http://old-storage',tenantHost:'old.storage'},keys:[],anonymousToken:'anon',enabled:true});
 const gateway=managedGateway(catalog,keys,configured,(async(input)=>{calls.push(String(input));return new Response(null,{status:204});}) as typeof fetch);
 const req=(service='rest')=>new Request(`http://local/${runtime}/${service}/v1/`,{headers:{apikey:key.token}});
 try{
  catalog.changeRuntimeRouting(runtime,0,'pause');expect((await gateway(req())).status).toBe(503);expect(calls).toEqual([]);
  catalog.changeRuntimeRouting(runtime,1,'stage',target);expect((await gateway(req())).status).toBe(503);
  catalog.changeRuntimeRouting(runtime,2,'resume');expect((await gateway(req())).status).toBe(204);
  expect(calls).toEqual(['http://target-rest/']);expect((await gateway(req('storage'))).status).toBe(404);
 }finally{catalog.close();keys.close();}
});

test('invalid transitions and credential-bearing placement never mutate routing',()=>{
 const {catalog,keys,runtime}=fixture(':memory:');
 try{
  expect(()=>catalog.changeRuntimeRouting(runtime,0,'stage',target)).toThrow();
  expect(()=>catalog.changeRuntimeRouting(runtime,0,'resume')).toThrow();
  catalog.changeRuntimeRouting(runtime,0,'pause');
  expect(()=>catalog.changeRuntimeRouting(runtime,1,'stage',{...target,auth:'http://user:secret@host'})).toThrow();
  expect(()=>catalog.changeRuntimeRouting(runtime,1,'pause')).toThrow();
  expect(catalog.runtimeRouting(runtime).revision).toBe(1);
  expect(()=>validatePlacement({...target,rest:'file:///etc/passwd'})).toThrow();
 }finally{catalog.close();keys.close();}
});
