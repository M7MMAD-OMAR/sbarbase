import {test,expect,afterAll} from 'bun:test';
import {Database} from 'bun:sqlite';
import {chmodSync,mkdirSync,mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {main,parse,backupsOf,EXIT,type Deps} from '../lab/sbarbase';
import {managementPublishableKey} from '../lab/upstream-app';

const TOKEN='access-token-that-must-never-print';
const PASSWORD='correct horse battery staple';
const NEW_KEY='sb_publishable_new_key_shown_once';
const CONSOLE='http://127.0.0.1:8790';
const roots:string[]=[];
afterAll(()=>{for(const root of roots)rmSync(root,{recursive:true,force:true});});

/** A throwaway installation root with a real control catalog, published endpoints and backups. */
function installation(options:{console?:boolean}={}) {
 const root=mkdtempSync(join(tmpdir(),'sbarbase-cli-'));roots.push(root);
 const upstream=join(root,'.lab','upstream');mkdirSync(upstream,{recursive:true});
 const catalog=new Catalog(join(upstream,'control.sqlite'));
 const org=catalog.createOrganization('alice','Acme');
 const shop=catalog.createProject('alice',org,'shop');
 const production=catalog.createEnvironment('alice',shop,'production');
 const staging=catalog.createEnvironment('alice',shop,'staging');
 catalog.close();
 const database=new Database(join(upstream,'control.sqlite'));
 database.query("UPDATE provision_jobs SET state='succeeded' WHERE environment=?").run(production);
 const runtime=database.query<{runtime:string},[string]>('SELECT runtime FROM provision_jobs WHERE environment=?').get(production)!.runtime;
 const stagingRuntime=database.query<{runtime:string},[string]>('SELECT runtime FROM provision_jobs WHERE environment=?').get(staging)!.runtime;
 database.query(`INSERT INTO notification_outbox(id,at,last_at,window_until,kind,severity,dedupe_key,actor,reason,detail,expires_at)
  VALUES ('n1',1790000000000,1790000000000,0,'backup.failed','critical','k','system:supervisor','export_failed','{"secret_detail":"hidden"}',0)`).run();
 database.query(`INSERT INTO notification_delivery(event,channel,state,attempts,next_attempt_at,last_error)
  VALUES ('n1','email','failed',3,0,'smtp said something private')`).run();
 database.close();
 writeFileSync(join(upstream,'endpoints.json'),JSON.stringify({[runtime]:{auth:'http://10.0.0.2:9999',rest:'http://10.0.0.3:3000'}}));
 if(options.console!==false)writeFileSync(join(upstream,'server.json'),JSON.stringify({url:CONSOLE,pid:4242}));
 for(const stamp of ['20260922T030000Z','20260923T030000Z']) {
  mkdirSync(join(root,'.lab','backups',runtime,stamp),{recursive:true});
  writeFileSync(join(root,'.lab','backups',runtime,stamp,'manifest.json'),'{}');
 }
 // Interrupted: no manifest, so never the last backup.
 mkdirSync(join(root,'.lab','backups',runtime,'20260924T030000Z'),{recursive:true});
 return {root,production,staging,runtime,stagingRuntime,project:shop,organization:org};
}

type Call={url:string;method:string;headers:Record<string,string>;body?:string};

/** Deps with every external effect recorded: children, HTTP, prompts and output. */
function harness(root:string,options:{routes?:(call:Call)=>Response|undefined;tty?:boolean;answers?:string[];
 stdin?:string;env?:Record<string,string>;container?:boolean;unit?:boolean;code?:number;unitEnvironment?:string}={}) {
 const runs:string[][]=[],calls:Call[]=[],out:string[]=[],err:string[]=[],prompts:{question:string;secret:boolean}[]=[];
 const answers=[...(options.answers??[])];
 const deps:Deps={
  root,env:options.env??{},
  run:async argv=>{runs.push(argv);return options.code??0;},
  capture:async argv=>{runs.push(argv);return {code:0,stdout:argv[1]==='show'?options.unitEnvironment??'Environment=\n':'active\n'};},
  fetch:(async(input:RequestInfo|URL,init?:RequestInit)=>{
   const call={url:String(input),method:init?.method??'GET',headers:Object.fromEntries(new Headers(init?.headers).entries()),
    body:typeof init?.body==='string'?init.body:undefined};
   calls.push(call);
   if(call.url.endsWith('/health')||call.url.endsWith(':3000/'))return new Response('ok');
   if(call.url===CONSOLE+'/management/auth/v1/token?grant_type=password')
    return Response.json(JSON.parse(call.body!).password===PASSWORD?{access_token:TOKEN}:{message:'Invalid login'},
     {status:JSON.parse(call.body!).password===PASSWORD?200:400});
   if(call.url===CONSOLE+'/management/auth/v1/logout')return new Response(null,{status:204});
   return options.routes?.(call)??Response.json({message:'Unknown route'},{status:404});
  }) as typeof fetch,
  prompt:async(question,secret=false)=>{prompts.push({question,secret});return answers.shift()??'';},
  stdin:async()=>options.stdin??'',
  isTTY:options.tty??false,
  out:text=>out.push(text),err:text=>err.push(text),
  pidAlive:pid=>pid===4242,
  inContainer:options.container??false,
  unitPath:options.unit?join(root,'sbarbase.service'):join(root,'absent.service'),
 };
 if(options.unit)writeFileSync(deps.unitPath,'[Unit]\n');
 const printed=()=>out.join('\n')+'\n'+err.join('\n');
 return {deps,runs,calls,out,err,prompts,printed};
}

test('parsing: commands, flags, aliases and refusals',()=>{
 expect(parse([])).toEqual({command:'help',args:[],flags:{}});
 expect(parse(['status','--json'])).toEqual({command:'status',args:[],flags:{'--json':true}});
 expect(parse(['logs','auth','x','-n','5','-f']).flags).toEqual({'--lines':'5','--follow':true});
 expect(parse(['upgrade','check','--to=v1']).flags['--to']).toBe('v1');
 expect(()=>parse(['nope'])).toThrow('Unknown command');
 expect(()=>parse(['status','--yes'])).toThrow('does not apply');
 expect(()=>parse(['status','--bogus'])).toThrow('Unknown option');
 expect(()=>parse(['backup','now','--keep'])).toThrow('needs a value');
 expect(()=>parse(['rotate-key','x','--password','hunter2'])).toThrow('never accepted as an argument');
 expect(()=>parse(['status','--json=1'])).toThrow('takes no value');
});

test('help and usage errors exit with their own codes, and the help has no long dash',async()=>{
 const {root}=installation();
 const help=harness(root);
 expect(await main(['--help'],help.deps)).toBe(EXIT.ok);
 expect(help.out.join('\n')).toContain('Exit codes');
 expect(help.out.join('\n')).not.toMatch(/[\u2013\u2014]/);
 const topic=harness(root);
 expect(await main(['restore','--help'],topic.deps)).toBe(EXIT.ok);
 expect(topic.out.join('\n')).toContain('lab/backup.py restore');
 expect(await main(['help','restore'],harness(root).deps)).toBe(EXIT.ok);
 expect(await main(['help','nope'],harness(root).deps)).toBe(EXIT.usage);
 const bad=harness(root);
 expect(await main(['frobnicate'],bad.deps)).toBe(EXIT.usage);
 expect(bad.err.join('\n')).toContain('Unknown command');
 expect(await main(['status','extra'],harness(root).deps)).toBe(EXIT.usage);
 expect(bad.runs).toEqual([]);
});

test('status --json reports service, console, environments with routing, health and last backup, and notifications',async()=>{
 const {root,runtime,production,stagingRuntime}=installation();
 const run=harness(root,{unit:true});
 expect(await main(['status','--json'],run.deps)).toBe(EXIT.ok);
 const status=JSON.parse(run.out.join('\n'));
 expect(status.healthy).toBe(true);
 expect(status.service).toEqual({manager:'systemd',state:'active'});
 expect(run.runs).toEqual([['systemctl','is-active','sbarbase.service']]);
 expect(status.console).toEqual({running:true,url:CONSOLE});
 expect(status.catalog).toBe('ok');
 const ready=status.environments.find((environment:any)=>environment.runtime===runtime);
 expect(ready).toMatchObject({id:production,client:'Acme',project:'shop',name:'production',state:'succeeded',published:true,
  routing:{mode:'serving',placement:'source',revision:0},health:{auth:200,rest:200},lastBackup:'20260923T030000Z',backups:2});
 const queued=status.environments.find((environment:any)=>environment.runtime===stagingRuntime);
 expect(queued).toMatchObject({name:'staging',state:'queued',published:false,health:null,lastBackup:null});
 expect(status.notifications.undelivered).toBe(1);
 expect(status.notifications.recent[0]).toEqual({kind:'backup.failed',severity:'critical',reason:'export_failed',
  at:new Date(1790000000000).toISOString(),channels:{email:'failed'}});
 // Neither the event detail nor a delivery error ever leaves the catalog.
 expect(run.printed()).not.toContain('hidden');
 expect(run.printed()).not.toContain('private');
 expect(run.calls.map(call=>call.url).sort()).toEqual(['http://10.0.0.2:9999/health','http://10.0.0.3:3000/']);
});

test('status reads routing: a moved environment is checked at its placement, maintenance is not a failure',async()=>{
 const {root,runtime}=installation();
 const database=new Database(join(root,'.lab','upstream','control.sqlite'));
 database.query('INSERT INTO runtime_routing VALUES (?,?,?,?)').run(runtime,3,1,JSON.stringify({auth:'http://10.9.0.2:9999',rest:'http://10.9.0.3:3000'}));
 database.close();
 const run=harness(root,{routes:()=>undefined});
 run.deps.fetch=(async(input:RequestInfo|URL)=>{run.calls.push({url:String(input),method:'GET',headers:{}});throw new Error('down');}) as unknown as typeof fetch;
 expect(await main(['status','--json'],run.deps)).toBe(EXIT.ok);
 const status=JSON.parse(run.out.join('\n'));
 expect(status.environments[0].routing).toEqual({mode:'maintenance',placement:'moved',revision:3});
 expect(status.environments[0].health).toEqual({auth:'unreachable',rest:'unreachable'});
 expect(run.calls.map(call=>call.url)).toContain('http://10.9.0.2:9999/health');
});

test('status is unhealthy without a console, and the text form names what it read',async()=>{
 const {root}=installation({console:false});
 const run=harness(root,{container:true});
 expect(await main(['status'],run.deps)).toBe(EXIT.failed);
 const text=run.out.join('\n');
 expect(text).toContain('Docker control-plane container');
 expect(text).toContain('Console        not running');
 expect(text).toContain('Acme/shop/production');
 expect(text).toContain('last backup 20260923T030000Z');
 expect(text).toContain('1 not delivered');
 expect(run.runs).toEqual([]);
});

test('status degrades when the catalog cannot be read and says how to run it',async()=>{
 const root=mkdtempSync(join(tmpdir(),'sbarbase-cli-'));roots.push(root);
 mkdirSync(join(root,'.lab','upstream'),{recursive:true});
 writeFileSync(join(root,'.lab','upstream','control.sqlite'),'not a database');
 const run=harness(root);
 expect(await main(['status'],run.deps)).toBe(EXIT.failed);
 expect(run.out.join('\n')).toContain('sudo -u sbarbase');
 expect(await main(['environments'],harness(root).deps)).toBe(EXIT.refused);
});

test('environments lists every environment from the catalog, read only',async()=>{
 const {root,production,runtime}=installation();
 const run=harness(root);
 expect(await main(['environments','--json'],run.deps)).toBe(EXIT.ok);
 const rows=JSON.parse(run.out.join('\n'));
 expect(rows).toHaveLength(2);
 expect(rows.find((row:any)=>row.id===production)).toEqual({id:production,client:'Acme',project:'shop',name:'production',runtime,state:'succeeded'});
 const text=harness(root);
 expect(await main(['environments'],text.deps)).toBe(EXIT.ok);
 expect(text.out.join('\n')).toContain(`Acme/shop/production  ${production}  ${runtime}  succeeded`);
});

test('backups: the last complete backup ignores an interrupted one',()=>{
 const {root,runtime}=installation();
 expect(backupsOf(root,runtime)).toEqual(['20260922T030000Z','20260923T030000Z']);
 expect(backupsOf(root,'e_'+'0'.repeat(24))).toEqual([]);
});

test('backup now and backups list delegate to lab/backup.py, passing its exit code through',async()=>{
 const {root,runtime,production}=installation();
 const all=harness(root,{env:{SBARBASE_BACKUP_KEEP:'5'}});
 expect(await main(['backup','now'],all.deps)).toBe(0);
 expect(all.runs).toEqual([['/usr/bin/python3','lab/backup.py','create','all','--keep','5']]);
 const one=harness(root,{code:1});
 expect(await main(['backup','now','shop/production','--keep','3'],one.deps)).toBe(1);
 expect(one.runs).toEqual([['/usr/bin/python3','lab/backup.py','create',production,'--keep','3']]);
 const byRuntime=harness(root);
 await main(['backup','now',runtime],byRuntime.deps);
 expect(byRuntime.runs).toEqual([['/usr/bin/python3','lab/backup.py','create',runtime]]);
 const listing=harness(root);
 expect(await main(['backups','list'],listing.deps)).toBe(0);
 await main(['backups','list',runtime],listing.deps);
 expect(listing.runs).toEqual([['/usr/bin/python3','lab/backup.py','list'],['/usr/bin/python3','lab/backup.py','list',runtime]]);
 expect(await main(['backup','now','--keep','0'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['backup','later'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['backup','now','nothing/here'],harness(root).deps)).toBe(EXIT.refused);
});

test('restore confirms at a terminal, refuses without one, and delegates with --yes',async()=>{
 const {root,runtime}=installation();
 const headless=harness(root);
 expect(await main(['restore',runtime,'20260923T030000Z'],headless.deps)).toBe(EXIT.refused);
 expect(headless.runs).toEqual([]);
 expect(headless.prompts).toEqual([]);
 const declined=harness(root,{tty:true,answers:['yes']});
 expect(await main(['restore',runtime,'20260923T030000Z'],declined.deps)).toBe(EXIT.refused);
 expect(declined.runs).toEqual([]);
 expect(declined.err.join('\n')).toContain('will be gone');
 const confirmed=harness(root,{tty:true,answers:['20260923T030000Z']});
 expect(await main(['restore',runtime,'20260923T030000Z'],confirmed.deps)).toBe(0);
 expect(confirmed.runs).toEqual([['/usr/bin/python3','lab/backup.py','restore',runtime,'20260923T030000Z']]);
 const forced=harness(root,{code:1});
 expect(await main(['restore',runtime,'20260923T030000Z','--yes'],forced.deps)).toBe(1);
 expect(forced.runs).toHaveLength(1);
 expect(await main(['restore',runtime,'../etc','--yes'],harness(root).deps)).toBe(EXIT.usage);
});

test('upgrade delegates to lab/upgrade.py and defaults to check',async()=>{
 const {root}=installation();
 const run=harness(root);
 await main(['upgrade'],run.deps);
 await main(['upgrade','start','--to','origin/main'],run.deps);
 await main(['upgrade','rollback'],run.deps);
 expect(run.runs).toEqual([['/usr/bin/python3','lab/upgrade.py','check'],
  ['/usr/bin/python3','lab/upgrade.py','start','--to','origin/main'],['/usr/bin/python3','lab/upgrade.py','rollback']]);
 expect(await main(['upgrade','status','--to','x'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['upgrade','sideways'],harness(root).deps)).toBe(EXIT.usage);
});

test('logs: journalctl for the supervisor, docker logs for a service, a hint inside the container',async()=>{
 const {root,runtime}=installation();
 const systemd=harness(root,{unit:true});
 await main(['logs'],systemd.deps);
 await main(['logs','auth','shop/production','-n','20','--follow'],systemd.deps);
 await main(['logs','storage'],systemd.deps);
 await main(['logs','database'],systemd.deps);
 expect(systemd.runs).toEqual([['journalctl','--unit','sbarbase.service','--lines','100','--no-pager'],
  ['docker','logs','--tail','20','--follow',`sbarbase-durable-${runtime}-auth`],
  ['docker','logs','--tail','100','sbarbase-durable-storage'],['docker','logs','--tail','100','sbarbase-durable-db']]);
 const container=harness(root,{container:true});
 expect(await main(['logs'],container.deps)).toBe(EXIT.usage);
 expect(container.err.join('\n')).toContain('docker compose logs sbarbase');
 expect(await main(['logs'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['logs','rest'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['logs','storage',runtime],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['logs','kernel'],harness(root).deps)).toBe(EXIT.usage);
});

function api(state:{organization:string;project:string;environment:string;keys:{id:string;kind:string;created_at:number;revoked_at:number|null}[];failRevoke?:boolean;conflict?:string}) {
 return (call:Call)=>{
  const path=call.url.slice((CONSOLE+'/management/v1').length);
  if(call.headers.authorization!=='Bearer '+TOKEN)return Response.json({message:'Authentication required'},{status:401});
  if(path==='/organizations')return Response.json({data:[{id:state.organization,name:'Acme',role:'owner'}],operator:true});
  if(path===`/organizations/${state.organization}/projects`)return Response.json({data:[{id:state.project,name:'shop'}]});
  if(path===`/projects/${state.project}/environments`&&call.method==='POST') {
   if(state.conflict)return Response.json({message:state.conflict},{status:409});
   return Response.json({id:'11111111-2222-4333-8444-555555555555',state:'queued'},{status:202});
  }
  if(path===`/environments/${state.environment}/keys`&&call.method==='GET')return Response.json({data:state.keys});
  if(path===`/environments/${state.environment}/keys`&&call.method==='POST')
   return Response.json({id:'99999999-2222-4333-8444-555555555555',token:NEW_KEY},{status:201});
  const revoke=path.match(/^\/environments\/[^/]+\/keys\/([^/]+)$/);
  if(revoke&&call.method==='DELETE')return state.failRevoke?Response.json({message:'Key operation failed'},{status:500}):Response.json({revoked:true});
  if(path===`/environments/${state.environment}/share`) {
   if(call.method==='GET')return Response.json({data:{share:4,default:4,ceiling:32,operator:true,total:64,allocated:12}});
   const wanted=JSON.parse(call.body!).share;
   if(wanted>40)return Response.json({message:'Invalid share'},{status:400});
   if(wanted>20)return Response.json({message:'Shares exceed gateway capacity'},{status:409});
   return Response.json({data:{share:wanted,default:4,ceiling:32,operator:true,total:64,allocated:12-4+wanted}});
  }
  if(path===`/environments/${state.environment}/studio`)return Response.json({data:{desired:call.method==='POST'?'running':'stopped'}},{status:202});
  return undefined;
 };
}

test('add-environment signs in and posts exactly what the console posts, then signs out',async()=>{
 const {root,organization,project,production}=installation();
 const run=harness(root,{env:{SBARBASE_EMAIL:'owner@example.com'},stdin:PASSWORD+'\n',
  routes:api({organization,project,environment:production,keys:[]})});
 expect(await main(['add-environment','Acme/shop','preview','--password-stdin'],run.deps)).toBe(EXIT.ok);
 const token=run.calls.find(call=>call.url.includes('/management/auth/v1/token'))!;
 expect(token.headers.apikey).toBe(managementPublishableKey);
 expect(JSON.parse(token.body!)).toEqual({email:'owner@example.com',password:PASSWORD});
 const create=run.calls.find(call=>call.method==='POST'&&call.url.endsWith(`/projects/${project}/environments`))!;
 expect(create.headers['content-type']).toBe('application/json');
 expect(JSON.parse(create.body!)).toEqual({name:'preview'});
 expect(run.calls.at(-1)!.url).toBe(CONSOLE+'/management/auth/v1/logout');
 expect(run.out.join('\n')).toContain('Acme/shop/preview is queued');
 expect(run.printed()).not.toContain(TOKEN);
 expect(run.printed()).not.toContain(PASSWORD);
 expect(run.runs).toEqual([]);
});

test('add-environment reports the API refusals in plain words and signs out anyway',async()=>{
 const {root,organization,project,production}=installation();
 const run=harness(root,{tty:true,answers:['owner@example.com',PASSWORD],
  routes:api({organization,project,environment:production,keys:[],conflict:'Environment capacity reached'})});
 expect(await main(['add-environment',project,'preview'],run.deps)).toBe(EXIT.failed);
 expect(run.prompts).toEqual([{question:'Operator email: ',secret:false},{question:'Password: ',secret:true}]);
 expect(run.err.join('\n')).toContain('environment limit');
 expect(run.calls.at(-1)!.url).toBe(CONSOLE+'/management/auth/v1/logout');
 const missing=harness(root,{tty:true,answers:['owner@example.com',PASSWORD],routes:api({organization,project,environment:production,keys:[]})});
 expect(await main(['add-environment','nothing','preview'],missing.deps)).toBe(EXIT.refused);
});

test('sign-in refusals: no terminal, a wrong password, a loose operator file, no console',async()=>{
 const {root,organization,project,production}=installation();
 const routes=api({organization,project,environment:production,keys:[]});
 const headless=harness(root,{routes});
 expect(await main(['add-environment','shop','x'],headless.deps)).toBe(EXIT.usage);
 expect(headless.calls).toEqual([]);
 const wrong=harness(root,{routes,env:{SBARBASE_EMAIL:'a@example.com'},stdin:'nope\n'});
 expect(await main(['add-environment','shop','x','--password-stdin'],wrong.deps)).toBe(EXIT.failed);
 expect(wrong.err.join('\n')).toContain('Sign-in failed');
 expect(wrong.printed()).not.toContain('nope');
 const file=join(root,'operator.json');
 writeFileSync(file,JSON.stringify({email:'owner@example.com',password:PASSWORD,organization:'Acme'}));
 chmodSync(file,0o644);
 const loose=harness(root,{routes});
 expect(await main(['add-environment','shop','x','--operator-file',file],loose.deps)).toBe(EXIT.refused);
 expect(loose.calls).toEqual([]);
 chmodSync(file,0o600);
 const fromFile=harness(root,{routes});
 expect(await main(['add-environment','shop','x','--operator-file',file],fromFile.deps)).toBe(EXIT.ok);
 const down=installation({console:false});
 expect(await main(['add-environment','shop','x','--operator-file',file],harness(down.root).deps)).toBe(EXIT.failed);
});

test('rotate-key issues, shows the new key once on stdout, then revokes the one old key',async()=>{
 const {root,organization,project,production}=installation();
 const old={id:'aaaaaaaa-2222-4333-8444-555555555555',kind:'publishable',created_at:1,revoked_at:null};
 const run=harness(root,{env:{SBARBASE_EMAIL:'owner@example.com'},stdin:PASSWORD,
  routes:api({organization,project,environment:production,keys:[old,{...old,id:'bbbbbbbb-2222-4333-8444-555555555555',revoked_at:5}]})});
 expect(await main(['rotate-key','Acme/shop/production','--password-stdin'],run.deps)).toBe(EXIT.ok);
 const writes=run.calls.filter(call=>call.url.includes('/keys')).map(call=>call.method+' '+call.url.slice(call.url.indexOf('/environments')));
 expect(writes).toEqual([`GET /environments/${production}/keys`,`POST /environments/${production}/keys`,
  `DELETE /environments/${production}/keys/${old.id}`]);
 expect(run.out).toEqual([NEW_KEY]);
 expect(run.err.join('\n')).not.toContain(NEW_KEY);
 expect(run.printed()).not.toContain(TOKEN);
});

test('rotate-key refuses to guess between several active keys, and names a failed revoke',async()=>{
 const {root,organization,project,production,runtime}=installation();
 const keys=['aaaaaaaa','cccccccc'].map(prefix=>({id:prefix+'-2222-4333-8444-555555555555',kind:'publishable',created_at:1,revoked_at:null}));
 const ambiguous=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes:api({organization,project,environment:production,keys})});
 expect(await main(['rotate-key',runtime,'--password-stdin'],ambiguous.deps)).toBe(EXIT.refused);
 expect(ambiguous.calls.some(call=>call.method==='POST'&&call.url.endsWith('/keys'))).toBe(false);
 expect(ambiguous.err.join('\n')).toContain('--revoke');
 const chosen=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes:api({organization,project,environment:production,keys,failRevoke:true})});
 expect(await main(['rotate-key',production,'--revoke','cccccccc','--password-stdin'],chosen.deps)).toBe(EXIT.failed);
 expect(chosen.out).toEqual([NEW_KEY]);
 expect(chosen.err.join('\n')).toContain('still active');
 expect(chosen.calls.some(call=>call.method==='DELETE'&&call.url.endsWith(keys[1]!.id))).toBe(true);
});

test('studio start and stop go through the management API, never straight to lab/studio.py',async()=>{
 const {root,organization,project,production}=installation();
 const run=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes:api({organization,project,environment:production,keys:[]})});
 expect(await main(['studio','start','shop/production','--password-stdin'],run.deps)).toBe(EXIT.ok);
 expect(await main(['studio','stop',production,'--password-stdin'],run.deps)).toBe(EXIT.ok);
 expect(run.calls.filter(call=>call.url.endsWith('/studio')).map(call=>call.method)).toEqual(['POST','DELETE']);
 expect(run.runs).toEqual([]);
 expect(await main(['studio','open',production],harness(root).deps)).toBe(EXIT.usage);
});

test('share reads and sets the gateway share through the management route, and names its refusals',async()=>{
 const {root,organization,project,production}=installation();
 const routes=api({organization,project,environment:production,keys:[]});
 const read=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes});
 expect(await main(['share','shop/production','--password-stdin'],read.deps)).toBe(EXIT.ok);
 expect(read.out).toEqual(['Share 4 (default 4, ceiling 32)','Allocated 12 of 64 across every ready environment']);
 const set=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes});
 expect(await main(['share',production,'8','--password-stdin'],set.deps)).toBe(EXIT.ok);
 const put=set.calls.find(call=>call.method==='PUT')!;
 expect(put.url).toBe(`${CONSOLE}/management/v1/environments/${production}/share`);
 expect(put.headers['content-type']).toBe('application/json');
 expect(JSON.parse(put.body!)).toEqual({share:8});
 expect(set.out[0]).toContain('Share set to 8');
 const full=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes});
 expect(await main(['share',production,'30','--password-stdin'],full.deps)).toBe(EXIT.failed);
 expect(full.err.join('\n')).toContain('gateway capacity');
 const invalid=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,routes});
 expect(await main(['share',production,'50','--password-stdin'],invalid.deps)).toBe(EXIT.failed);
 expect(invalid.err.join('\n')).toContain('whole number');
 expect(await main(['share',production,'0'],harness(root).deps)).toBe(EXIT.usage);
 expect(await main(['share',production,'two'],harness(root).deps)).toBe(EXIT.usage);
 expect(read.printed()+set.printed()).not.toContain(TOKEN);
});

test('share: a member who is not an installation operator is told so in plain words',async()=>{
 const {root,production}=installation();
 const run=harness(root,{env:{SBARBASE_EMAIL:'o@example.com'},stdin:PASSWORD,
  routes:()=>Response.json({message:'Forbidden'},{status:403})});
 expect(await main(['share',production,'8','--password-stdin'],run.deps)).toBe(EXIT.failed);
 expect(run.err.join('\n')).toContain('no permission');
});

test('backup now keeps as many backups as the daily run, reading the unit when the shell has no setting',async()=>{
 const {root}=installation();
 const unit=harness(root,{unit:true,unitEnvironment:'Environment=HOME=/home/sbarbase "PATH=/usr/bin:/bin" SBARBASE_BACKUP_KEEP=14\n'});
 expect(await main(['backup','now'],unit.deps)).toBe(0);
 expect(unit.runs).toEqual([['systemctl','show','--property','Environment','sbarbase.service'],
  ['/usr/bin/python3','lab/backup.py','create','all','--keep','14']]);
 const shell=harness(root,{unit:true,env:{SBARBASE_BACKUP_KEEP:'9'}});
 await main(['backup','now'],shell.deps);
 expect(shell.runs).toEqual([['/usr/bin/python3','lab/backup.py','create','all','--keep','9']]);
 const unset=harness(root,{unit:true});
 await main(['backup','now'],unset.deps);
 expect(unset.runs.at(-1)).toEqual(['/usr/bin/python3','lab/backup.py','create','all']);
 expect(unset.err.join('\n')).toContain('default of 7');
 const container=harness(root,{container:true,unit:true});
 await main(['backup','now'],container.deps);
 expect(container.runs).toEqual([['/usr/bin/python3','lab/backup.py','create','all']]);
 expect(await main(['backup','now'],harness(root,{unit:true,unitEnvironment:'Environment=SBARBASE_BACKUP_KEEP=zero\n'}).deps)).toBe(EXIT.usage);
});
