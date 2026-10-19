// Live check of the developer login's own schemas, the reserved schema guard and Vault.
//
// Starts a disposable Supabase Postgres container, creates two environment databases with their
// developer logins from the runtime's own SQL (lab/durable_runtime.py developer_sql and
// developer_grants_sql), then signs in as one environment's developer login and checks: it creates and renames schemas of its own; it cannot create or rename into a
// platform name, nor drop, disable or replace the guard; it uses Vault; it reaches neither the
// other environment's database nor that environment's secrets; and the superuser still creates
// platform schemas. Passwords and the access rule are checked by lab/database-check.ts. Applying the grants twice changes nothing. Removes the container at the end.
// Needs Docker and about 1 GiB.
//
//   bun lab/developer-schema-check.ts [image] [evidence.json]
import {spawnSync} from 'node:child_process';
import {writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';

const image=process.argv[2]??'public.ecr.aws/supabase/postgres:17.6.1.166';
const evidence=process.argv[3];
const name='sbarbase-developer-schema-check-'+randomBytes(3).toString('hex');
const runtimes=['e_'+randomBytes(12).toString('hex'),'e_'+randomBytes(12).toString('hex')];
const passwords=runtimes.map(()=>randomBytes(18).toString('base64url'));
const results:{check:string;result:'pass'|'fail';detail?:string}[]=[];
const docker=(...args:string[])=>{const out=spawnSync('docker',args,{encoding:'utf8'});if(out.status)throw new Error(out.stderr.slice(0,500));return out.stdout.trim();};
const run=(user:string,database:string,sql:string,password?:string)=>spawnSync('docker',['exec','-i',...(password?['-e','PGPASSWORD='+password]:[]),name,
 'psql','-X','-qAt','-v','ON_ERROR_STOP=1',...(password?['-h','127.0.0.1']:[]),'-U',user,'-d',database],{input:sql,encoding:'utf8'});
const superuser=(database:string,sql:string)=>{const out=run('supabase_admin',database,sql);if(out.status)throw new Error(out.stderr.slice(0,800));return out.stdout.trim();};
const [a,b]=runtimes as [string,string];
const developer=(sql:string,database=a)=>run(`${a}_developer`,database,sql,passwords[0]);
const check=(label:string,work:()=>boolean|string)=>{
 try{const value=work();results.push(value===true?{check:label,result:'pass'}:{check:label,result:'fail',detail:String(value)});}
 catch(error){results.push({check:label,result:'fail',detail:(error as Error).message.slice(0,300)});}
};
const works=(sql:string,database=a)=>{const out=developer(sql,database);return out.status===0||out.stderr.slice(0,300);};
const refused=(sql:string,expect:RegExp,database=a)=>{const out=developer(sql,database);return out.status!==0&&expect.test(out.stderr)||`status ${out.status}: ${out.stderr.slice(0,300)}`;};
const generate=(runtime:string,password:string)=>{
 const out=spawnSync('/usr/bin/python3',['-c',`import sys;sys.path.insert(0,'lab');import durable_runtime as d
print(d.Runtime.developer_sql(None,${JSON.stringify(runtime)},${JSON.stringify(password)}))
print('-- grants')
print(d.Runtime.developer_grants_sql(None,${JSON.stringify(runtime)}))`],{encoding:'utf8'});
 if(out.status)throw new Error(out.stderr);
 return out.stdout.split('-- grants') as [string,string];
};
const reserved=/reserved for the platform/;

try {
 docker('run','-d','--rm','--name',name,'-e','POSTGRES_PASSWORD='+randomBytes(12).toString('hex'),image);
 for(let i=0;;i++){if(spawnSync('docker',['exec',name,'pg_isready','-U','postgres','-h','127.0.0.1']).status===0)break;if(i>90)throw new Error('PostgreSQL did not start');await Bun.sleep(1000);}
 await Bun.sleep(3000);
 // Each environment as the runtime makes it (lab/run.py provision_environment, provision_database).
 runtimes.forEach((runtime,index)=>{
  const [roleSql,grantSql]=generate(runtime,passwords[index]!);
  superuser('postgres',`CREATE ROLE ${runtime}_auth NOLOGIN NOINHERIT; CREATE ROLE ${runtime}_storage NOLOGIN NOINHERIT; CREATE DATABASE ${runtime}; REVOKE ALL ON DATABASE ${runtime} FROM PUBLIC;`);
  superuser(runtime,`REVOKE CREATE ON SCHEMA public FROM PUBLIC; CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION ${runtime}_auth; CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION ${runtime}_storage; CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;`);
  superuser('postgres',roleSql);
  superuser(runtime,grantSql);
 });
 superuser(b,"SELECT vault.create_secret('other environment secret', 'b_secret');");

 check('the developer login is not a superuser and cannot create roles or databases',()=>
  superuser('postgres',`SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname='${a}_developer'`)==='f'||'it can');
 check('it creates a schema of its own and works in it',()=>works('CREATE SCHEMA tdbr; CREATE TABLE tdbr.items(id int); INSERT INTO tdbr.items VALUES (1); CREATE TYPE tdbr.kind AS ENUM (\'a\');'));
 check('it renames its own schema to another name of its own',()=>works('CREATE SCHEMA scratch; ALTER SCHEMA scratch RENAME TO scratch_two; DROP SCHEMA scratch_two;'));
 check('it creates trusted extensions in its database',()=>works('CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS btree_gist;'));
 for(const schema of ['realtime','_realtime','cron','net','graphql','graphql_public','pgsodium','supabase_functions','supabase_anything','sbarbase_guard_two','pgbouncer','_analytics'])
  check(`it cannot create the platform schema ${schema}`,()=>refused(`CREATE SCHEMA ${schema};`,reserved));
 check('supabase db push can send CREATE SCHEMA IF NOT EXISTS supabase_migrations',()=>works('CREATE SCHEMA IF NOT EXISTS supabase_migrations;'));
 check('it cannot create a platform schema with IF NOT EXISTS',()=>refused('CREATE SCHEMA IF NOT EXISTS realtime;',reserved));
 check('it cannot create a platform schema under another owner it belongs to',()=>refused('CREATE SCHEMA realtime AUTHORIZATION anon;',reserved));
 check('it cannot rename its own schema into a platform name',()=>refused('ALTER SCHEMA tdbr RENAME TO realtime;',reserved));
 check('it cannot rename its own schema into a supabase name',()=>refused('ALTER SCHEMA tdbr RENAME TO supabase_functions;',reserved));
 check('its own schema survived the refused renames',()=>works('SELECT count(*) FROM tdbr.items;'));
 check('it cannot create the existing Vault schema again',()=>refused('CREATE SCHEMA vault;',/already exists/));
 check('it cannot rename a platform schema',()=>refused('ALTER SCHEMA vault RENAME TO mine;',/must be owner|permission denied/));
 check('it cannot drop a platform schema',()=>refused('DROP SCHEMA vault CASCADE;',/must be owner|permission denied/));
 check('it cannot drop the guard',()=>refused('DROP EVENT TRIGGER sbarbase_reserved_schemas;',/must be owner|permission denied/));
 check('it cannot disable the guard',()=>refused('ALTER EVENT TRIGGER sbarbase_reserved_schemas DISABLE;',/must be owner|permission denied/));
 check('it cannot replace the guard function',()=>refused("CREATE OR REPLACE FUNCTION sbarbase_guard.reserved_schemas() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$;",/permission denied/));
 check('it cannot create its own event trigger',()=>refused("CREATE FUNCTION public.noop() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$; CREATE EVENT TRIGGER noop ON ddl_command_end EXECUTE FUNCTION public.noop();",/permission denied|must be superuser/));
 check('it stores and reads a Vault secret',()=>{
  const out=developer("SELECT vault.create_secret('top secret', 'a_secret'); SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'a_secret';");
  return out.status===0&&out.stdout.trim().split('\n').pop()==='top secret'||out.stderr.slice(0,300);
 });
 check('it updates a Vault secret',()=>{
  const out=developer("SELECT vault.update_secret(id, 'new secret') FROM vault.secrets WHERE name = 'a_secret'; SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'a_secret';");
  return out.status===0&&out.stdout.trim().split('\n').pop()==='new secret'||out.stderr.slice(0,300);
 });
 check('service_role reads Vault as on Supabase',()=>works("SET ROLE service_role; SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'a_secret';"));
 check('anon cannot read Vault',()=>refused("SET ROLE anon; SELECT * FROM vault.decrypted_secrets;",/permission denied/));
 check("it sees only its own environment's secrets",()=>{
  const out=developer("SELECT string_agg(name, ',' ORDER BY name) FROM vault.secrets;");
  return out.status===0&&out.stdout.trim()==='a_secret'||out.stdout+out.stderr.slice(0,300);
 });
 check("it cannot connect to the other environment's database",()=>refused('SELECT 1;',/permission denied for database|not have CONNECT/,b));
 check("it cannot create a schema in the other environment's database",()=>{
  const out=superuser('postgres',`SELECT has_database_privilege('${a}_developer', '${b}', 'CREATE')::text || has_database_privilege('${a}_developer', '${b}', 'CONNECT')::text`);
  return out==='falsefalse'||out;
 });
 check('the superuser still creates platform schemas',()=>{superuser(a,'CREATE SCHEMA realtime; DROP SCHEMA realtime;');return true;});
 check('applying the grants again changes nothing and keeps one guard',()=>{
  const [roleSql,grantSql]=generate(a,passwords[0]!);
  superuser('postgres',roleSql); superuser(a,grantSql);
  const out=superuser(a,"SELECT count(*) || ',' || min(evtenabled) FROM pg_event_trigger WHERE evtname = 'sbarbase_reserved_schemas'");
  return out==='1,A'||out;
 });
 check('the guard still refuses after the grants are applied again',()=>refused('CREATE SCHEMA net_two; ALTER SCHEMA net_two RENAME TO net;',reserved));
} catch(error) {
 results.push({check:'setup',result:'fail',detail:(error as Error).message.slice(0,500)});
} finally {
 spawnSync('docker',['rm','-f',name]);
}
const failed=results.filter(item=>item.result==='fail');
for(const item of results)console.log(`${item.result==='pass'?'pass':'FAIL'}  ${item.check}${item.detail?'  '+item.detail:''}`);
if(evidence)writeFileSync(evidence,JSON.stringify({image,date:new Date().toISOString().slice(0,10),checks:results},null,1)+'\n');
process.exit(failed.length?1:0);
