import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync,readFileSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {bootstrapOperator,type BootstrapIdentity} from '../src/control/bootstrap';
import {bootstrapJournal} from '../lab/bootstrap-journal';
import {managementHandler} from '../src/control/http';

for(const phase of ['intent','identity-created','identity-recorded','catalog'])test('bootstrap resumes after '+phase+' without duplicate owner or organization',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-bootstrap-')),path=join(directory,'catalog.sqlite'),journalPath=join(directory,'intent.json');
 let catalog=new Catalog(path),identity:BootstrapIdentity|null=null,creates=0;
 const journal=bootstrapJournal(journalPath),input={email:'Owner@example.com',password:'test-only-long-password',organization:'Original'};
 const auth={async find(){return identity;},async create(_email:string,_password:string,operation:string){creates++;return identity={id:'owner',operation};},async verify(){return 'owner';}};
 try {
  await expect(bootstrapOperator(catalog,auth,journal,input,stage=>{if(stage===phase)throw new Error('Crash');})).rejects.toThrow('Crash');
  catalog.close();catalog=new Catalog(path);
  const result=await bootstrapOperator(catalog,auth,journal,input);
  expect(await bootstrapOperator(catalog,auth,journal,input)).toEqual(result);
  expect(creates).toBe(1);expect(catalog.listOrganizations('owner')).toHaveLength(1);
  expect(readFileSync(journalPath,'utf8')).not.toContain(input.password);expect(statSync(journalPath).mode&0o777).toBe(0o600);
  await expect(bootstrapOperator(catalog,auth,journal,{...input,email:'other@example.com'})).rejects.toThrow('intent');
  await expect(bootstrapOperator(catalog,auth,{read:()=>null,write(){}},input)).rejects.toThrow('already initialized');
  expect(creates).toBe(1);
 }finally{catalog.close();rmSync(directory,{recursive:true});}
});

test('bootstrap retry cannot restore ownership after membership revocation',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-bootstrap-')),catalog=new Catalog(':memory:');
 const journal=bootstrapJournal(join(directory,'intent.json'));let identity:BootstrapIdentity|null=null;
 const auth={async find(){return identity;},async create(_e:string,_p:string,operation:string){return identity={id:'owner',operation};},async verify(){return 'owner';}};
 const input={email:'owner@example.com',password:'test-only-long-password',organization:'O'};
 try {
  const result=await bootstrapOperator(catalog,auth,journal,input);
  catalog.setMember('owner',result.organization,'second','owner');catalog.setMember('second',result.organization,'owner',null);
  await expect(bootstrapOperator(catalog,auth,journal,input)).rejects.toThrow('Forbidden');
  expect(catalog.listOrganizations('owner')).toEqual([]);
 }finally{catalog.close();rmSync(directory,{recursive:true});}
});

test('existing Auth identity must verify credentials before creating catalog authority',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-bootstrap-')),catalog=new Catalog(':memory:');
 const journal=bootstrapJournal(join(directory,'intent.json'));let identity:BootstrapIdentity|null=null;
 const auth={async find(){return identity;},async create(_e:string,_p:string,operation:string){return identity={id:'owner',operation};},async verify(){return null;}};
 try {
  await expect(bootstrapOperator(catalog,auth,journal,{email:'owner@example.com',password:'test-only-long-password',organization:'O'})).rejects.toThrow('credentials');
  expect(catalog.installationBootstrap()).toBeNull();expect(catalog.listOrganizations('owner')).toEqual([]);
 }finally{catalog.close();rmSync(directory,{recursive:true});}
});

test('organization discovery is authenticated and reflects current memberships only',async()=>{
 const catalog=new Catalog(':memory:');
 try {
  const a=catalog.createOrganization('alice','A'),b=catalog.createOrganization('bob','B');catalog.setMember('alice',a,'bob','viewer');
  const handler=managementHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor?:string,method='GET')=>handler(new Request('http://local/management/v1/organizations',{method,headers:actor?{authorization:actor}:{}}));
  expect((await call()).status).toBe(401);expect((await call('alice','PUT')).status).toBe(405);
  // No bootstrap is recorded here, so nobody is an installation operator and POST is refused.
  expect((await call('alice','POST')).status).toBe(403);
  expect((await (await call('alice')).json()).data).toEqual([{id:a,name:'A',role:'owner'}]);
  expect((await (await call('outsider')).json()).data).toEqual([]);
  expect((await (await call('bob')).json()).data).toHaveLength(2);
  catalog.setMember('alice',a,'bob',null);
  expect((await (await call('bob')).json()).data).toEqual([{id:b,name:'B',role:'owner'}]);
 }finally{catalog.close();}
});
