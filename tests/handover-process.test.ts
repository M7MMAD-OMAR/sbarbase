import {afterEach,expect,test} from 'bun:test';
import {createServer} from 'node:net';
import {existsSync,mkdtempSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {listeningInodes,processStart} from '../src/http/handover';

/** Two real console processes (tests/fixtures/handover-process.ts: the gateway and signal wiring of
 * lab/upstream-server.ts over a stand-in application) on loopback ports, as the handover runs them: the
 * old one serves, the candidate joins every fixed port, steers new connections to itself through
 * bun:ffi, and the old one retires, while clients connect without a pause. What this shows is the
 * kernel and Bun mechanism with the repository's own code; the supervisor's side is lab/test_handover.py. */
const fixture=new URL('./fixtures/handover-process.ts',import.meta.url).pathname;
const children:{kill(signal?:number|NodeJS.Signals):void}[]=[];
import {releasePorts,reservePort} from './fixtures/ports';
const folders:string[]=[];
afterEach(() => {releasePorts();for(const child of children.splice(0))try{child.kill('SIGKILL');}catch{}for(const folder of folders.splice(0))rmSync(folder,{recursive:true,force:true});});

/** A free port below Linux's ephemeral range (32768 to 60999), where the clients of these tests take their source
 * ports: a port found free in the range can be taken by one of them before it is bound. */
const freePort=reservePort;
const until=async(done:() => boolean,ms=15_000) => {const deadline=Date.now()+ms;while(!done()&&Date.now()<deadline)await Bun.sleep(10);return done();};

async function layout() {
 const state=mkdtempSync(join(tmpdir(),'handover-process-'));folders.push(state);
 return {state,ports:{PORT:String(await freePort()),DATABASE_PORT:String(await freePort()),STUDIO_PORT:String(await freePort())}};
}
async function launch(role:'serving'|'candidate',where:Awaited<ReturnType<typeof layout>>,tag:string,extra:Record<string,string>={}) {
 const child=Bun.spawn(['bun',fixture],{env:{...process.env,ROLE:role,STATE:where.state,TAG:tag,...where.ports,...extra},stdout:'pipe',stderr:'pipe'});
 children.push(child);
 let output='';
 const reader=child.stdout.getReader();
 const decoder=new TextDecoder();
 const ready=(async() => {for(;;){const {value,done}=await reader.read();if(done)return false;output+=decoder.decode(value);if(output.includes('READY'))return true;}})();
 const errors=new Response(child.stderr).text();
 if(!await Promise.race([ready,child.exited.then(() => false)]))throw new Error(`${role} did not start: ${await errors}`);
 // Keeps reading, so a full pipe never blocks the child.
 void (async() => {for(;;){const {done}=await reader.read();if(done)return;}})();
 return {child,pid:child.pid,errors};
}
const record=(state:string,pid:number) => JSON.parse(readFileSync(join(state,`listeners-${pid}.json`),'utf8'));
const serverPid=(state:string) => {
 try{return JSON.parse(readFileSync(join(state,'server.json'),'utf8')).pid as number;}catch{return undefined;}
};
type Seen={pid:string|null;error?:string;started:number};
/** One request on a fresh connection to the console port: the answering process, or the failure. */
async function ask(port:string):Promise<Seen> {
 const started=performance.now();
 try{
  const answer=await fetch(`http://127.0.0.1:${port}/health`,{headers:{connection:'close'},signal:AbortSignal.timeout(60_000)});
  await answer.text();
  return answer.status===200?{pid:answer.headers.get('x-sbarbase-pid'),started}:{pid:null,error:'HTTP '+answer.status,started};
 }catch(error){return {pid:null,error:(error as Error).message,started};}
}
/** Clients that connect without a pause until stopped. */
function load(port:string,clients=8) {
 const seen:Seen[]=[];let running=true;
 // Paced: back to back clients make thousands of connections a second, and loopback then drops a SYN now and then
 // (a source port reused against a TIME-WAIT), which stalls that one connection for 1, 3 or 7 s.
 const loops=Array.from({length:clients},async() => {while(running){seen.push(await ask(port));await Bun.sleep(4);}});
 return {seen,async stop(){running=false;await Promise.all(loops);}};
}

test('the candidate takes every new connection once steered, and the old process retires with no request lost',async() => {
 const where=await layout();
 const old=await launch('serving',where,'old');
 expect(await until(() => existsSync(join(where.state,`listeners-${old.pid}.json`)))).toBe(true);
  // Its Studio route is open, as it is by the time an upgrade is applied.
 expect(await until(() => record(where.state,old.pid).listeners.length===3)).toBe(true);
 // The supervisor names the old process (A7): its listeners freeze.
 writeFileSync(join(where.state,'handover.json'),JSON.stringify({pid:old.pid,started:processStart(old.pid)}));
 expect(await until(() => record(where.state,old.pid).frozen===true)).toBe(true);
 const candidate=await launch('candidate',where,'new');
 // Before the promotion the candidate holds no fixed listener: the old process answers alone.
 expect(record(where.state,candidate.pid).listeners).toEqual([]);
 for(let i=0;i<10;i++)expect((await ask(where.ports.PORT)).pid).toBe(String(old.pid));
 expect(listeningInodes('127.0.0.1',Number(where.ports.PORT)).length).toBe(1);

 const clients=load(where.ports.PORT);
 await Bun.sleep(150);
 candidate.child.kill('SIGUSR2');
 const named=() => serverPid(where.state);
 expect(named()).toBe(old.pid);
 expect(await until(() => named()===candidate.pid)).toBe(true);
 const published=JSON.parse(readFileSync(join(where.state,'server.json'),'utf8'));
 expect(published).toMatchObject({pid:candidate.pid,promoted:true,url:`http://127.0.0.1:${where.ports.PORT}`});
 expect(published.private).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
 // Two members on each shared port, the candidate's record steered on every one (H4, H5).
 for(const port of Object.values(where.ports))expect(listeningInodes('127.0.0.1',Number(port)).length).toBe(2);
 const promoted=record(where.state,candidate.pid);
 expect(promoted.role).toBe('candidate');
 expect(promoted.listeners.map((item:{name:string}) => item.name)).toEqual(['console','database','studio-internal']);
 expect(promoted.listeners.every((item:{steered:boolean}) => item.steered)).toBe(true);
 // From the attach on, every fresh connection is the candidate's (H7). The attach was done before server.json named the
 // candidate, so a request that STARTED after this moment is a fresh connection after the attach; one that started earlier
 // may rightly have been answered by the old process, whenever its answer came back.
 const attached=performance.now();
 // Enough answers to mean something, however starved the clients are; the count is not the assertion.
 const fresh=() => clients.seen.filter(item => item.started>=attached);
 expect(await until(() => fresh().length>20,60_000)).toBe(true);
 expect(fresh().filter(item => item.pid!==String(candidate.pid)).length).toBe(0);

 // H8: the old process retires; the load goes on through its accept grace and its exit.
 old.child.kill('SIGTERM');
 expect(await Promise.race([old.child.exited,Bun.sleep(25_000).then(() => 'late')])).toBe(0);
 const before=clients.seen.length;
 expect(await until(() => clients.seen.length-before>20,60_000)).toBe(true);
 await clients.stop();
 // Not one request failed. A request is allowed to be slow (a loaded host stalls loopback connects for seconds),
 // not to be lost: the clients wait up to a minute for an answer.
 expect(clients.seen.filter(item => item.error)).toEqual([]);
 expect(clients.seen.length).toBeGreaterThan(50);
 for(const port of Object.values(where.ports))expect(listeningInodes('127.0.0.1',Number(port)).length).toBe(1);
 expect(existsSync(join(where.state,`listeners-${old.pid}.json`))).toBe(false);
 // The old process left the published address to the candidate.
 expect(JSON.parse(readFileSync(join(where.state,'server.json'),'utf8')).pid).toBe(candidate.pid);

 // H9a: the next SIGUSR2 detaches the program; a later one is ignored.
 candidate.child.kill('SIGUSR2');
 expect(await until(() => record(where.state,candidate.pid).listeners.every((item:{steered:boolean}) => !item.steered))).toBe(true);
 candidate.child.kill('SIGUSR2');
 await Bun.sleep(200);
 expect((await ask(where.ports.PORT)).pid).toBe(String(candidate.pid));
 expect(record(where.state,candidate.pid).listeners.every((item:{steered:boolean}) => !item.steered)).toBe(true);
},90_000);

test('a candidate that cannot bind a fixed port exits 75 and the old process goes on serving alone',async() => {
 const where=await layout();
 const old=await launch('serving',where,'old');
 writeFileSync(join(where.state,'handover.json'),JSON.stringify({pid:old.pid,started:processStart(old.pid)}));
 const candidate=await launch('candidate',where,'new',{DATABASE_PORT:where.ports.DATABASE_PORT});
 // A listener without SO_REUSEPORT on the Studio port that the old process has not opened yet: the
 // old process serves the console and the database port, the candidate wants all three.
 const hog=createServer();
 await new Promise<void>(resolve => hog.listen({port:Number(where.ports.STUDIO_PORT),host:'127.0.0.1'},() => resolve()));
 try{
  candidate.child.kill('SIGUSR2');
  expect(await Promise.race([candidate.child.exited,Bun.sleep(15_000).then(() => 'late')])).toBe(75);
  expect(JSON.parse(readFileSync(join(where.state,'server.json'),'utf8')).pid).toBe(old.pid);
  // Everything the candidate had bound is closed again: the old process alone is in each group.
  expect(listeningInodes('127.0.0.1',Number(where.ports.PORT)).length).toBe(1);
  expect(listeningInodes('127.0.0.1',Number(where.ports.DATABASE_PORT)).length).toBe(1);
  expect((await ask(where.ports.PORT)).pid).toBe(String(old.pid));
 }finally{hog.close();}
},60_000);

test('a SIGTERM to the candidate before it was promoted ends it and leaves the old process serving',async() => {
 const where=await layout();
 const old=await launch('serving',where,'old');
 const candidate=await launch('candidate',where,'new');
 candidate.child.kill('SIGTERM');
 expect(await Promise.race([candidate.child.exited,Bun.sleep(25_000).then(() => 'late')])).toBe(0);
 expect(existsSync(join(where.state,`listeners-${candidate.pid}.json`))).toBe(false);
 expect(existsSync(join(where.state,'candidate.json'))).toBe(false);
 expect((await ask(where.ports.PORT)).pid).toBe(String(old.pid));
},60_000);
