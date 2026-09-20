import {test,expect} from 'bun:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';

test('cross-organization authority, last owner and read-only membership boundaries',()=>{
 const c=new Catalog(':memory:');
 try {
  const a=c.createOrganization('alice','A'),b=c.createOrganization('bob','B');
  c.setMember('alice',a,'viewer','viewer');c.setMember('alice',a,'admin','admin');
  const p=c.createProject('admin',a,'P'),e=c.createEnvironment('admin',p,'production');
  expect(c.listEnvironments('viewer',p).map(x=>x.id)).toEqual([e]);
  expect(()=>c.createProject('viewer',a,'Denied')).toThrow('Forbidden');
  expect(()=>c.createEnvironment('viewer',p,'Denied')).toThrow('Forbidden');
  expect(()=>c.listProjects('bob',a)).toThrow('Forbidden');
  expect(()=>c.listEnvironments('bob',p)).toThrow('Forbidden');
  expect(()=>c.setMember('admin',a,'admin','owner')).toThrow('Forbidden');
  expect(()=>c.setMember('alice',a,'alice',null)).toThrow('Last owner');
  expect(()=>c.setMember('alice',a,'alice','admin')).toThrow('Last owner');
  expect(()=>c.transferProject('alice',p,b)).toThrow('Forbidden');
  expect(c.listProjects('alice',a).map(x=>x.id)).toEqual([p]);
  c.setMember('bob',b,'alice','admin');
  expect(()=>c.transferProject('alice',p,b)).toThrow('Forbidden');
  c.setMember('alice',a,'viewer',null);
  expect(()=>c.listEnvironments('viewer',p)).toThrow('Forbidden');
 } finally {c.close();}
});

test('ownership transfer preserves project/environment identity and persists access change',()=>{
 const dir=mkdtempSync(join(tmpdir(),'sbarbase-catalog-')),path=join(dir,'catalog.sqlite');
 let c=new Catalog(path);
 try {
  const a=c.createOrganization('alice','A'),b=c.createOrganization('bob','B');
  const p=c.createProject('alice',a,'P');
  const ids=[c.createEnvironment('alice',p,'production'),c.createEnvironment('alice',p,'staging')].sort();
  c.setMember('bob',b,'alice','owner');c.setMember('alice',a,'old-admin','admin');
  c.transferProject('alice',p,b);
  c.close();c=new Catalog(path);
  expect(c.listProjects('alice',a)).toEqual([]);
  expect(c.listProjects('bob',b).map(x=>x.id)).toEqual([p]);
  expect(c.listEnvironments('bob',p).map(x=>x.id).sort()).toEqual(ids);
  expect(()=>c.listEnvironments('old-admin',p)).toThrow('Forbidden');
  c.setMember('bob',b,'alice',null);
  expect(()=>c.listEnvironments('alice',p)).toThrow('Forbidden');
  expect(()=>c.createEnvironment('bob',p,'production')).toThrow();
  expect(c.listEnvironments('bob',p)).toHaveLength(2);
 } finally {c.close();rmSync(dir,{recursive:true});}
});
