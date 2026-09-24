import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {PressureMonitor,type Saturation} from '../src/gateway/pressure';
import {Catalog,CATALOG_SCHEMA_VERSION} from '../src/control/catalog';

const request=()=>new Request('http://localhost/');
const empty=async()=>new Response(null,{status:204});
const hold=(gate:ConcurrencyGate,environment:string)=>{let done!:(r:Response)=>void;
 const pending=gate.run(environment,request(),()=>new Promise<Response>(resolve=>{done=resolve;}));
 return async()=>{done?.(new Response(null,{status:204}));await pending;};};

test('a run of saturated minutes reports once, a calm minute resets the run',async()=>{
 const gate=new ConcurrencyGate(1,4,30_000,30_000,{ceiling:2,headroom:1});
 const reports:[string,Saturation][]=[];
 const monitor=new PressureMonitor(gate,(runtime,s)=>reports.push([runtime,s]),3);
 const releases=[hold(gate,'busy'),hold(gate,'busy')];await Bun.sleep(0);   // at its ceiling of 2
 for(let minute=0;minute<3;minute++){await gate.run('busy',request(),empty);monitor.sample();}   // refused each minute
 expect(reports).toEqual([['busy',{minutes:3,refused:3,peak:2,guarantee:1}]]);
 await gate.run('busy',request(),empty);monitor.sample();                   // a new run starts
 for(const release of releases)await release();
 monitor.sample();monitor.sample();                                         // calm minutes end it
 expect(reports.length).toBe(1);
});

test('borrowing an idle server, however long, never notifies',async()=>{
 const gate=new ConcurrencyGate(2,32,30_000,30_000,{ceiling:24,headroom:8});
 const reports:unknown[]=[];const monitor=new PressureMonitor(gate,(...args)=>reports.push(args),2);
 const held=Array.from({length:20},()=>hold(gate,'busy'));await Bun.sleep(0);
 for(let minute=0;minute<5;minute++)monitor.sample();
 expect(reports).toEqual([]);
 for(const release of held)await release();
});

test('a quiet environment within its share is never reported',async()=>{
 const gate=new ConcurrencyGate(2,4);
 const reports:unknown[]=[];const monitor=new PressureMonitor(gate,(...args)=>reports.push(args),1);
 await gate.run('quiet',request(),empty);monitor.sample();monitor.sample();
 expect(reports).toEqual([]);
});

test('a failing report never escapes the monitor',async()=>{
 const gate=new ConcurrencyGate(1,2);const monitor=new PressureMonitor(gate,()=>{throw new Error('outbox down');},1);
 const release=hold(gate,'a');await Bun.sleep(0);await gate.run('a',request(),empty);
 expect(()=>monitor.sample()).not.toThrow();await release();
});

function readyRuntime(catalog:Catalog) {
 const org=catalog.createOrganization('owner','Org'),project=catalog.createProject('owner',org,'Shop');
 const environment=catalog.createEnvironment('owner',project,'production'),job=catalog.claimProvision()!;
 catalog.finishProvision(environment,job.claim!,true);
 return {environment,runtime:job.runtime};
}

test('the catalog writes one saturation notice per window, on every channel',()=>{
 const catalog=new Catalog(':memory:');const {environment,runtime}=readyRuntime(catalog);
 const detail={minutes:15,refused:40,peak:24,guarantee:8};
 const first=catalog.environmentSaturated(runtime,detail),second=catalog.environmentSaturated(runtime,detail);
 expect(first).toBeString();expect(second).toBe(first!);
 const db=(catalog as unknown as {db:Database}).db;
 const row=db.query('SELECT kind,severity,reason,environment,occurrences,detail FROM notification_outbox').get() as Record<string,unknown>;
 expect(row).toEqual({kind:'environment.saturated',severity:'warning',reason:'environment_saturated',environment,occurrences:2,detail:JSON.stringify(detail)});
 expect(db.query('SELECT channel FROM notification_delivery ORDER BY channel').all()).toEqual([{channel:'email'},{channel:'telegram'},{channel:'webhook'}]);
 expect(catalog.environmentSaturated('e_000000000000000000000000',detail)).toBeUndefined();
 catalog.close();
});

test('an older catalog keeps its deliveries and gains the Telegram channel',()=>{
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-catalog-'));
 try {
  const path=join(directory,'control.sqlite');
  const old=new Catalog(path,{channels:['email']});const {runtime}=readyRuntime(old);
  old.environmentSaturated(runtime,{minutes:15,refused:1,peak:9,guarantee:8});old.close();
  // Put the delivery table back to its version 2 shape, as a release before this one left it.
  const db=new Database(path);
  const sql=(db.query("SELECT sql FROM sqlite_master WHERE name='notification_delivery'").get() as {sql:string}).sql;
  db.exec(`ALTER TABLE notification_delivery RENAME TO d; ${sql.replace(",'telegram'","")}; INSERT INTO notification_delivery SELECT * FROM d; DROP TABLE d; PRAGMA user_version=2;`);
  db.close();
  const upgraded=new Catalog(path);
  expect(upgraded.schemaVersion()).toBe(CATALOG_SCHEMA_VERSION);
  const check=new Database(path);
  expect((check.query("SELECT sql FROM sqlite_master WHERE name='notification_delivery'").get() as {sql:string}).sql).toContain("'telegram'");
  expect(check.query('SELECT channel FROM notification_delivery').all()).toEqual([{channel:'email'}]);
  check.close();upgraded.close();
 } finally {rmSync(directory,{recursive:true,force:true});}
});
