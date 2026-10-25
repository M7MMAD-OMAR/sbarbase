import {afterEach,expect,test} from 'bun:test';
import {createServer} from 'node:net';
import {chmodSync,copyFileSync,existsSync,mkdirSync,mkdtempSync,readdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {processStart} from '../src/http/handover';
import {join} from 'node:path';

/** The real lab/upstream-server.ts, started in a throwaway installation folder (a catalog, a key store and a
 * management realm address are all it needs), as the supervisor starts it: the old process serving, the
 * candidate beside it. This is the entry point's own wiring: the signal handlers, candidate.json, the
 * protocol in server.json, the promoted server.json and the exit status 75. */
const server=new URL('../lab/upstream-server.ts',import.meta.url).pathname;
const protocolFile=new URL('../lab/handover.py',import.meta.url).pathname;
const children:{kill(signal?:number|NodeJS.Signals):void}[]=[];
import {releasePorts,reservePort} from './fixtures/ports';
const folders:string[]=[];
afterEach(() => {releasePorts();for(const child of children.splice(0))try{child.kill('SIGKILL');}catch{}for(const folder of folders.splice(0))rmSync(folder,{recursive:true,force:true});});

/** A free port below Linux's ephemeral range (32768 to 60999), where the clients of these tests take their source
 * ports: a port found free in the range can be taken by one of them before it is bound. */
const freePort=reservePort;
function installation() {
 const root=mkdtempSync(join(tmpdir(),'handover-server-'));folders.push(root);
 for(const folder of ['.lab/upstream','.secrets/upstream','lab'])mkdirSync(join(root,folder),{recursive:true,mode:0o700});
 for(const folder of ['.lab','.secrets','.lab/upstream','.secrets/upstream'])chmodSync(join(root,folder),0o700);
 copyFileSync(protocolFile,join(root,'lab/handover.py'));
 writeFileSync(join(root,'.lab/upstream/management.json'),JSON.stringify({auth:'http://127.0.0.1:9'}));
 writeFileSync(join(root,'.lab/upstream/endpoints.json'),'{}');
 writeFileSync(join(root,'.secrets/upstream/runtime.json'),JSON.stringify({management:{jwt:'a'.repeat(64)},environments:{}}));
 return root;
}
const until=async(done:() => boolean,ms=20_000) => {const deadline=Date.now()+ms;while(!done()&&Date.now()<deadline)await Bun.sleep(10);return done();};
function start(root:string,port:number,env:Record<string,string>,without:string[]=[]) {
 const inherited:Record<string,string|undefined>={...process.env};
 for(const name of without)delete inherited[name];
 const child=Bun.spawn(['bun',server],{cwd:root,env:{...inherited,SBARBASE_CONSOLE_PORT:String(port),SBARBASE_DATABASE_PORT:'off',
  SBARBASE_DATABASE_EXTERNAL_PORT:'off',...env},stdout:'pipe',stderr:'pipe'});
 children.push(child);
 void new Response(child.stdout).text();void new Response(child.stderr).text();
 return child;
}
// A file the process is writing may be seen half written (Bun.write truncates first): that is not there yet.
const json=(root:string,name:string) => {
 try{return existsSync(join(root,name))?JSON.parse(readFileSync(join(root,name),'utf8')):undefined;}catch{return undefined;}
};
async function pid(port:number):Promise<string|null> {
 try{const answer=await fetch(`http://127.0.0.1:${port}/health`,{headers:{connection:'close'},signal:AbortSignal.timeout(5_000)});await answer.text();return answer.headers.get('x-sbarbase-pid');}
 catch{return 'error';}
}

test('the switch off (0): no new file is written, no header added and a candidate marker alone changes nothing',async() => {
 const root=installation(),port=await freePort();
 const process_=start(root,port,{SBARBASE_HANDOVER:'0',SBARBASE_CANDIDATE:'1'});
 expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===process_.pid)).toBe(true);
 expect(Object.keys(json(root,'.lab/upstream/server.json')).sort()).toEqual(['pid','url']);
 expect(await pid(port)).toBeNull();
 const names=readdirSync(join(root,'.lab/upstream'));
 expect(names.filter(name => /^(listeners-|candidate\.json|handover)/.test(name))).toEqual([]);
 process_.kill('SIGTERM');
 expect(await process_.exited).toBe(0);
},60_000);

for(const [label,env,without] of [['unset',{},['SBARBASE_HANDOVER']],['empty',{SBARBASE_HANDOVER:''},[]],['an unknown value (yes)',{SBARBASE_HANDOVER:'yes'},[]]] as [string,Record<string,string>,string[]][]){
 test(`the switch ${label} means on: the process keeps its listener record and answers with its pid`,async() => {
  const root=installation(),port=await freePort();
  const process_=start(root,port,env,without);
  expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===process_.pid)).toBe(true);
  expect(await until(() => json(root,`.lab/upstream/listeners-${process_.pid}.json`)!==undefined)).toBe(true);
  expect(json(root,`.lab/upstream/listeners-${process_.pid}.json`)).toMatchObject({role:'serving'});
  expect(await pid(port)).toBe(String(process_.pid));
  process_.kill('SIGTERM');
  expect(await process_.exited).toBe(0);
 },60_000);
}

test('the real entry point: an old process, a candidate that waits, a promotion, and the release',async() => {
 const root=installation(),port=await freePort();
 const old=start(root,port,{SBARBASE_HANDOVER:'1'});
 expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===old.pid)).toBe(true);
 expect(json(root,'.lab/upstream/server.json').protocol).toBeGreaterThan(0);
 expect(await until(() => json(root,`.lab/upstream/listeners-${old.pid}.json`)!==undefined)).toBe(true);
 expect(json(root,`.lab/upstream/listeners-${old.pid}.json`)).toMatchObject({role:'serving',frozen:false,
  listeners:[{name:'console',host:'127.0.0.1',port,steered:false}]});
 expect(await pid(port)).toBe(String(old.pid));
 writeFileSync(join(root,'.lab/upstream/handover.json'),JSON.stringify({pid:old.pid,started:processStart(old.pid)}));
 expect(await until(() => json(root,`.lab/upstream/listeners-${old.pid}.json`).frozen===true,10_000)).toBe(true);

 const candidate=start(root,port,{SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1'});
 expect(await until(() => json(root,'.lab/upstream/candidate.json')?.pid===candidate.pid)).toBe(true);
 const announced=json(root,'.lab/upstream/candidate.json');
 expect(announced.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
 expect(announced.url).not.toBe(`http://127.0.0.1:${port}`);
 // Still the old process on the shared address; the candidate answers on its private one.
 expect(json(root,'.lab/upstream/server.json').pid).toBe(old.pid);
 expect(await pid(port)).toBe(String(old.pid));
 expect(await pid(Number(new URL(announced.url).port))).toBe(String(candidate.pid));
 expect(json(root,`.lab/upstream/listeners-${candidate.pid}.json`)).toMatchObject({role:'candidate',frozen:false,listeners:[]});

 candidate.kill('SIGUSR2');
 expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===candidate.pid)).toBe(true);
 expect(json(root,'.lab/upstream/server.json')).toMatchObject({promoted:true,url:`http://127.0.0.1:${port}`,private:announced.url});
 expect(json(root,`.lab/upstream/listeners-${candidate.pid}.json`)).toMatchObject({role:'candidate',
  listeners:[{name:'console',host:'127.0.0.1',port,steered:true}]});
 for(let i=0;i<15;i++)expect(await pid(port)).toBe(String(candidate.pid));

 old.kill('SIGTERM');
 expect(await old.exited).toBe(0);
 expect(json(root,'.lab/upstream/server.json').pid).toBe(candidate.pid);
 expect(existsSync(join(root,`.lab/upstream/listeners-${old.pid}.json`))).toBe(false);
 for(let i=0;i<10;i++)expect(await pid(port)).toBe(String(candidate.pid));

 candidate.kill('SIGUSR2');
 expect(await until(() => json(root,`.lab/upstream/listeners-${candidate.pid}.json`).role==='serving')).toBe(true);
 expect(json(root,`.lab/upstream/listeners-${candidate.pid}.json`).listeners[0].steered).toBe(false);
 candidate.kill('SIGUSR2');
 await Bun.sleep(300);
 expect(await pid(port)).toBe(String(candidate.pid));
 candidate.kill('SIGTERM');
 expect(await candidate.exited).toBe(0);
 expect(existsSync(join(root,'.lab/upstream/server.json'))).toBe(false);
 expect(readdirSync(join(root,'.lab/upstream')).filter(name => name.startsWith('listeners-')||name==='candidate.json')).toEqual([]);
},90_000);

test('a candidate that cannot bind a fixed listener exits 75 and leaves the old process serving',async() => {
 const root=installation(),port=await freePort();
 const old=start(root,port,{SBARBASE_HANDOVER:'1'});
 expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===old.pid)).toBe(true);
 writeFileSync(join(root,'.lab/upstream/handover.json'),JSON.stringify({pid:old.pid,started:processStart(old.pid)}));
 const hog=createServer();
 const taken=await freePort();
 await new Promise<void>(resolve => hog.listen({port:taken,host:'127.0.0.1'},() => resolve()));
 try{
  const candidate=start(root,port,{SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1',SBARBASE_DATABASE_PORT:String(taken)});
  expect(await until(() => json(root,'.lab/upstream/candidate.json')?.pid===candidate.pid)).toBe(true);
  candidate.kill('SIGUSR2');
  expect(await Promise.race([candidate.exited,Bun.sleep(30_000).then(() => 'late')])).toBe(75);
  expect(json(root,'.lab/upstream/server.json').pid).toBe(old.pid);
  expect(await pid(port)).toBe(String(old.pid));
 }finally{hog.close();}
},90_000);

test('a stop before the promotion ends the candidate cleanly and the old process keeps the address',async() => {
 const root=installation(),port=await freePort();
 const old=start(root,port,{SBARBASE_HANDOVER:'1'});
 expect(await until(() => json(root,'.lab/upstream/server.json')?.pid===old.pid)).toBe(true);
 const candidate=start(root,port,{SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1'});
 expect(await until(() => json(root,'.lab/upstream/candidate.json')?.pid===candidate.pid)).toBe(true);
 candidate.kill('SIGTERM');
 expect(await candidate.exited).toBe(0);
 expect(existsSync(join(root,'.lab/upstream/candidate.json'))).toBe(false);
 expect(existsSync(join(root,`.lab/upstream/listeners-${candidate.pid}.json`))).toBe(false);
 expect(json(root,'.lab/upstream/server.json').pid).toBe(old.pid);
 expect(await pid(port)).toBe(String(old.pid));
},60_000);

/** The signals a process catches, from /proc/<pid>/status (SigCgt). */
const caught=(pid:number) => {
 try{return BigInt('0x'+/SigCgt:\s*([0-9a-f]+)/.exec(readFileSync(`/proc/${pid}/status`,'utf8'))![1]!);}catch{return 0n;}
};
const SIGUSR2=1n<<11n,SIGTERM=1n<<14n;
/** Resolves as soon as the process has installed the handlers a candidate installs first. */
async function handlersInstalled(pid:number) {
 return until(() => (caught(pid)&(SIGUSR2|SIGTERM))===(SIGUSR2|SIGTERM),20_000);
}

test('a stop that arrives while the candidate is still starting is carried out once it is up',async() => {
 const root=installation(),port=await freePort();
 const candidate=start(root,port,{SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1'});
 expect(await handlersInstalled(candidate.pid)).toBe(true);
 candidate.kill('SIGTERM');
 expect(await Promise.race([candidate.exited,Bun.sleep(30_000).then(() => 'late')])).toBe(0);
},60_000);

test('a promote signal that arrives while the candidate is still starting is queued, not lost',async() => {
 const root=installation(),port=await freePort();
 const candidate=start(root,port,{SBARBASE_HANDOVER:'1',SBARBASE_CANDIDATE:'1'});
 expect(await handlersInstalled(candidate.pid)).toBe(true);
 candidate.kill('SIGUSR2');
 // Alone on the port, it binds it without a partner: promoted all the same.
 expect(await until(() => json(root,'.lab/upstream/server.json')?.promoted===true,30_000)).toBe(true);
 expect(json(root,'.lab/upstream/server.json').pid).toBe(candidate.pid);
 expect(await pid(port)).toBe(String(candidate.pid));
},60_000);
