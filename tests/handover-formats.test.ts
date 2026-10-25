import {afterEach,expect,test} from 'bun:test';
import {mkdirSync,mkdtempSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createGateway,type EnvironmentRoute} from '../src/gateway/handler';
import {functionsStorageWanted} from '../src/control/functions-storage';
import {studioState} from '../lab/upstream-app';
import {readJsonCached} from '../src/http/cached-json';

/** The Bun side of the format rule (docs/engineering/HANDOVER.md, src/control/handover-formats.json): the
 * documents the writers of lab/runtime_files.py produce, with and without keys the reader does not know,
 * are read by the readers both console processes use, with no fallback path. The fixtures are the shapes
 * lab/test_handover_formats.py has the writers produce byte for byte. */
const E='e_'+'a'.repeat(24);
const entry={auth:'http://172.30.0.3:9999',rest:'http://172.30.0.4:3000',
 storage:{url:'http://172.30.0.5:5000',tenantHost:E+'.storage.internal'},
 realtime:{url:'http://172.30.0.6:4000',migrated:'sha256:1'},
 functions:{storage:{host:'172.31.0.1',port:18080,subnet:'172.31.0.0/24'}},
 database:{user:E+'_developer',database:E},cron:{user:E+'_cron'},serviceConcurrency:{rest:8}};
const later={quota:{rows:5},zone:'b'};
const folders:string[]=[];
const previous=process.cwd();
afterEach(() => {process.chdir(previous);for(const folder of folders.splice(0))rmSync(folder,{recursive:true,force:true});});
function installation(files:Record<string,unknown>) {
 const root=mkdtempSync(join(tmpdir(),'handover-formats-'));folders.push(root);
 mkdirSync(join(root,'.lab/upstream'),{recursive:true});
 for(const [name,value] of Object.entries(files))writeFileSync(join(root,name),JSON.stringify(value));
 process.chdir(root);
 return root;
}

for(const [label,extra] of [['as the writers produce it',{}],['with keys of a later shape',later]] as const) {
 test(`the Studio state is read ${label}`,() => {
  installation({'.lab/upstream/studio.json':{sessions:{[E]:{url:'http://172.30.0.8:3000',...extra}},upstream:{host:'172.30.0.1',port:25432},...extra}});
  const state=studioState();
  expect(state.sessions[E]!.url).toBe('http://172.30.0.8:3000');
  expect(state.upstream).toEqual({host:'172.30.0.1',port:25432});
 });
 test(`the Functions Storage listeners are wanted from endpoints ${label}`,() => {
  const wanted=functionsStorageWanted({[E]:{...entry,...extra},[`e_${'b'.repeat(24)}`]:{auth:'a',rest:'r',...extra}});
  expect([...wanted.keys()]).toEqual([E]);
  expect(wanted.get(E)).toEqual({host:'172.31.0.1',port:18080,subnet:'172.31.0.0/24'});
 });
 test(`the gateway routes an environment whose entry is ${label}`,async() => {
  const calls:string[]=[];
  const route:EnvironmentRoute={...(entry as unknown as EnvironmentRoute),...extra,keys:['k'],anonymousToken:'anon',enabled:true};
  const handler=createGateway(new Map([[E,route]]),(async(url:RequestInfo|URL) => {calls.push(String(url));return Response.json([]);}) as unknown as typeof fetch);
  expect((await handler(new Request(`http://local/${E}/rest/v1/items`,{headers:{apikey:'k'}}))).status).toBe(200);
  expect(calls).toEqual(['http://172.30.0.4:3000/items']);
 });
 test(`the database and management documents are read ${label}`,() => {
  const root=installation({'.lab/upstream/database.json':{host:'172.30.0.2',port:5432,...extra},'.lab/upstream/management.json':{auth:'http://172.30.0.7:9999',...extra}});
  // The readers of upstream-server.ts and upstream-app.ts, by their own expressions.
  const database=readJsonCached(join(root,'.lab/upstream/database.json')) as {host?:string;port?:number};
  expect(database.host&&database.port?{host:database.host,port:database.port}:undefined).toEqual({host:'172.30.0.2',port:5432});
  expect((readJsonCached(join(root,'.lab/upstream/management.json')) as {auth:string}).auth).toBe('http://172.30.0.7:9999');
 });
}

test('a rewrite of the management document is seen on the next read, as a replaced realm needs',() => {
 const root=installation({'.lab/upstream/management.json':{auth:'http://172.30.0.7:9999'}});
 const path=join(root,'.lab/upstream/management.json');
 expect((readJsonCached(path) as {auth:string}).auth).toBe('http://172.30.0.7:9999');
 writeFileSync(path,JSON.stringify({auth:'http://172.30.0.9:9999',extra:true}));
 expect((readJsonCached(path) as {auth:string}).auth).toBe('http://172.30.0.9:9999');
 expect(readFileSync(path,'utf8')).toContain('172.30.0.9');
});

test('a Studio state that cannot be read is no sessions, not a crash',() => {
 const root=installation({});
 writeFileSync(join(root,'.lab/upstream/studio.json'),'{');
 expect(studioState()).toEqual({sessions:{}});
});
