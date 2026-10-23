import {test,expect,afterEach,spyOn} from 'bun:test';
import {Database} from 'bun:sqlite';
import {createHash} from 'node:crypto';
import {chmodSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {KeyStore,KEY_STORE_SCHEMA_VERSION} from '../src/control/keys';
import {AccessTokenStore,ACCESS_TOKEN_SCHEMA_VERSION} from '../src/control/access-tokens';
import {DeploymentSetupStore,DEPLOYMENT_SETUP_SCHEMA_VERSION} from '../src/control/deployment-setup';

const directories:string[]=[];
afterEach(()=>{for(const directory of directories.splice(0))rmSync(directory,{recursive:true,force:true});});
function directory(){const made=mkdtempSync(join(tmpdir(),'sbarbase-store-version-'));directories.push(made);return made;}
function version(path:string){const db=new Database(path,{readonly:true});try{return db.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version;}finally{db.close();}}
function columns(path:string,table:string){const db=new Database(path,{readonly:true});try{return db.query<{name:string},[]>(`PRAGMA table_info(${table})`).all().map(column=>column.name);}finally{db.close();}}
function seed(path:string,sql:string,userVersion=0){const db=new Database(path,{create:true});db.exec(sql);db.exec(`PRAGMA user_version=${userVersion}`);db.close();chmodSync(path,0o600);}

// The api_keys table as every release before the ladder created it, before its three added columns.
const ORIGINAL_KEYS=`CREATE TABLE api_keys(id TEXT PRIMARY KEY, environment TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('publishable','secret')), digest TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, revoked_at INTEGER);
 CREATE INDEX api_keys_environment ON api_keys(environment);`;
const CURRENT_KEYS=ORIGINAL_KEYS+`ALTER TABLE api_keys ADD COLUMN runtime_epoch INTEGER NOT NULL DEFAULT 0;
 ALTER TABLE api_keys ADD COLUMN name TEXT NOT NULL DEFAULT 'default';
 ALTER TABLE api_keys ADD COLUMN token TEXT CHECK(token IS NULL OR kind='publishable');`;

test('a fresh store of each kind stays at the baseline, version 0, that every earlier release opens',()=>{
 const dir=directory();
 new KeyStore(join(dir,'keys.sqlite')).close();
 new AccessTokenStore(join(dir,'tokens.sqlite')).close();
 new DeploymentSetupStore(dir).close();
 expect(version(join(dir,'keys.sqlite'))).toBe(KEY_STORE_SCHEMA_VERSION);
 expect(version(join(dir,'tokens.sqlite'))).toBe(ACCESS_TOKEN_SCHEMA_VERSION);
 expect(version(join(dir,'setup.sqlite'))).toBe(DEPLOYMENT_SETUP_SCHEMA_VERSION);
 // lab/upgrade.py reads a release without these constants as 0; a rollback to it needs the store at 0.
 expect([KEY_STORE_SCHEMA_VERSION,ACCESS_TOKEN_SCHEMA_VERSION,DEPLOYMENT_SETUP_SCHEMA_VERSION]).toEqual([0,0,0]);
});

test('a key store of the original shape gains its columns and keeps its keys, still at the baseline',()=>{
 const path=join(directory(),'keys.sqlite');
 const token='sb_publishable_original';
 seed(path,ORIGINAL_KEYS+`INSERT INTO api_keys(id,environment,kind,digest,created_at,revoked_at)
  VALUES ('k','a_prod','publishable','${createHash('sha256').update(token).digest('hex')}',1,NULL);`);
 expect(columns(path,'api_keys')).not.toContain('token');
 const store=new KeyStore(path);
 try {
  expect(store.resolve('a_prod',token)).toBe('publishable');
  expect(store.list('a_prod')[0]?.name).toBe('default');
 } finally {store.close();}
 expect(columns(path,'api_keys')).toEqual(expect.arrayContaining(['runtime_epoch','name','token']));
 expect(version(path)).toBe(KEY_STORE_SCHEMA_VERSION);
});

test('a store that already has the current shape opens again and again without a change',()=>{
 const dir=directory();
 seed(join(dir,'keys.sqlite'),CURRENT_KEYS+`INSERT INTO api_keys(id,environment,kind,digest,created_at,name) VALUES ('k','a_prod','secret','d',1,'named_key');`);
 const keys=new KeyStore(join(dir,'keys.sqlite'));
 expect(keys.list('a_prod').map(key=>key.name)).toEqual(['named_key']);keys.close();
 expect(version(join(dir,'keys.sqlite'))).toBe(KEY_STORE_SCHEMA_VERSION);
 new AccessTokenStore(join(dir,'tokens.sqlite')).close();new AccessTokenStore(join(dir,'tokens.sqlite')).close();
 expect(version(join(dir,'tokens.sqlite'))).toBe(ACCESS_TOKEN_SCHEMA_VERSION);
 new DeploymentSetupStore(dir).close();new DeploymentSetupStore(dir).close();
 expect(version(join(dir,'setup.sqlite'))).toBe(DEPLOYMENT_SETUP_SCHEMA_VERSION);
});

test('a store a future release stamped 1 is refused and left as it is',()=>{
 const dir=directory();
 seed(join(dir,'keys.sqlite'),'CREATE TABLE marker(x)',KEY_STORE_SCHEMA_VERSION+1);
 expect(()=>new KeyStore(join(dir,'keys.sqlite'))).toThrow('Key store schema is newer than this release');
 expect(columns(join(dir,'keys.sqlite'),'api_keys')).toEqual([]);
 expect(version(join(dir,'keys.sqlite'))).toBe(KEY_STORE_SCHEMA_VERSION+1);
 seed(join(dir,'tokens.sqlite'),'CREATE TABLE marker(x)',ACCESS_TOKEN_SCHEMA_VERSION+1);
 expect(()=>new AccessTokenStore(join(dir,'tokens.sqlite'))).toThrow('Access token store schema is newer than this release');
 expect(columns(join(dir,'tokens.sqlite'),'access_tokens')).toEqual([]);
 seed(join(dir,'setup.sqlite'),'CREATE TABLE marker(x)',DEPLOYMENT_SETUP_SCHEMA_VERSION+1);
 expect(()=>new DeploymentSetupStore(dir)).toThrow('Deployment setup store schema is newer than this release');
 expect(columns(join(dir,'setup.sqlite'),'setup')).toEqual([]);
});

test('opening a current store reads past another process holding the write lock',()=>{
 const dir=directory();
 new KeyStore(join(dir,'keys.sqlite')).close();new AccessTokenStore(join(dir,'tokens.sqlite')).close();new DeploymentSetupStore(dir).close();
 const writers=['keys.sqlite','tokens.sqlite','setup.sqlite'].map(name=>{
  const db=new Database(join(dir,name));db.exec('BEGIN IMMEDIATE');db.exec('CREATE TABLE pending(x)');return db;});
 try {
  const started=performance.now();
  new KeyStore(join(dir,'keys.sqlite')).close();
  new AccessTokenStore(join(dir,'tokens.sqlite')).close();
  new DeploymentSetupStore(dir).close();
  expect(performance.now()-started).toBeLessThan(1000);
 } finally {for(const db of writers){db.exec('ROLLBACK');db.close();}}
});

test('a refused open closes its database handle',()=>{
 const dir=directory();
 seed(join(dir,'keys.sqlite'),'CREATE TABLE marker(x)',1);
 seed(join(dir,'tokens.sqlite'),'CREATE TABLE marker(x)',1);
 seed(join(dir,'setup.sqlite'),'CREATE TABLE marker(x)',1);
 const close=spyOn(Database.prototype,'close');
 try {
  expect(()=>new KeyStore(join(dir,'keys.sqlite'))).toThrow('newer');
  expect(()=>new AccessTokenStore(join(dir,'tokens.sqlite'))).toThrow('newer');
  expect(()=>new DeploymentSetupStore(dir)).toThrow('newer');
  expect(close).toHaveBeenCalledTimes(3);
 } finally {close.mockRestore();}
});
