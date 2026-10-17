// Live check of the agents' SQL runner (src/control/agent-sql.ts) against a real PostgreSQL.
//
// Starts a disposable Supabase Postgres container, creates one environment database and its
// developer login with the runtime's own SQL (lab/durable_runtime.py developer_sql and
// developer_grants_sql), then runs the runner as an agent would: writes, parameters, read-only
// queries including attempts to leave the read-only transaction, migrations with their history,
// and an oversized result. Removes the container at the end. Needs Docker and about 1 GiB.
//
//   bun lab/agent-sql-check.ts [image]
import {spawnSync} from 'node:child_process';
import {mkdtempSync,rmSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomBytes} from 'node:crypto';
import {developerSql,SqlRefusal} from '../src/control/agent-sql';

const image=process.argv[2]??'public.ecr.aws/supabase/postgres:17.11.0.003';
const name='sbarbase-agent-sql-check-'+randomBytes(3).toString('hex');
const runtime='e_'+randomBytes(12).toString('hex');
const password=randomBytes(18).toString('base64url');
const dir=mkdtempSync(join(tmpdir(),'sbarbase-agent-sql-'));
const results:{check:string;result:'pass'|'fail';detail?:string}[]=[];
const docker=(...args:string[])=>{const out=spawnSync('docker',args,{encoding:'utf8'});if(out.status)throw new Error(out.stderr.slice(0,500));return out.stdout.trim();};
const psql=(database:string,sql:string)=>{const out=spawnSync('docker',['exec','-i',name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database],{input:sql,encoding:'utf8'});
 if(out.status)throw new Error(out.stderr.slice(0,800));return out.stdout.trim();};
const check=async(label:string,work:()=>Promise<boolean|string>)=>{
 try{const value=await work();results.push(value===true?{check:label,result:'pass'}:{check:label,result:'fail',detail:String(value)});}
 catch(error){results.push({check:label,result:'fail',detail:(error as Error).message.slice(0,300)});}
};
const refused=async(work:()=>Promise<unknown>)=>{try{await work();return false;}catch(error){return error instanceof SqlRefusal?error.message:String(error);}};

try {
 docker('run','-d','--rm','--name',name,'-e','POSTGRES_PASSWORD='+randomBytes(12).toString('hex'),'-p','127.0.0.1::5432',image);
 const port=Number(docker('port',name,'5432/tcp').split('\n')[0]!.split(':').pop());
 for(let i=0;;i++){if(spawnSync('docker',['exec',name,'pg_isready','-U','postgres','-h','127.0.0.1']).status===0)break;if(i>60)throw new Error('PostgreSQL did not start');await Bun.sleep(1000);}
 await Bun.sleep(2000);
 // The environment as the runtime makes it: its database, its service logins, its developer login.
 const generated=spawnSync('/usr/bin/python3',['-c',`import sys;sys.path.insert(0,'lab');import durable_runtime as d
print(d.Runtime.developer_sql(None,${JSON.stringify(runtime)},${JSON.stringify(password)}))
print('-- grants')
print(d.Runtime.developer_grants_sql(None,${JSON.stringify(runtime)}))`],{encoding:'utf8'});
 if(generated.status)throw new Error(generated.stderr);
 const [roleSql,grantSql]=generated.stdout.split('-- grants');
 psql('postgres',`CREATE DATABASE ${runtime}; CREATE ROLE ${runtime}_auth NOLOGIN; CREATE ROLE ${runtime}_storage NOLOGIN;`);
 psql('postgres',roleSql!);
 psql(runtime,'CREATE SCHEMA IF NOT EXISTS extensions;');
 psql(runtime,grantSql!);
 // Password authentication from the host, as through the loopback listener.
 mkdirSync(join(dir,'database'),{recursive:true});
 writeFileSync(join(dir,'database',`${runtime}.json`),JSON.stringify({password}),{mode:0o600});
 const sql=developerSql(join(dir,'database'),{SBARBASE_DATABASE_PORT:String(port)});
 const target={runtime,host:'127.0.0.1',port};

 await check('several statements run and the last one answers',async()=>{
  const rows=await sql.query(target,{readOnly:false,query:'create table public.items(id int primary key, note text); insert into items values (1,\'a\'),(2,\'b\'); select count(*)::int as n from items;'}) as any[];
  return rows[0]?.n===2||JSON.stringify(rows);
 });
 await check('parameters are bound',async()=>{
  const rows=await sql.query(target,{readOnly:false,query:'select note from items where id = $1',parameters:[2]}) as any[];
  return rows[0]?.note==='b'||JSON.stringify(rows);
 });
 await check('a read-only query reads',async()=>{
  const rows=await sql.query(target,{readOnly:true,query:'select count(*)::int as n from items'}) as any[];
  return rows[0]?.n===2||JSON.stringify(rows);
 });
 for(const attempt of ['delete from items','commit; delete from items','set transaction read write; delete from items','select 1; delete from items','begin; delete from items; commit'])
  await check(`read only refuses: ${attempt}`,async()=>{const message=await refused(()=>sql.query(target,{readOnly:true,query:attempt}));return !!message||'it ran';});
 await check('nothing was deleted by the read-only attempts',async()=>{
  const rows=await sql.query(target,{readOnly:true,query:'select count(*)::int as n from items'}) as any[];
  return rows[0]?.n===2||JSON.stringify(rows);
 });
 await check('a migration runs and its history is kept in supabase_migrations',async()=>{
  await sql.migrate(target,{name:'create_orders',query:'create table public.orders(id int); alter table public.orders enable row level security;'});
  const list=await sql.migrations(target);
  return list.length===1&&list[0]!.name==='create_orders'||JSON.stringify(list);
 });
 await check('a failing migration leaves nothing behind',async()=>{
  const message=await refused(()=>sql.migrate(target,{name:'broken',query:'create table public.half(id int); select * from missing_table;'}));
  const rows=await sql.query(target,{readOnly:true,query:"select to_regclass('public.half') is null as gone"}) as any[];
  const list=await sql.migrations(target);
  return !!message&&rows[0]?.gone===true&&list.length===1||`${message} ${JSON.stringify(rows)} ${list.length}`;
 });
 await check('two migrations in the same second both keep their history',async()=>{
  await sql.migrate(target,{name:'first',query:'select 1'});
  await sql.migrate(target,{name:'second',query:'select 1'});
  const list=await sql.migrations(target);
  const versions=list.map(item=>item.version);
  return list.length===3&&new Set(versions).size===3&&versions.join()===[...versions].sort().join()||JSON.stringify(list);
 });
 await check('the developer login cannot reach another database',async()=>{
  const message=await refused(()=>developerSql(join(dir,'database'),{SBARBASE_DATABASE_PORT:String(port)})
   .query({runtime,host:'127.0.0.1',port},{readOnly:false,query:'create database stolen'}));
  return !!message||'it ran';
 });
 await check('the developer login cannot create a schema (a platform schema made first would run its code)',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:false,query:'create schema realtime'}));
  return typeof message==='string'&&/permission denied/.test(message)||String(message);
 });
 await check('read-only SQL runs inside a read-only transaction',async()=>{
  const rows=await sql.query(target,{readOnly:true,query:"select current_setting('transaction_read_only') as ro"}) as any[];
  return rows[0]?.ro==='on'||JSON.stringify(rows);
 });
 await check('read-only SQL refuses several statements in one text',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:true,query:'select 1; select 2'}));
  return typeof message==='string'&&message.includes('multiple commands')||String(message);
 });
 await check('read-only SQL cannot set the role back',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:true,query:'reset role; delete from items'}));
  return !!message||'it ran';
 });
 await check('the deadline cancels a query that lifted its own statement timeout',async()=>{
  const short=developerSql(join(dir,'database'),{SBARBASE_DATABASE_PORT:String(port)},3000);
  const started=Date.now();
  const message=await refused(()=>short.query(target,{readOnly:false,query:'set statement_timeout = 0; select pg_sleep(60)'}));
  await Bun.sleep(1000);
  const still=psql(runtime,"select count(*) from pg_stat_activity where query like '%pg_sleep(60)%' and state = 'active' and pid <> pg_backend_pid()");
  return typeof message==='string'&&message.includes('cancelled')&&Date.now()-started<10_000&&still==='0'||`${message} ${still}`;
 });
 await check('CREATE SCHEMA IF NOT EXISTS is refused even for the existing supabase_migrations (PostgreSQL checks the right first), so supabase db push cannot run as this login',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:false,query:'create schema if not exists supabase_migrations'}));
  return typeof message==='string'&&/permission denied for database/.test(message)||String(message);
 });
 await check('the developer login can add tables to supabase_migrations',async()=>{
  await sql.query(target,{readOnly:false,query:'create table if not exists supabase_migrations.seed_files(path text primary key)'});
  return true;
 });
 await check('int8 comes back as a number, as the Supabase MCP tools parse it',async()=>{
  const rows=await sql.query(target,{readOnly:true,query:'select count(*) as n, pg_total_relation_size(\'public.items\') as bytes'}) as any[];
  return typeof rows[0]?.n==='number'&&typeof rows[0]?.bytes==='number'||JSON.stringify(rows);
 });
 await check('read-only SQL with parameters, as list_tables sends it',async()=>{
  const rows=await sql.query(target,{readOnly:true,query:'select c.relname as name, c.oid::int8 as id from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname in ($1) and c.relkind=\'r\' order by 1',parameters:['public']}) as any[];
  return rows.some(row=>row.name==='items'&&typeof row.id==='number')||JSON.stringify(rows);
 });
 await check('an oversized result is refused with a hint',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:true,query:"select repeat('x', 1000) from generate_series(1, 10000)"}));
  return typeof message==='string'&&message.includes('LIMIT')||String(message);
 });
 await check('a syntax error comes back as PostgreSQL wrote it',async()=>{
  const message=await refused(()=>sql.query(target,{readOnly:false,query:'selec 1'}));
  return typeof message==='string'&&message.includes('syntax')||String(message);
 });
} finally {
 spawnSync('docker',['rm','-f',name]);
 rmSync(dir,{recursive:true,force:true});
 console.log(JSON.stringify({image,checks:results},null,1));
 if(results.some(item=>item.result==='fail')||!results.length)process.exitCode=1;
}
