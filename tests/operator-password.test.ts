import {test,expect,afterAll} from 'bun:test';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {resetOperatorPassword,realm,ResetRefusal} from '../lab/operator-password';

// A local stand-in for the management realm's admin API: nothing leaves the machine.
const ID='11111111-2222-3333-4444-555555555555';
const seen:{method:string;path:string;auth:string|null;body:any}[]=[];
const auth=Bun.serve({port:0,async fetch(request){
 const url=new URL(request.url),body=request.method==='PUT'?await request.json():undefined;
 seen.push({method:request.method,path:url.pathname+url.search,auth:request.headers.get('authorization'),body});
 if(url.pathname==='/admin/users')return Response.json({users:[{id:'99999999-2222-3333-4444-555555555555',email:'other@example.com'},{id:ID,email:'Owner@Example.com'}]});
 if(url.pathname===`/admin/users/${ID}`&&request.method==='PUT')return body.password==='weakweakweak'?new Response('{}',{status:422}):Response.json({id:ID});
 return new Response('no',{status:404});
}});
afterAll(()=>auth.stop(true));
const target={auth:`http://127.0.0.1:${auth.port}`,secret:'s'.repeat(64)};

test('the account is found by email, its password set, its sessions ended and the change audited',async()=>{
 const catalog=new Catalog(':memory:');
 try{
  const before=(catalog as any).db.query("SELECT epoch FROM management_mfa_epoch WHERE actor=?").get(ID);
  expect(await resetOperatorPassword(target,'owner@example.com','a new long password',catalog)).toBe(ID);
  const put=seen.find(item=>item.method==='PUT')!;
  expect(put.path).toBe(`/admin/users/${ID}`);expect(put.body).toEqual({password:'a new long password'});
  expect(put.auth).toMatch(/^Bearer [\w-]+\.[\w-]+\.[\w-]+$/);
  const after=(catalog as any).db.query("SELECT epoch FROM management_mfa_epoch WHERE actor=?").get(ID);
  expect(after.epoch).toBeGreaterThan(before?.epoch??0);
  const audit=(catalog as any).db.query("SELECT actor,action,subject,detail FROM audit_events WHERE action='management.password_reset'").all();
  expect(audit).toEqual([{actor:'system:host',action:'management.password_reset',subject:ID,detail:'{}'}]);
  expect(JSON.stringify(audit)).not.toContain('a new long password');
 }finally{catalog.close();}
});

test('a short password, an unknown email and a refused password change nothing',async()=>{
 const catalog=new Catalog(':memory:');
 try{
  seen.length=0;
  await expect(resetOperatorPassword(target,'owner@example.com','short',catalog)).rejects.toBeInstanceOf(ResetRefusal);
  expect(seen).toEqual([]);
  await expect(resetOperatorPassword(target,'nobody@example.com','a new long password',catalog)).rejects.toThrow('No management account');
  await expect(resetOperatorPassword(target,'owner@example.com','weakweakweak',catalog)).rejects.toThrow('refused this password');
  expect((catalog as any).db.query("SELECT count(*) n FROM audit_events WHERE action='management.password_reset'").get().n).toBe(0);
 }finally{catalog.close();}
});

test('the realm comes from the installation files and refuses a malformed one',()=>{
 const root=mkdtempSync(join(tmpdir(),'sbar-reset-'));
 mkdirSync(join(root,'.lab/upstream'),{recursive:true});mkdirSync(join(root,'.secrets/upstream'),{recursive:true});
 writeFileSync(join(root,'.lab/upstream/management.json'),JSON.stringify({auth:'http://172.16.1.3:9999'}));
 writeFileSync(join(root,'.secrets/upstream/runtime.json'),JSON.stringify({management:{auth:'x',jwt:'j'.repeat(64)}}));
 expect(realm(root)).toEqual({auth:'http://172.16.1.3:9999',secret:'j'.repeat(64)});
 writeFileSync(join(root,'.lab/upstream/management.json'),JSON.stringify({auth:'http://user@evil/path'}));
 expect(()=>realm(root)).toThrow('not configured');
});
