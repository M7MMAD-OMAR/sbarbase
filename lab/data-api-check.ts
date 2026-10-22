// Live check of per environment Data API settings (lab/data_api.py, docs/engineering/DATA-API-SCHEMAS.md).
//
// Starts a disposable Supabase Postgres container and the pinned PostgREST against it, with two
// environment databases built from the runtime's own SQL (developer_sql, developer_grants_sql and
// lab/data_api.py). It checks that: PostgREST serves only `public` until the settings are written;
// the settings it applies from the environment's database expose another schema without a
// restart, in the given order, with the row limit; the developer login cannot change those
// settings, nor rename or drop an exposed schema (which would make PostgREST answer 503 to every
// request), while it still renames its other schemas; `public` stays (the platform owns it, and the
// guard covers it too when nothing is set); a
// schema missing at start is left out so the rest is served, and with none left an empty platform
// schema is served rather than `public`; Studio's view reads the same values;
// and a reset returns to the container's defaults. Removes its containers and network at the end.
// Needs Docker and about 1.5 GiB.
//
//   bun lab/data-api-check.ts [evidence.json]
//
// Prints one JSON object: {"total", "failed", "skipped", "checks"}.
import {spawn,spawnSync} from 'node:child_process';
import {readFileSync,writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';

const pins=JSON.parse(readFileSync(new URL('./distro-image.lock.json',import.meta.url),'utf8'));
const rest=JSON.parse(readFileSync(new URL('./images.lock.json',import.meta.url),'utf8')).rest;
const image=pins.digests[0] as string,restImage=rest.digests[0] as string;
const evidence=process.argv[2];
const suffix=randomBytes(3).toString('hex');
const name='sbarbase-data-api-check-'+suffix,restName=name+'-rest',network=name;
const runtimes=['e_'+randomBytes(12).toString('hex'),'e_'+randomBytes(12).toString('hex')];
const passwords=runtimes.map(()=>randomBytes(18).toString('base64url'));
const restPassword=randomBytes(18).toString('hex'),jwt=randomBytes(32).toString('hex');
const [a,b]=runtimes as [string,string];
const results:{check:string;result:'pass'|'fail';detail?:string}[]=[];
const docker=(...args:string[])=>{const out=spawnSync('docker',args,{encoding:'utf8'});if(out.status)throw new Error(out.stderr.slice(0,500));return out.stdout.trim();};
const run=(user:string,database:string,sql:string,password?:string)=>spawnSync('docker',['exec','-i',...(password?['-e','PGPASSWORD='+password]:[]),name,
 'psql','-X','-qAt','-v','ON_ERROR_STOP=1',...(password?['-h','127.0.0.1']:[]),'-U',user,'-d',database],{input:sql,encoding:'utf8'});
const superuser=(database:string,sql:string)=>{const out=run('supabase_admin',database,sql);if(out.status)throw new Error(out.stderr.slice(0,800));return out.stdout.trim();};
const developer=(sql:string,runtime=a)=>run(`${runtime}_developer`,runtime,sql,passwords[runtimes.indexOf(runtime)]);
const check=async(label:string,work:()=>boolean|string|Promise<boolean|string>)=>{
 try{const value=await work();results.push(value===true?{check:label,result:'pass'}:{check:label,result:'fail',detail:String(value)});}
 catch(error){results.push({check:label,result:'fail',detail:(error as Error).message.slice(0,300)});}
};
const works=(sql:string,runtime=a)=>{const out=developer(sql,runtime);return out.status===0||out.stderr.slice(0,300);};
const refused=(sql:string,expect:RegExp,runtime=a)=>{const out=developer(sql,runtime);return out.status!==0&&expect.test(out.stderr)||`status ${out.status}: ${out.stderr.slice(0,300)}`;};
const python=(code:string)=>{
 const out=spawnSync('/usr/bin/python3',['-c',`import sys,json;sys.path.insert(0,'lab');import durable_runtime as d;import data_api\n${code}`],{encoding:'utf8'});
 if(out.status)throw new Error(out.stderr.slice(0,800));
 return out.stdout;
};
// The guard first, as provisioning installs it (provision_database), then the developer grants.
const generate=(runtime:string,password:string)=>python(`print(d.Runtime.developer_sql(None,${JSON.stringify(runtime)},${JSON.stringify(password)}))
print('-- grants')
print(data_api.guard_sql(${JSON.stringify(runtime)})+d.Runtime.developer_grants_sql(None,${JSON.stringify(runtime)}))`).split('-- grants') as [string,string];
type Settings={schemas:string[];extra_search_path:string[];max_rows:number|null};
const roleSql=(settings:Settings,runtime=a)=>python(`print(data_api.SAFE+data_api.settings_sql(${JSON.stringify(runtime)},json.loads(${JSON.stringify(JSON.stringify(settings))}))+data_api.RELOAD_SQL)`);
let port='';
const token=(role:string)=>{
 const encode=(value:object)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 const head=encode({alg:'HS256',typ:'JWT'}),body=encode({role,exp:Math.floor(Date.now()/1000)+3600});
 return `${head}.${body}.`+new Bun.CryptoHasher('sha256',jwt).update(`${head}.${body}`).digest('base64url');
};
const get=async(path:string,profile?:string)=>{
 // A connection refused or closed while PostgREST starts or reloads counts as no answer yet.
 try{
  const response=await fetch(`http://127.0.0.1:${port}${path}`,{headers:{authorization:'Bearer '+token('anon'),...(profile?{'accept-profile':profile}:{})}});
  return {status:response.status,body:await response.text()};
 }catch(error){return {status:0,body:(error as Error).message};}
};
/** A function call by POST: PostgREST runs a GET call in a read-only transaction, which would refuse a write for that reason alone. */
const postRpc=async(name:string)=>{
 try{const r=await fetch(`http://127.0.0.1:${port}/rpc/${name}`,{method:'POST',headers:{authorization:'Bearer '+token('anon'),'content-type':'application/json'},body:'{}'});return {status:r.status,body:await r.text()};}
 catch(error){return {status:0,body:(error as Error).message};}
};
/** PostgREST reloads in the background after NOTIFY; wait for the answer to settle. */
const settle=async(path:string,profile:string|undefined,want:number)=>{
 let last={status:0,body:''};
 for(let i=0;i<40;i++){last=await get(path,profile);if(last.status===want)return last;await Bun.sleep(250);}
 return last;
};
const expectStatus=async(path:string,profile:string|undefined,want:number,body?:RegExp)=>{
 const answer=await settle(path,profile,want);
 return answer.status===want&&(!body||body.test(answer.body))||`${answer.status} ${answer.body.slice(0,200)}`;
};

try {
 docker('network','create','--label','io.sbarbase.owner=data-api-check',network);
 docker('run','-d','--rm','--name',name,'--network',network,'--label','io.sbarbase.owner=data-api-check','-e','POSTGRES_PASSWORD='+randomBytes(12).toString('hex'),image);
 // The image initialises, then restarts the server once: ready means answering ten seconds in a row.
 for(let i=0,streak=0;streak<10;i++){streak=run('supabase_admin','postgres','SELECT 1').status===0?streak+1:0;if(i>180)throw new Error('PostgreSQL did not start');await Bun.sleep(1000);}
 for(const [index,runtime] of runtimes.entries()){
  const [developerSql,grantSql]=generate(runtime,passwords[index]!);
  superuser('postgres',`CREATE ROLE ${runtime}_auth NOLOGIN NOINHERIT; CREATE ROLE ${runtime}_storage NOLOGIN NOINHERIT; CREATE DATABASE ${runtime}; REVOKE ALL ON DATABASE ${runtime} FROM PUBLIC;
   CREATE ROLE ${runtime}_rest LOGIN NOINHERIT PASSWORD '${restPassword}'; GRANT anon, authenticated, service_role TO ${runtime}_rest; GRANT CONNECT ON DATABASE ${runtime} TO ${runtime}_rest;`);
  // What provisioning leaves on the REST login (durable_runtime.py REST_PINS).
  superuser('postgres',python(`print(d.REST_PINS.format(e=${JSON.stringify(runtime)}))`));
  superuser(runtime,`REVOKE CREATE ON SCHEMA public FROM PUBLIC; CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION ${runtime}_auth; CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION ${runtime}_storage; CREATE SCHEMA IF NOT EXISTS extensions;`);
  superuser('postgres',developerSql);
  superuser(runtime,grantSql);
 }
 // The application's own schemas, made by its developer login as a migration would.
 const made=developer(`CREATE TABLE public.a(x int); INSERT INTO public.a VALUES (1),(2),(3);
  CREATE SCHEMA i2s; CREATE TABLE i2s.b(y int); INSERT INTO i2s.b VALUES (2);
  CREATE SCHEMA i2s_private; CREATE SCHEMA scratch;
  GRANT USAGE ON SCHEMA i2s TO anon, authenticated, service_role; GRANT SELECT ON ALL TABLES IN SCHEMA public, i2s TO anon;`);
 if(made.status)throw new Error(made.stderr.slice(0,500));
 // What every REST container is started with (data_api.CONTAINER_ENV): the settings function and the empty schema.
 const containerEnv=Object.entries(JSON.parse(python('print(json.dumps(data_api.CONTAINER_ENV))'))).flatMap(([key,value])=>['-e',`${key}=${value}`]);
 docker('run','-d','--rm','--name',restName,'--network',network,'--label','io.sbarbase.owner=data-api-check','-p','127.0.0.1::3000',
  '-e',`PGRST_DB_URI=postgres://${a}_rest:${restPassword}@${name}:5432/${a}`,'-e','PGRST_DB_ANON_ROLE=anon',
  '-e','PGRST_JWT_SECRET='+jwt,'-e','PGRST_DB_POOL=3',...containerEnv,restImage);
 port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
 for(let i=0;(await get('/a')).status!==200;i++){if(i>120)throw new Error('PostgREST did not start');await Bun.sleep(250);}

 await check('with no settings PostgREST serves public only',async()=>{
  const ok=await expectStatus('/a',undefined,200);
  return ok===true?await expectStatus('/b','i2s',406,/PGRST106/):ok;
 });
 await check('the settings in the environment database expose i2s without a restart',async()=>{
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public','extensions'],max_rows:null}));
  return expectStatus('/b','i2s',200,/"y":2/);
 });
 await check('public is still the default schema',()=>expectStatus('/a',undefined,200,/"x":1/));
 await check('a schema that is not listed stays refused',()=>expectStatus('/x','i2s_private',406,/PGRST106/));
 await check('the first schema listed is the default',async()=>{
  superuser(a,roleSql({schemas:['i2s','public'],extra_search_path:['public'],max_rows:null}));
  return expectStatus('/b',undefined,200,/"y":2/);
 });
 await check('the row limit applies',async()=>{
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:1}));
  const answer=await settle('/a',undefined,200);
  return answer.status===200&&JSON.parse(answer.body).length===1||`${answer.status} ${answer.body.slice(0,200)}`;
 });
 await check('Studio reads the same values PostgREST serves',()=>{
  const lines=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  const out=python(`print(json.dumps(data_api.serving(${JSON.stringify(lines)})))`);
  const served=JSON.parse(out);
  return JSON.stringify(served)===JSON.stringify({schemas:['public','i2s'],extra_search_path:['public'],max_rows:1})||out;
 });
 await check('schema names holding a space or a newline do not make other names look present',()=>{
  // A tenant's schema "x" + newline + "phantom" once made `phantom` look present, and PostgREST then
  // answered every request with 503 at every start.
  const made=works('CREATE SCHEMA "ghost x"; CREATE SCHEMA "x\nphantom";');
  if(made!==true)return made;
  const out=JSON.parse(python(`settings={'schemas':['public','ghost','phantom'],'extra_search_path':['public'],'max_rows':None}
print(json.dumps(data_api.effective(settings,data_api.existing(sys.argv[1] if len(sys.argv)>1 else ''))[1]))`.replace("sys.argv[1] if len(sys.argv)>1 else ''",
   JSON.stringify(superuser(a,python(`print(data_api.present_sql({'schemas':['public','ghost','phantom'],'extra_search_path':['public'],'max_rows':None}))`))))));
  return JSON.stringify(out)==='["ghost","phantom"]'||JSON.stringify(out);
 });
 await check('the developer login cannot put an extension into the empty platform schema',()=>{
  const created=refused('CREATE EXTENSION pg_trgm SCHEMA sbarbase_empty;',/stays empty|permission denied/);
  if(created!==true)return created;
  const moved=refused('CREATE EXTENSION IF NOT EXISTS pg_trgm; ALTER EXTENSION pg_trgm SET SCHEMA sbarbase_empty;',/stays empty|permission denied|must be owner/);
  if(moved!==true)return moved;
  const count=superuser(a,"SELECT count(*) FROM pg_proc WHERE pronamespace = 'sbarbase_empty'::regnamespace");
  return count==='0'||count;
 });
 await check('a platform write naming a schema that is gone writes nothing',()=>{
  const before=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  const out=run('supabase_admin',a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','nowhere'],'extra_search_path':['public'],'max_rows':None}))`));
  const after=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  return out.status!==0&&out.stderr.includes(python('print(data_api.RACE)').trim())&&before===after||`status ${out.status} ${out.stderr.slice(0,200)}`;
 });
 await check('a drop that holds the lock first makes the platform write see the schema gone',async()=>{
  const made=works('CREATE SCHEMA racer;');
  if(made!==true)return made;
  // Built first: starting Python takes long enough, on a loaded host, for the drop to finish before the write is tried.
  const script=python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s','racer'],'extra_search_path':['public'],'max_rows':None}))`);
  // The developer drops `racer` and keeps its transaction open; the guard holds the lock meanwhile.
  const drop=Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+passwords[0],name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],
   {stdin:new TextEncoder().encode('BEGIN; DROP SCHEMA racer; SELECT pg_sleep(5); COMMIT;'),stdout:'pipe',stderr:'pipe'});
  await Bun.sleep(1500);
  // The platform only tries the lock: busy while the drop holds it, then the schema is seen gone.
  const busy=run('supabase_admin',a,script);
  const dropped=await drop.exited;
  const retried=run('supabase_admin',a,script);
  const serving=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  return dropped===0&&busy.stderr.includes('sbarbase_data_api_busy')&&retried.status!==0&&retried.stderr.includes('schema_gone')&&!serving.includes('racer')
   ||`drop ${dropped} busy ${busy.stderr.slice(0,80)} retried ${retried.status} ${retried.stderr.slice(0,120)} ${serving}`;
 });
 await check('a platform write that holds the lock first makes a concurrent drop wait and be refused',async()=>{
  const made=works('CREATE SCHEMA racer2;');
  if(made!==true)return made;
  const settings=JSON.stringify({schemas:['public','i2s','racer2'],extra_search_path:['public'],max_rows:null});
  const script=python(`print(data_api.write_sql(${JSON.stringify(a)},json.loads(${JSON.stringify(settings)})))`).replace(' COMMIT;',' SELECT pg_sleep(3); COMMIT;');
  const write=Bun.spawn(['docker','exec','-i',name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',a],{stdin:new TextEncoder().encode(script),stdout:'pipe',stderr:'pipe'});
  await Bun.sleep(1000);
  const drop=developer('DROP SCHEMA racer2;');
  const wrote=await write.exited;
  const ok=wrote===0&&drop.status!==0&&/exposed through the Data API/.test(drop.stderr);
  // Back to the settings the checks after this one expect.
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:1}));
  return ok||`write ${wrote} drop ${drop.status} ${drop.stderr.slice(0,200)}`;
 });
 await check('a schema named in the extra search path cannot be dropped either',()=>{
  const made=works('CREATE SCHEMA pathonly;');
  if(made!==true)return made;
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public','pathonly'],max_rows:1}));
  const out=refused('DROP SCHEMA pathonly;',/schema pathonly is exposed/);
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:1}));
  return out;
 });
 await check('a tenant session holding the lock makes the platform refuse at once, not wait',async()=>{
  const hold=Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+passwords[0],name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],
   {stdin:new TextEncoder().encode(`SELECT pg_advisory_lock(${python('print(data_api.LOCK_KEY)').trim()}); SELECT pg_sleep(6);`),stdout:'pipe',stderr:'pipe'});
  await Bun.sleep(1000);
  // Only the database call is timed, not Python's start.
  const script=python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':1}))`);
  const started=Date.now();
  const write=run('supabase_admin',a,script);
  const took=Date.now()-started;
  await hold.exited;
  return write.status!==0&&write.stderr.includes('sbarbase_data_api_busy')&&took<2500||`status ${write.status} ${took} ms ${write.stderr.slice(0,160)}`;
 });
 await check('a start ends the tenant session that holds the lock, then writes',async()=>{
  const hold=Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+passwords[0],name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],
   {stdin:new TextEncoder().encode(`SELECT pg_advisory_lock(${python('print(data_api.LOCK_KEY)').trim()}); SELECT pg_sleep(20);`),stdout:'pipe',stderr:'pipe'});
  await Bun.sleep(1000);
  const ended=superuser(a,python('print(data_api.release_sql())'));
  const holder=await Promise.race([hold.exited,Bun.sleep(5000).then(()=>'still running')]);
  const write=run('supabase_admin',a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':1}))`));
  if(holder==='still running')hold.kill();
  return ended==='1'&&holder!=='still running'&&write.status===0||`ended ${ended} holder ${holder} write ${write.status} ${write.stderr.slice(0,160)}`;
 });
 await check('a start claims the lock past a holder and the sessions queued behind it',async()=>{
  const key=python('print(data_api.LOCK_KEY)').trim();
  const session=()=>Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+passwords[0],name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],
   {stdin:new TextEncoder().encode(`SELECT pg_advisory_lock(${key}); SELECT pg_sleep(20);`),stdout:'pipe',stderr:'pipe'});
  const sessions=[session()];await Bun.sleep(700);
  for(let i=0;i<3;i++)sessions.push(session());
  await Bun.sleep(1200);
  const started=Date.now();
  const write=run('supabase_admin',a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':1},claim=True))`));
  const took=Date.now()-started;
  const left=await Promise.all(sessions.map(item=>Promise.race([item.exited,Bun.sleep(4000).then(()=>'running')])));
  for(const item of sessions)item.kill();
  return write.status===0&&took<9000&&left.every(code=>code!=='running')||`write ${write.status} ${took} ms ${JSON.stringify(left)} ${write.stderr.slice(0,160)}`;
 });
 await check('the guard refuses a schema drop at repeatable read',()=>refused('BEGIN ISOLATION LEVEL REPEATABLE READ; DROP SCHEMA i2s_private; COMMIT;',/read committed isolation level/));
 await check('the developer login cannot change the REST login settings',()=>
  refused(`ALTER ROLE ${a}_rest IN DATABASE ${a} SET pgrst.jwt_secret = 'x';`,/permission denied to alter role/));
 await check('the developer login cannot rename an exposed schema',()=>refused('ALTER SCHEMA i2s RENAME TO i2s_old;',/exposed through the Data API/));
 await check('the developer login cannot drop an exposed schema',()=>refused('DROP SCHEMA i2s CASCADE;',/exposed through the Data API/));
 await check('the developer login cannot drop public while it is exposed',()=>refused('DROP SCHEMA public CASCADE;',/exposed through the Data API|must be owner of schema public/));
 await check('the API still serves after the refused changes',()=>expectStatus('/b','i2s',200,/"y":2/));
 await check('the developer login still renames and drops schemas that are not exposed',()=>works('ALTER SCHEMA scratch RENAME TO scratch_two; DROP SCHEMA scratch_two;'));
 await check('with no settings public is protected in another environment',()=>refused('ALTER SCHEMA public RENAME TO elsewhere;',/schema public is exposed|must be owner of schema public/,b));
 await check('the developer login cannot replace or disable the guard',()=>{
  const replaced=refused("CREATE OR REPLACE FUNCTION sbarbase_guard.exposed_schemas() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$;",/permission denied/);
  return replaced===true?refused('ALTER EVENT TRIGGER sbarbase_exposed_schemas DISABLE;',/must be owner|permission denied/):replaced;
 });
 await check('a missing exposed schema makes PostgREST answer 503 to every request (why the guard exists)',async()=>{
  // The superuser passes the guard, as a restore would.
  superuser(a,"ALTER SCHEMA i2s RENAME TO i2s_moved; NOTIFY pgrst, 'reload schema';");
  return expectStatus('/a',undefined,503,/PGRST002/);
 });
 await check('at start a missing schema is left out and the rest is served',async()=>{
  const existing=superuser(a,python(`print(data_api.present_sql({'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  const effective=JSON.parse(python(`served,missing=data_api.effective({'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None},data_api.existing(${JSON.stringify(existing)}))
print(json.dumps({'served':served,'missing':missing}))`));
  if(JSON.stringify(effective.missing)!=='["i2s"]')return JSON.stringify(effective);
  superuser(a,roleSql(effective.served));
  return expectStatus('/a',undefined,200,/"x":1/);
 });
 await check('with every exposed schema gone the empty schema is served, not public',async()=>{
  // A hardened app that exposes only i2s, still renamed away by the step before: falling back to
  // public would publish what it left out.
  const existing=superuser(a,python(`print(data_api.present_sql({'schemas':['i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  const effective=JSON.parse(python(`served,missing=data_api.effective({'schemas':['i2s'],'extra_search_path':['public'],'max_rows':None},data_api.existing(${JSON.stringify(existing)}))
print(json.dumps(served))`));
  superuser(a,roleSql(effective));
  const empty=await expectStatus('/a',undefined,404);
  return empty===true?await expectStatus('/a','public',406,/PGRST106/):empty;
 });
 await check('a reset returns to the container defaults',async()=>{
  superuser(a,"ALTER SCHEMA i2s_moved RENAME TO i2s;");
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  const exposed=await expectStatus('/b','i2s',200);
  if(exposed!==true)return exposed;
  superuser(a,python(`print(data_api.reset_sql(${JSON.stringify(a)})+data_api.RELOAD_SQL)`));
  return expectStatus('/b','i2s',406,/PGRST106/);
 });
 await check('turned off, the API serves no table and public is not published',async()=>{
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':[],'extra_search_path':['public'],'max_rows':None})+data_api.RELOAD_SQL)`));
  const none=await expectStatus('/a',undefined,404);
  if(none!==true)return none;
  const unlisted=await expectStatus('/a','public',406,/PGRST106/);
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  return unlisted;
 });
 await check('functions the tenant plants in public never run as the superuser',()=>{
  // Better matches than pg_catalog's polymorphic unnest and count, for a platform query that would
  // resolve them through the superuser's search path. Each makes the developer login a superuser.
  const role=`${a}_developer`;
  const planted=works(`CREATE FUNCTION public.unnest(text[]) RETURNS SETOF text LANGUAGE plpgsql AS $f$ BEGIN
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN EXECUTE 'ALTER ROLE ${role} SUPERUSER'; END IF;
    RETURN QUERY SELECT x FROM pg_catalog.unnest($1) x; END $f$;
   CREATE FUNCTION public.planted_count(bigint, boolean) RETURNS bigint LANGUAGE plpgsql AS $f$ BEGIN
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN EXECUTE 'ALTER ROLE ${role} SUPERUSER'; END IF;
    RETURN $1 + 1; END $f$;
   CREATE AGGREGATE public.count(boolean) (SFUNC = public.planted_count, STYPE = bigint, INITCOND = '0');`);
  if(planted!==true)return planted;
  const settings=`{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None}`;
  for(const code of [`print(data_api.serving_sql(${JSON.stringify(a)}))`,`print(data_api.present_sql(${settings}))`,
   `print(data_api.guard_present_sql(${JSON.stringify(a)}))`,`print(data_api.guard_sql(${JSON.stringify(a)}))`,
   `print(data_api.write_sql(${JSON.stringify(a)},${settings}))`,`print(data_api.write_sql(${JSON.stringify(a)},${settings},claim=True))`])
   run('supabase_admin',a,python(code));
  const superuserNow=superuser('postgres',`SELECT rolsuper FROM pg_roles WHERE rolname = '${role}'`);
  // Control: the same query without the pinned search path, as it was once written, does escalate.
  run('supabase_admin',a,python(`print(data_api.present_sql(${settings}).removeprefix(data_api.SAFE).replace('pg_catalog.unnest(','unnest('))`));
  const control=superuser('postgres',`SELECT rolsuper FROM pg_roles WHERE rolname = '${role}'`);
  superuser('postgres',`ALTER ROLE ${role} NOSUPERUSER;`);
  superuser(a,'DROP AGGREGATE public.count(boolean); DROP FUNCTION public.planted_count(bigint, boolean); DROP FUNCTION public.unnest(text[]);');
  return superuserNow==='f'&&control==='t'||`after the platform queries ${superuserNow}, control ${control}`;
 });
 await check('every guard searches the temporary schema last, and the presence check sees it',()=>{
  const configs=superuser(a,"SELECT string_agg(proname || ':' || array_to_string(proconfig, ';'), ' ' ORDER BY proname) FROM pg_proc WHERE pronamespace = 'sbarbase_guard'::regnamespace");
  const present=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  return configs==='exposed_schemas:search_path=pg_catalog, pg_temp exposed_schemas_dropped:search_path=pg_catalog, pg_temp reserved_schemas:search_path=pg_catalog, pg_temp'&&present==='t'||`${configs} ${present}`;
 });
 await check('dropping an extension the tenant owns cannot take an exposed schema with it',()=>{
  const made=works('CREATE SCHEMA withlo; CREATE EXTENSION IF NOT EXISTS lo SCHEMA public;');
  if(made!==true)return made;
  superuser(a,roleSql({schemas:['public','i2s','withlo'],extra_search_path:['public'],max_rows:null}));
  const out=refused('ALTER EXTENSION lo ADD SCHEMA withlo; DROP EXTENSION lo CASCADE;',/schema withlo is exposed|must be owner/);
  const still=superuser(a,"SELECT count(*) FROM pg_namespace WHERE nspname = 'withlo'");
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  return out===true&&still==='1'||`${out} ${still}`;
 });
 await check('a cascade through an extension the tenant owns cannot take an exposed schema with it',()=>{
  const made=works('CREATE SCHEMA viacascade; CREATE EXTENSION IF NOT EXISTS lo SCHEMA public; CREATE TABLE public.cascade_base(x int); CREATE VIEW public.cascade_view AS SELECT x FROM public.cascade_base;');
  if(made!==true)return made;
  superuser(a,roleSql({schemas:['public','i2s','viacascade'],extra_search_path:['public'],max_rows:null}));
  const out=refused('ALTER EXTENSION lo ADD SCHEMA viacascade; ALTER EXTENSION lo ADD VIEW public.cascade_view; DROP TABLE public.cascade_base CASCADE;',/schema viacascade is exposed|must be owner/);
  const still=superuser(a,"SELECT count(*) FROM pg_namespace WHERE nspname = 'viacascade'");
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  return out===true&&still==='1'||`${out} ${still}`;
 });
 await check('turning database access on again cannot run a function the tenant planted as the superuser',()=>{
  const role=`${a}_developer`;
  const planted=works(`CREATE FUNCTION public.pg_has_role(text, text, text) RETURNS boolean LANGUAGE plpgsql AS $f$ BEGIN
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN EXECUTE 'ALTER ROLE ${role} SUPERUSER'; END IF;
    RETURN false; END $f$;`);
  if(planted!==true)return planted;
  // The Studio login must exist for the identity block to call pg_has_role.
  superuser('postgres',`DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${a}_studio') THEN CREATE ROLE ${a}_studio NOLOGIN; END IF; END $$;`);
  const [,grantSql]=generate(a,passwords[0]!);
  superuser(a,grantSql);
  const after=superuser('postgres',`SELECT rolsuper FROM pg_roles WHERE rolname = '${role}'`);
  // Control: the identity block as it was, without the pinned search path and the qualified call.
  run('supabase_admin',a,python(`print(d.studio_identity_sql(${JSON.stringify(a)}).replace('SET search_path TO pg_catalog, pg_temp;','').replace('pg_catalog.pg_has_role(','pg_has_role(').replace('::pg_catalog.name','').replace('::pg_catalog.text',''))`));
  const control=superuser('postgres',`SELECT rolsuper FROM pg_roles WHERE rolname = '${role}'`);
  superuser('postgres',`ALTER ROLE ${role} NOSUPERUSER;`);
  superuser(a,'DROP FUNCTION public.pg_has_role(text, text, text);');
  return after==='f'&&control==='t'||`after the grants ${after}, control ${control}`;
 });
 await check('a start repairs a reserved-schema guard from before, which searched the temporary schema first',()=>{
  superuser(a,'ALTER FUNCTION sbarbase_guard.reserved_schemas() SET search_path = pg_catalog;');
  const before=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  superuser(a,python(`print(data_api.guard_sql(${JSON.stringify(a)}))`));
  const config=superuser(a,"SELECT array_to_string(proconfig, ';') FROM pg_proc WHERE oid = 'sbarbase_guard.reserved_schemas()'::regprocedure");
  const after=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  return before==='f'&&config==='search_path=pg_catalog, pg_temp'&&after==='t'||`${before} ${config} ${after}`;
 });
 await check('no environment keeps its settings in the catalog the cluster shares',()=>{
  superuser(b,roleSql({schemas:['public','b_only'],extra_search_path:['public'],max_rows:77},b));
  const shared=superuser('postgres',"SELECT count(*) FROM pg_db_role_setting, unnest(setconfig) setting WHERE setting LIKE 'pgrst.%'");
  return shared==='0'||`${shared} pgrst role settings`;
 });
 await check('tenant code that becomes the REST login reads nothing of another environment',async()=>{
  // RESET ROLE in an ordinary function that PostgREST runs returns to the REST login (found in review).
  const made=works(`CREATE FUNCTION public.peek() RETURNS text LANGUAGE plpgsql AS $f$ DECLARE found text; BEGIN
     RESET ROLE;
     SELECT string_agg(array_to_string(setconfig, ','), ';') INTO found FROM pg_catalog.pg_db_role_setting;
     RETURN current_user || '|' || coalesce(found, '') || '|' || (SELECT string_agg(key || '=' || value, ',') FROM sbarbase_rest.settings());
   END $f$; GRANT EXECUTE ON FUNCTION public.peek() TO anon; NOTIFY pgrst, 'reload schema';`);
  if(made!==true)return made;
  let answer={status:0,body:''};
  for(let i=0;i<40&&answer.status!==200;i++){answer=await get('/rpc/peek');if(answer.status!==200)await Bun.sleep(250);}
  works('DROP FUNCTION public.peek();');
  const body=answer.body;
  return answer.status===200&&body.includes(`${a}_rest|`)&&!body.includes('b_only')&&!body.includes('db_max_rows=77')||`${answer.status} ${body.slice(0,300)}`;
 });
 await check('tenant code that becomes the REST login cannot change what PostgREST serves',async()=>{
  const made=works(`CREATE FUNCTION public.meddle() RETURNS text LANGUAGE plpgsql AS $f$ DECLARE out text := ''; BEGIN
     RESET ROLE;
     BEGIN EXECUTE 'ALTER ROLE ' || current_user || ' IN DATABASE ' || current_database() || ' SET pgrst.db_schemas = ''auth'''; out := out || 'role-set ';
     EXCEPTION WHEN OTHERS THEN out := out || 'role-denied '; END;
     BEGIN EXECUTE 'CREATE OR REPLACE FUNCTION sbarbase_rest.settings() RETURNS TABLE(key text, value text) LANGUAGE sql AS $s$ VALUES (''pgrst.db_schemas'', ''auth'') $s$'; out := out || 'function-replaced ';
     EXCEPTION WHEN OTHERS THEN out := out || 'function-denied '; END;
     RETURN out;
   END $f$; GRANT EXECUTE ON FUNCTION public.meddle() TO anon; NOTIFY pgrst, 'reload schema';`);
  if(made!==true)return made;
  let answer={status:0,body:''};
  for(let i=0;i<40&&answer.status!==200;i++){answer=await postRpc('meddle');if(answer.status!==200)await Bun.sleep(250);}
  works('DROP FUNCTION public.meddle();');
  return answer.status===200&&answer.body.includes('role-denied')&&answer.body.includes('function-denied')||`${answer.status} ${answer.body.slice(0,300)}`;
 });
 await check('PostgREST applies its settings at a restart, not only after a reload',async()=>{
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  return expectStatus('/b','i2s',200,/"y":2/);
 });
 await check('a platform backup restores the settings; a tenant dump of every schema still works',()=>{
  const platform=spawnSync('docker',['exec',name,'pg_dump','-U','supabase_admin','-Fc','-d',a]);
  if(platform.status)return 'platform dump '+platform.stderr.toString().slice(0,200);
  superuser('postgres',`CREATE DATABASE ${a}_copy;`);
  const restored=spawnSync('docker',['exec','-i',name,'pg_restore','-U','supabase_admin','--exit-on-error','-d',`${a}_copy`],{input:platform.stdout});
  const copied=superuser(`${a}_copy`,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  const original=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  superuser('postgres',`DROP DATABASE ${a}_copy;`);
  const tenant=spawnSync('docker',['exec','-e','PGPASSWORD='+passwords[0],name,'pg_dump','-h','127.0.0.1','-U',`${a}_developer`,'--schema-only','-d',a],{encoding:'utf8'});
  return restored.status===0&&copied===original&&copied.includes('pgrst.db_schemas=public,i2s')&&tenant.status===0
   ||`restore ${restored.status} ${restored.stderr.toString().slice(0,200)}, copied ${copied}, tenant ${tenant.status} ${tenant.stderr.slice(0,200)}`;
 });
 await check('the presence check notices the REST login losing the pre-config function, and the guard gives it back',()=>{
  superuser(a,`REVOKE EXECUTE ON FUNCTION sbarbase_rest.pre_config() FROM ${a}_rest;`);
  const lost=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  superuser(a,python(`print(data_api.guard_sql(${JSON.stringify(a)}))`));
  const back=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  return lost==='f'&&back==='t'||`lost ${lost}, back ${back}`;
 });
 await check('role settings tenant code gives the REST login elsewhere never reach another environment\'s export, and a start clears them',async()=>{
  // Any login may set its own role settings in any database, even one it cannot connect to.
  const made=works(`CREATE FUNCTION public.spread() RETURNS text LANGUAGE plpgsql AS $f$ BEGIN
     RESET ROLE;
     EXECUTE 'ALTER ROLE ' || current_user || ' IN DATABASE ${b} SET work_mem = ''64kB''';
     EXECUTE 'ALTER ROLE ' || current_user || ' SET work_mem = ''64kB''';
     RETURN 'spread';
   END $f$; GRANT EXECUTE ON FUNCTION public.spread() TO anon; NOTIFY pgrst, 'reload schema';`);
  if(made!==true)return made;
  let answer={status:0,body:''};
  for(let i=0;i<40&&answer.status!==200;i++){answer=await postRpc('spread');if(answer.status!==200)await Bun.sleep(250);}
  works('DROP FUNCTION public.spread();');
  if(answer.status!==200)return `${answer.status} ${answer.body.slice(0,200)}`;
  const exported=superuser('postgres',python(`import recovery_boundaries as rb\nprint(rb.settings_query(${JSON.stringify(b)},"'${b}_auth','${b}_rest','${b}_storage'"))`));
  python(`import subprocess
def run(query, database='postgres', check=True):
    out=subprocess.run(['docker','exec','-i',${JSON.stringify(name)},'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database],input=query,capture_output=True,text=True)
    if out.returncode:raise SystemExit(out.stderr)
    return out
d.Runtime.rest_deadlines(None,${JSON.stringify(a)},executor=run,rewrite=True)`);
  const left=superuser('postgres',`SELECT coalesce(string_agg(coalesce(d.datname,'*') || ':' || array_to_string(setconfig, ','), ' ' ORDER BY 1), '') FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid = s.setdatabase WHERE s.setrole = '${a}_rest'::regrole`);
  return !exported.includes(`${a}_rest`)&&left===`${a}:search_path=pg_catalog,statement_timeout=8s,transaction_timeout=12s`||`exported ${exported}, left ${left}`;
 });
 await check('a connection that carries a PostgREST setting stops the configuration load; a clean one and a platform key do not',()=>{
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  const asRest=(sql:string)=>run(`${a}_rest`,a,sql,restPassword);
  const clean=asRest('SELECT sbarbase_rest.pre_config();');
  const poisoned=asRest("SELECT set_config('pgrst.jwt_secret','not-the-secret',false); SELECT sbarbase_rest.pre_config();");
  const anonRole=asRest("SELECT set_config('pgrst.db_anon_role','service_role',false); SELECT sbarbase_rest.pre_config();");
  // A platform key set in the session is overwritten for the load, not refused.
  const managed=asRest("SELECT set_config('pgrst.db_schemas','auth',false); BEGIN; SELECT sbarbase_rest.pre_config(); SELECT current_setting('pgrst.db_schemas'); COMMIT;");
  const path=asRest("SET search_path = public; SELECT sbarbase_rest.pre_config();");
  const refused=(out:ReturnType<typeof run>)=>out.status!==0&&/before the Data API settings were read/.test(out.stderr);
  return clean.status===0&&refused(poisoned)&&refused(anonRole)&&refused(path)&&managed.status===0&&managed.stdout.split('\n').includes('public,i2s')
   ||`clean ${clean.status} ${clean.stderr.slice(0,100)}, poisoned ${poisoned.status} ${poisoned.stderr.slice(0,100)}, role ${anonRole.status}, path ${path.status} ${path.stderr.slice(0,100)}, managed ${managed.status} ${managed.stdout.slice(0,100)} ${managed.stderr.slice(0,100)}`;
 });
 await check('a settings save does not interrupt the API: no refused or missing table while PostgREST reloads',async()=>{
  // Ending the REST login's connections here made PostgREST answer 503, then 404, for about 3 s on every save.
  const seen:number[]=[];
  let stop=false;
  const poll=(async()=>{while(!stop){seen.push((await get('/a')).status);await Bun.sleep(50);}})();
  await Bun.sleep(500);
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':50}))`));
  docker('kill','-s','SIGUSR1',restName);
  await Bun.sleep(3000);
  stop=true;await poll;
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  docker('kill','-s','SIGUSR1',restName);
  const bad=seen.filter(status=>status!==200);
  return seen.length>=20&&bad.length===0||`${bad.length} of ${seen.length} answers were not 200: ${[...new Set(bad)].join(',')}`;
 });
 await check('a database whose platform schema belongs to a non-superuser is refused by the guard',()=>{
  superuser('postgres',`CREATE DATABASE ${a}_owned; CREATE ROLE ${a}_ownerless LOGIN;`);
  superuser(`${a}_owned`,`CREATE SCHEMA sbarbase_rest AUTHORIZATION ${a}_ownerless;`);
  const refusedGuard=run('supabase_admin',`${a}_owned`,python(`print(data_api.guard_sql(${JSON.stringify(a)}))`));
  const present=superuser(`${a}_owned`,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  superuser('postgres',`DROP DATABASE ${a}_owned; DROP ROLE ${a}_ownerless;`);
  return refusedGuard.status!==0&&/must belong to a superuser/.test(refusedGuard.stderr)&&present==='f'
   ||`guard ${refusedGuard.status} ${refusedGuard.stderr.slice(0,160)}, present ${present}`;
 });
 await check('a database restored from before the pin is pinned again before REST starts, so PostgREST does not fall back to public',async()=>{
  superuser(a,roleSql({schemas:['i2s'],extra_search_path:['public'],max_rows:null}));
  superuser(a,`ALTER ROLE ${a}_rest IN DATABASE ${a} RESET search_path;`);
  // What every path that starts REST does first (Runtime.data_api calls rest_deadlines with rewrite).
  python(`import subprocess
def run(query, database='postgres', check=True):
    out=subprocess.run(['docker','exec','-i',${JSON.stringify(name)},'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database],input=query,capture_output=True,text=True)
    if out.returncode:raise SystemExit(out.stderr)
    return out
d.Runtime.rest_deadlines(None,${JSON.stringify(a)},executor=run,rewrite=True)`);
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  const served=await expectStatus('/b','i2s',200,/"y":2/);
  const notPublic=served===true?await expectStatus('/a',undefined,404):served;
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  return notPublic;
 });
 await check('a tenant holding the REST login\'s role setting rows open delays the pin by seconds at most, and a start that ends the sessions gets through',async()=>{
  const sleeper=spawn('docker',['exec','-i','-e','PGPASSWORD='+restPassword,name,'psql','-h','127.0.0.1','-U',`${a}_rest`,'-d',a,'-qAt'],{stdio:['pipe','ignore','ignore']});
  sleeper.stdin.write(`BEGIN; ALTER ROLE ${a}_rest IN DATABASE ${a} SET search_path = public; SELECT pg_sleep(120);\n`);
  const waiting=()=>superuser(a,`SELECT count(*) FROM pg_stat_activity WHERE usename = '${a}_rest' AND datname = '${a}' AND query LIKE '%pg_sleep%'`);
  let seen='0';
  for(let i=0;i<20&&seen==='0';i++){seen=waiting();if(seen==='0')await Bun.sleep(250);}
  const timed=Date.now();
  const bounded=run('supabase_admin',a,python(`print(d.rest_pin_sql(${JSON.stringify(a)}))`));
  const boundedSeconds=(Date.now()-timed)/1000;
  const started=Date.now();
  const ended=run('supabase_admin',a,python(`print(d.rest_pin_sql(${JSON.stringify(a)},end_sessions=True))`));
  const endedSeconds=(Date.now()-started)/1000;
  sleeper.kill();
  const left=superuser('postgres',`SELECT coalesce(string_agg(coalesce(d.datname,'*') || ':' || array_to_string(setconfig, ','), ' ' ORDER BY 1), '') FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid = s.setdatabase WHERE s.setrole = '${a}_rest'::regrole`);
  return seen!=='0'&&bounded.status!==0&&/lock timeout/.test(bounded.stderr)&&boundedSeconds<15&&ended.status===0&&endedSeconds<10
   &&left===`${a}:search_path=pg_catalog,statement_timeout=8s,transaction_timeout=12s`||`seen ${seen}, bounded ${bounded.status} ${bounded.stderr.slice(0,100)} ${boundedSeconds}s, ended ${ended.status} ${ended.stderr.slice(0,100)} ${endedSeconds}s, left ${left}`;
 });
 await check('PostgREST applies a settings write when its container is sent SIGUSR1, with no NOTIFY involved',async()=>{
  // What Runtime.tell_rest does: SIGUSR1 reloads the schema cache and, on the pinned PostgREST, the configuration with it.
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  docker('kill','-s','SIGUSR1',restName);
  const onlyI2s=await expectStatus('/b',undefined,200,/"y":2/);
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  docker('kill','-s','SIGUSR1',restName);
  const both=onlyI2s===true?await expectStatus('/a',undefined,200,/"x":1/):onlyI2s;
  return both;
 });
 await check('a temporary table named record in the REST session cannot make pre_config set a platform key',()=>{
  // Found in review: pre_config looped over a record variable, a temporary table of that name took its place,
  // and a domain CHECK function of the tenant ran between the set_config calls.
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  const made=works(`CREATE FUNCTION public.sneak(text) RETURNS boolean LANGUAGE plpgsql AS $f$ BEGIN
     PERFORM set_config('pgrst.db_schemas', 'auth', true); RETURN true; END $f$;
   CREATE DOMAIN public.sneaky AS text CHECK (public.sneak(VALUE));`);
  const asRest=(sql:string)=>run(`${a}_rest`,a,sql,restPassword);
  // The REST login has no TEMPORARY privilege here; the superuser stands in for a tenant function that created the table.
  const out=run('supabase_admin',a,`SET search_path = pg_catalog; SET ROLE ${a}_rest;
   RESET ROLE; CREATE TEMP TABLE record (key public.sneaky, value public.sneaky); INSERT INTO record VALUES ('x','y');
   SELECT set_config('search_path','pg_catalog',false); SET ROLE ${a}_rest;
   BEGIN; SELECT sbarbase_rest.pre_config(); SELECT current_setting('pgrst.db_schemas'); COMMIT;`);
  // Control: the function as it was written (a record variable) is taken over by the same table.
  superuser(a,`CREATE FUNCTION pg_temp.noop() RETURNS void LANGUAGE sql AS 'select 1';
   CREATE FUNCTION public.old_pre_config() RETURNS void LANGUAGE plpgsql AS $f$ DECLARE item record; BEGIN
     FOR item IN SELECT s.key, s.value FROM sbarbase_rest.settings() s LOOP PERFORM pg_catalog.set_config(item.key, item.value, true); END LOOP;
   END $f$;`);
  const control=run('supabase_admin',a,`SET search_path = pg_catalog; CREATE TEMP TABLE record (key public.sneaky, value public.sneaky); INSERT INTO record VALUES ('x','y');
   BEGIN; SELECT public.old_pre_config(); SELECT current_setting('pgrst.db_schemas'); COMMIT;`);
  superuser(a,'DROP FUNCTION public.old_pre_config();');
  works('DROP DOMAIN public.sneaky CASCADE; DROP FUNCTION public.sneak(text);');
  void asRest;
  return made===true&&out.status===0&&out.stdout.split('\n').includes('public,i2s')&&control.stdout.split('\n').includes('auth')
   ||`made ${made} status ${out.status} ${out.stderr.slice(0,200)} ${out.stdout.slice(0,200)}; control ${control.status} ${control.stderr.slice(0,200)} ${control.stdout.slice(0,100)}`;
 });
 await check('a load that pre_config cannot make serves nothing, not public: the container falls back to the empty schema',async()=>{
  superuser(a,roleSql({schemas:['i2s'],extra_search_path:['public'],max_rows:null}));
  // The function is gone, so every load fails and PostgREST uses the container\'s own configuration.
  superuser(a,'ALTER FUNCTION sbarbase_rest.pre_config() RENAME TO pre_config_off;');
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  let status=0;
  for(let i=0;i<40;i++){status=(await get('/a')).status;if(status!==0&&status!==503)break;await Bun.sleep(250);}
  const secondary=await get('/b','i2s');
  superuser(a,'ALTER FUNCTION sbarbase_rest.pre_config_off() RENAME TO pre_config;');
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  const back=await expectStatus('/b','i2s',200,/"y":2/);
  return status!==200&&secondary.status!==200&&back===true||`while off: /a ${status}, /b ${secondary.status}; after: ${back}`;
 });
 await check('a database from before the settings function (no sbarbase_rest, no empty schema) is made ready by the guard and then serves',async()=>{
  // What the restore paths do before they start REST (lab/recovery-check-services.py, lab/target_runtime.py).
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  superuser(a,'DROP SCHEMA sbarbase_rest CASCADE; DROP SCHEMA sbarbase_empty CASCADE;');
  const missing=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  superuser(a,python(`print(data_api.guard_sql(${JSON.stringify(a)}))`));
  superuser(a,python(`print(data_api.write_sql(${JSON.stringify(a)},{'schemas':['public','i2s'],'extra_search_path':['public'],'max_rows':None}))`));
  const present=superuser(a,python(`print(data_api.guard_present_sql(${JSON.stringify(a)}))`));
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  const served=await expectStatus('/b','i2s',200,/"y":2/);
  return missing==='f'&&present==='t'&&served===true||`missing ${missing}, present ${present}, served ${served}`;
 });
 await check('a session search_path in a pooled REST connection, with unnest planted in public, cannot make PostgREST serve auth',async()=>{
  // The configuration query resolves its names through the connection's search path, and its role-setting rows outrank
  // pre_config's. A planted unnest that answers nothing to the key list and a fake setting to the setting list would
  // leave pre_config unevaluated, if the load were not refused as a whole (raised in review, not reproduced).
  superuser(a,roleSql({schemas:['i2s'],extra_search_path:['public'],max_rows:null}));
  superuser(a,'GRANT USAGE ON SCHEMA auth TO anon; CREATE TABLE IF NOT EXISTS auth.secrets_probe(v int); INSERT INTO auth.secrets_probe VALUES (42); GRANT SELECT ON auth.secrets_probe TO anon;');
  const made=works(`CREATE FUNCTION public.unnest(text[]) RETURNS SETOF text LANGUAGE plpgsql IMMUTABLE AS $f$ BEGIN
     IF array_length($1, 1) > 5 THEN RETURN; END IF;
     RETURN NEXT 'pgrst.db_schemas=auth'; RETURN NEXT 'pgrst.db_extra_search_path=public'; END $f$;
   CREATE FUNCTION i2s.poison() RETURNS void LANGUAGE plpgsql AS $f$ BEGIN
     PERFORM pg_sleep(0.3); PERFORM set_config('search_path','public',false); END $f$;
   GRANT EXECUTE ON FUNCTION i2s.poison() TO anon;`);
  if(made!==true)return made;
  docker('kill','-s','SIGUSR1',restName);await Bun.sleep(2500);
  const call=async(name:string)=>{try{const r=await fetch(`http://127.0.0.1:${port}/rpc/${name}`,{method:'POST',headers:{authorization:'Bearer '+token('anon'),'content-type':'application/json','content-profile':'i2s'},body:'{}'});return {status:r.status,body:await r.text()};}catch(error){return {status:0,body:(error as Error).message};}};
  // Every pooled connection (the pool is 3), then a reload.
  await Promise.all([1,2,3,4,5,6].map(()=>call('poison')));
  // The reload any tenant can force (NOTIFY), together with a platform change.
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  await Bun.sleep(3000);
  const probe=await get('/secrets_probe','auth');
  const refused=await get('/b','i2s');
  works('DROP FUNCTION i2s.poison(); DROP FUNCTION public.unnest(text[]);');
  superuser(a,'DROP TABLE auth.secrets_probe; REVOKE USAGE ON SCHEMA auth FROM anon;');
  docker('restart',restName);
  port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;
  const back=await expectStatus('/b','i2s',200,/"y":2/);
  // The load is refused (and the container's empty schema is served), or made from the platform's settings: never auth.
  return probe.status!==200&&back===true||`auth ${probe.status} ${probe.body.slice(0,80)}, i2s ${refused.status}, after ${back}`;
 });
 await check('a settings() that an archive brought, answering more than the three managed keys, applies only those',()=>{
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  superuser(a,`CREATE OR REPLACE FUNCTION sbarbase_rest.settings() RETURNS TABLE(key text, value text) LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS
   $s$ SELECT * FROM (VALUES ('pgrst.db_schemas', 'public,i2s'), ('pgrst.db_extra_search_path', 'public'), ('pgrst.db_anon_role', 'service_role'), ('pgrst.jwt_secret', 'x')) item(key, value) $s$;`);
  const out=run(`${a}_rest`,a,"BEGIN; SELECT sbarbase_rest.pre_config(); SELECT coalesce(current_setting('pgrst.db_anon_role', true), '(unset)'); SELECT current_setting('pgrst.db_schemas'); COMMIT;",restPassword);
  const lines=out.stdout.split('\n');
  const serving=superuser(a,python(`print(data_api.serving_sql(${JSON.stringify(a)}))`));
  roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null});
  superuser(a,roleSql({schemas:['public','i2s'],extra_search_path:['public'],max_rows:null}));
  return out.status===0&&lines.includes('(unset)')&&lines.includes('public,i2s')&&!serving.includes('anon_role')&&!serving.includes('jwt')
   ||`status ${out.status} ${out.stderr.slice(0,200)} ${out.stdout.slice(0,120)} | ${serving}`;
 });
 await check('the developer login cannot put an extension into the schemas that hold the platform\'s functions',()=>{
  // Privileged extensions run as the superuser through supautils (found in review: citext into sbarbase_rest worked).
  const results=[refused('CREATE EXTENSION citext SCHEMA sbarbase_rest;',/hold no extension|permission denied|reserved|must be/,a),
   refused('CREATE EXTENSION hstore SCHEMA sbarbase_guard;',/hold no extension|permission denied|reserved|must be/,a)];
  const left=superuser(a,"SELECT count(*) FROM pg_extension WHERE extnamespace IN ('sbarbase_rest'::regnamespace, 'sbarbase_guard'::regnamespace)");
  return results.every(item=>item===true)&&left==='0'||`${results.join(' | ')} left ${left}`;
 });
 await check('the Studio login\'s grants give up after the lock bound when a tenant transaction holds the tables, instead of holding the operation lock',async()=>{
  // A Data API change or a signing rotation restarted a running Studio while holding the installation's operation lock
  // (found in review). Both now leave the restart to the supervisor, and the bound stays.
  const hold=Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+passwords[0],name,'psql','-X','-qAt','-h','127.0.0.1','-U',`${a}_developer`,'-d',a],
   {stdin:new TextEncoder().encode('BEGIN; GRANT SELECT ON public.a TO anon; SELECT pg_sleep(40); COMMIT;'),stdout:'pipe',stderr:'pipe'});
  await Bun.sleep(1500);
  const script=python(`import studio\nprint(studio.role_sql(${JSON.stringify(a)},'p'*32))`);
  const started=Date.now();
  const grants=run('supabase_admin',a,script);
  const took=(Date.now()-started)/1000;
  hold.kill();await hold.exited;
  return grants.status!==0&&/lock timeout/.test(grants.stderr)&&took<25||`status ${grants.status} ${took}s ${grants.stderr.slice(0,160)}`;
 });
 await check('a REST login whose password tenant code changed gets the platform\'s password back from the start\'s pin',()=>{
  // Acting as that login (RESET ROLE in a function PostgREST runs) tenant code can change its own password, and PostgREST
  // then cannot connect: the readiness wait fails, and with it the whole start.
  // Through the container's network address: the loopback route of this harness does not ask for a password.
  const viaNetwork=(user:string,sql:string,password:string)=>spawnSync('docker',['exec','-i','-e','PGPASSWORD='+password,name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h',name,'-U',user,'-d',a],{input:sql,encoding:'utf8'});
  const generated=randomBytes(32).toString('hex');
  superuser('postgres',`ALTER ROLE ${a}_rest PASSWORD '${generated}';`);
  const changed=viaNetwork(`${a}_rest`,`ALTER ROLE ${a}_rest PASSWORD 'tenant-chosen';`,generated);
  const locked=viaNetwork(`${a}_rest`,'SELECT 1',generated);
  superuser('postgres',python(`print(d.rest_password_sql(${JSON.stringify(a)},${JSON.stringify(generated)}))`));
  const back=viaNetwork(`${a}_rest`,'SELECT 1',generated);
  superuser('postgres',`ALTER ROLE ${a}_rest PASSWORD '${restPassword}';`);
  return changed.status===0&&locked.status!==0&&back.status===0||`changed ${changed.status} ${changed.stderr.slice(0,100)}, locked ${locked.status}, back ${back.status} ${back.stderr.slice(0,100)}`;
 });
 await check('a REST login session holding its own password row does not hold up the platform\'s password write',async()=>{
  // A request whose function changes the login's password and then runs on holds the row: the write waited for it
  // and failed after its lock bound, under the operation lock, during a signing rotation (found in review).
  const generated=randomBytes(32).toString('hex');
  superuser('postgres',`ALTER ROLE ${a}_rest PASSWORD '${generated}';`);
  const holder=()=>Bun.spawn(['docker','exec','-i','-e','PGPASSWORD='+generated,name,'psql','-X','-qAt','-h',name,'-U',`${a}_rest`,'-d',a],
   {stdin:new TextEncoder().encode(`BEGIN; ALTER ROLE ${a}_rest PASSWORD 'tenant-chosen'; SELECT pg_sleep(30); COMMIT;`),stdout:'pipe',stderr:'pipe'});
  const write=(end:boolean)=>python(`print(d.rest_password_sql(${JSON.stringify(a)},${JSON.stringify(generated)},end_sessions=${end?'True':'False'}))`);
  const [plain,ended]=[write(false),write(true)];
  const timed=async(sql:string)=>{const hold=holder();await Bun.sleep(1500);const started=Date.now();const result=run('supabase_admin','postgres',sql);
   const took=(Date.now()-started)/1000;hold.kill();await hold.exited;return {status:result.status,took,error:result.stderr};};
  // The control: without ending the sessions the write waits for the holder and gives up after its bound (5 s);
  // ended first, it does not wait for the holder, so it succeeds: had it waited, the same bound would have failed it.
  // No timing is asserted (measured under a second; 4.3 s on a host deep in swap).
  const control=await timed(plain);
  const fixed=await timed(ended);
  superuser('postgres',`ALTER ROLE ${a}_rest PASSWORD '${restPassword}';`);
  return control.status!==0&&/lock timeout/.test(control.error)&&fixed.status===0||
   `control ${control.status} ${control.took}s ${control.error.slice(0,100)}, fixed ${fixed.status} ${fixed.took}s ${fixed.error.slice(0,100)}`;
 });
 await check('a function planted in public does not change what PostgREST loads, and without the REST search path pin the load is refused',async()=>{
  const planted=works(`CREATE FUNCTION public.unnest(text[]) RETURNS SETOF text LANGUAGE sql IMMUTABLE AS $f$ SELECT 'pgrst.db_schemas=auth' $f$;`);
  if(planted!==true)return planted;
  const restart=async()=>{docker('restart',restName);port=docker('port',restName,'3000/tcp').split('\n')[0]!.split(':').pop()!;await settle('/a',undefined,200);};
  let result:string|true='';
  try{
   await restart();
   const pinned=await expectStatus('/x','auth',406,/PGRST106/);
   superuser(a,`ALTER ROLE ${a}_rest IN DATABASE ${a} RESET search_path;`);
   await restart();
   // Without the pin the load is refused (pre_config checks the caller's search path): nothing the
   // planted function says is served, and the platform's settings are not either: the container falls
   // back to the empty schema.
   const control=await get('/x','auth');
   const refusedLoad=await get('/b','i2s');
   result=pinned===true&&control.status===406&&/PGRST106/.test(control.body)&&refusedLoad.status===406||`pinned ${pinned}, control ${control.status} ${control.body.slice(0,100)}, i2s ${refusedLoad.status}`;
  }finally{
   superuser(a,python(`print(d.REST_PINS.format(e=${JSON.stringify(a)}))`));
   works('DROP FUNCTION public.unnest(text[]);');
   await restart();
  }
  return result;
 });
 await check('applying the guard again keeps one enabled trigger',()=>{
  superuser(a,python(`print(data_api.guard_sql(${JSON.stringify(a)}))`));
  const out=superuser(a,"SELECT count(*) || ',' || min(evtenabled) FROM pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas'");
  return out==='1,A'||out;
 });
} catch(error) {
 results.push({check:'setup',result:'fail',detail:(error as Error).message.slice(0,500)});
} finally {
 spawnSync('docker',['rm','-f',restName,name],{encoding:'utf8'});
 spawnSync('docker',['network','rm',network],{encoding:'utf8'});
}
const failed=results.filter(item=>item.result==='fail').length;
const summary={total:results.length,failed,skipped:0,checks:results};
if(evidence)writeFileSync(evidence,JSON.stringify({scope:'Disposable local containers: one Supabase Postgres and one PostgREST, two environment databases; not a running installation.',
 image,rest:restImage,at:new Date().toISOString(),...summary},null,1)+'\n');
console.log(JSON.stringify(summary));
process.exit(failed?1:0);
