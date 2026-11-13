import {test,expect} from 'bun:test';
import {AccessTokenStore} from '../src/control/access-tokens';

test('new tokens default to explicit read capabilities and never implicitly grant SQL writes',()=>{
 const store=new AccessTokenStore(':memory:');
 try{
  const issued=store.issue('owner','0.0',{name:'restricted',scope:{kind:'all'},read_only:false,days:7});
  expect(issued.data).toHaveProperty('permissions');
  expect((issued.data as any).permissions).toContain('projects.read');
  expect((issued.data as any).permissions).not.toContain('database.write');
 }finally{store.close();}
});

import {AGENT_PERMISSIONS,READ_PERMISSIONS,SAFE_ADMIN_PERMISSIONS,hasAgentPermission} from '../src/control/agent-permissions';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';

test('unknown, repeated and read-only write capabilities are refused before issuing a token',()=>{
 const store=new AccessTokenStore(':memory:');
 try{
  const input={name:'restricted',scope:{kind:'all'} as const,read_only:false,days:7};
  for(const permissions of [['everything'],['projects.read','projects.read'],'projects.read',null])
   expect(()=>store.issue('owner','0.0',{...input,permissions})).toThrow('permissions');
  expect(()=>store.issue('owner','0.0',{...input,read_only:true,permissions:['auth.write']})).toThrow('read-only');
  const issued=store.issue('owner','0.0',{...input,permissions:[]});
  expect(issued.data.permissions).toEqual([]);
  expect(store.resolve(issued.token,()=>'0.0')?.permissions).toEqual([]);
 }finally{store.close();}
});

test('safe administration excludes arbitrary SQL, executable deployments, deletion and revocation',()=>{
 expect(SAFE_ADMIN_PERMISSIONS).toContain('auth.write');
 expect(SAFE_ADMIN_PERMISSIONS).toContain('database.configure');
 for(const key of ['database.write','functions.write','functions.delete','keys.delete','auth.delete'])expect(SAFE_ADMIN_PERMISSIONS).not.toContain(key);
 expect(hasAgentPermission({read_only:true,permissions:AGENT_PERMISSIONS},'database.write')).toBe(false);
 expect(hasAgentPermission({read_only:false},'auth.read')).toBe(false);
});

test('existing schema 0 tokens migrate to explicit reads without reviving revoked or expired tokens',()=>{
 const dir=mkdtempSync(join(tmpdir(),'agent-grants-migration-')),path=join(dir,'tokens.sqlite');
 const token='sbp_'+'a'.repeat(40),digest=createHash('sha256').update(token).digest('hex');
 const db=new Database(path);
 db.exec(`CREATE TABLE access_tokens(id TEXT PRIMARY KEY,name TEXT NOT NULL,actor TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,
 scope_kind TEXT NOT NULL CHECK(scope_kind IN ('all','organization','project')),scope_id TEXT,
 read_only INTEGER NOT NULL CHECK(read_only IN (0,1)),epoch TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
 last_used_at INTEGER,revoked_at INTEGER,CHECK((scope_kind='all')=(scope_id IS NULL)));
 CREATE INDEX access_tokens_actor ON access_tokens(actor);`);
 db.query(`INSERT INTO access_tokens VALUES ('old','old','owner',?,'all',NULL,0,'0.0',1,9999999999999,NULL,NULL)`).run(digest);
 db.query(`INSERT INTO access_tokens VALUES ('expired','expired','owner',?,'all',NULL,0,'0.0',1,2,NULL,NULL)`).run(createHash('sha256').update('sbp_'+'b'.repeat(40)).digest('hex'));
 db.query(`INSERT INTO access_tokens VALUES ('revoked','revoked','owner',?,'all',NULL,0,'0.0',1,9999999999999,NULL,2)`).run(createHash('sha256').update('sbp_'+'c'.repeat(40)).digest('hex'));db.close();
 const store=new AccessTokenStore(path,()=>1000);
 try{
  expect(store.resolve(token,()=>'0.0')?.permissions).toEqual(READ_PERMISSIONS);
  expect(store.resolve('sbp_'+'b'.repeat(40),()=>'0.0')).toBeNull();
  expect(store.resolve('sbp_'+'c'.repeat(40),()=>'0.0')).toBeNull();
  expect(store.resolve(token,()=>'0.1')).toBeNull();
  expect(store.revoke('owner','old')).toBe(true);
  expect(store.resolve(token,()=>'0.0')).toBeNull();
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});
