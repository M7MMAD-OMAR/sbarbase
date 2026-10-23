import {test,expect,afterEach} from 'bun:test';
import {Database} from 'bun:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog,CATALOG_SCHEMA_VERSION} from '../src/control/catalog';
import {KeyStore,KEY_STORE_SCHEMA_VERSION} from '../src/control/keys';
import {AccessTokenStore,ACCESS_TOKEN_SCHEMA_VERSION} from '../src/control/access-tokens';
import {DeploymentSetupStore,DEPLOYMENT_SETUP_SCHEMA_VERSION} from '../src/control/deployment-setup';

/** src/control/store-schemas.json records, per control store, the schema it gets when made fresh
 * and its version. The release check (lab/release_channel.py stores_changed) compares that file
 * between two versions, so it must say exactly what the code does: this test builds each store
 * through its real module and fails on any difference. The catalog file also holds the tables
 * ManagementSecurity and studio activity make, both created by the Catalog constructor. The
 * transfer inventory and history stores are not wired into the running console yet.
 *
 * Known limit (Y2): only a fresh store is fingerprinted, so a table a store creates lazily (on
 * first use, outside its constructor or migrate step) is invisible here and to the release check.
 * The rule: every DDL of a control store runs in its constructor or a migrate step. */
const RECORD='src/control/store-schemas.json';
const recorded=JSON.parse(readFileSync(join(import.meta.dir,'..',RECORD),'utf8')) as Record<string,{version:number;sha256:string}>;
const STORES:Record<string,{constant:string;version:number;open:(dir:string)=>string}>={
 catalog:{constant:'CATALOG_SCHEMA_VERSION in src/control/catalog.ts',version:CATALOG_SCHEMA_VERSION,
  open:dir=>{const path=join(dir,'control.sqlite');new Catalog(path).close();return path;}},
 keys:{constant:'KEY_STORE_SCHEMA_VERSION in src/control/keys.ts',version:KEY_STORE_SCHEMA_VERSION,
  open:dir=>{const path=join(dir,'keys.sqlite');new KeyStore(path).close();return path;}},
 access_tokens:{constant:'ACCESS_TOKEN_SCHEMA_VERSION in src/control/access-tokens.ts',version:ACCESS_TOKEN_SCHEMA_VERSION,
  open:dir=>{const path=join(dir,'tokens.sqlite');new AccessTokenStore(path).close();return path;}},
 deployment_setup:{constant:'DEPLOYMENT_SETUP_SCHEMA_VERSION in src/control/deployment-setup.ts',version:DEPLOYMENT_SETUP_SCHEMA_VERSION,
  open:dir=>{new DeploymentSetupStore(dir).close();return join(dir,'setup.sqlite');}},
};

const directories:string[]=[];
afterEach(()=>{for(const directory of directories.splice(0))rmSync(directory,{recursive:true,force:true});});

function read(path:string){
 const db=new Database(path,{readonly:true});
 try {
  const rows=db.query<{type:string;name:string;sql:string|null},[]>('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').all();
  return {sha256:createHash('sha256').update(JSON.stringify(rows)).digest('hex'),
   version:db.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version};
 } finally {db.close();}
}

test('the recorded control store schemas say exactly what the code creates',()=>{
 const problems:string[]=[];
 for(const store of Object.keys(recorded))
  if(!STORES[store])problems.push(`${RECORD} records "${store}", which this test does not build: remove it, or add the store here`);
 for(const [store,{constant,version,open}] of Object.entries(STORES)) {
  const record=recorded[store];
  const dir=mkdtempSync(join(tmpdir(),'sbarbase-store-schema-'));directories.push(dir);
  const fresh=read(open(dir));
  if(!record){problems.push(`${RECORD} has no "${store}": add "${store}": {"version": ${version}, "sha256": "${fresh.sha256}"}`);continue;}
  if(fresh.sha256!==record.sha256)
   problems.push(`The ${store} stored schema text changed (even whitespace counts). If the schema changed, bump ${constant}, `
    +`add the step to its ladder and set "${store}" in ${RECORD} to {"version": <new version>, "sha256": "${fresh.sha256}"}; `
    +`if only formatting changed, set its sha256 to "${fresh.sha256}"`);
  if(version!==record.version)
   problems.push(`${constant} is ${version}, but ${RECORD} records ${record.version} for "${store}": set its version to ${version}`);
  if(fresh.version!==record.version)
   problems.push(`A fresh ${store} store is at user_version ${fresh.version}, but ${RECORD} records ${record.version}: `
    +`its ladder must stamp ${constant}`);
 }
 expect(problems).toEqual([]);
});
