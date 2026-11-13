"""Disposable real PGMQ and Studio permission check. No existing database is used."""
import subprocess,json,time,uuid,urllib.request
from pathlib import Path
import durable_runtime as runtime,studio
root=Path(__file__).resolve().parents[1]; prefix='sbarbase-pgmq-'+uuid.uuid4().hex[:10]; db=prefix+'-db';net=prefix+'-net';meta=prefix+'-meta'
e='e_'+'a'*24; other='e_'+'b'*24;password='isolated-pgmq-fixture-only'
def docker(*args,input=None,check=True):
 r=subprocess.run(['docker',*args],input=input,text=True,capture_output=True,timeout=90)
 if check and r.returncode:raise RuntimeError(r.stderr)
 return r
def sql(text,database='postgres',user='supabase_admin',check=True):
 return docker('exec','-i','-e','PGPASSWORD='+password,db,'psql','-X','-h','127.0.0.1','-U',user,'-d',database,'-v','ON_ERROR_STOP=1','-qAt',input=text,check=check)
r=object.__new__(runtime.Runtime)
try:
 docker('network','create','--internal',net)
 image=json.loads((root/'lab/distro-image.lock.json').read_text())['id']
 docker('run','-d','--rm','--name',db,'--network',net,'--label','io.sbarbase.owner='+prefix,'-e','POSTGRES_PASSWORD='+password,image)
 for attempt in range(90):
  result=sql('SELECT 1;',check=False)
  if result.returncode==0:break
  time.sleep(.5)
 else:raise RuntimeError('Database did not start')
 for name in [e,other]:
  sql('CREATE DATABASE '+name+'; REVOKE CONNECT ON DATABASE '+name+' FROM PUBLIC; CREATE ROLE '+name+'_auth NOLOGIN; CREATE ROLE '+name+'_storage NOLOGIN;')
  sql('CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE SCHEMA extensions;',name)
  sql(r.developer_sql(name,password),name)
  sql(r.developer_grants_sql(name),name)
  sql(studio.role_sql(name,password),name)
 # Simulate an existing installation made by the unmodified hosted-role hook.
 sql("CREATE EXTENSION pgmq WITH SCHEMA pgmq; SELECT pgmq.create('existing'); SELECT pgmq.send('existing','{\"retained\":true}'::jsonb);",e)
 container=json.loads(docker('inspect',db).stdout)[0]['Id']
 def adapter_docker(*args,data=None):return docker(*args,input=data)
 studio.studio_extensions.install(adapter_docker,container,image,prefix)
 studio.studio_extensions.install(adapter_docker,container,image,prefix)
 sql(studio.role_sql(e,password),e)
 assert sql("SELECT message FROM pgmq.q_existing;",e,e+'_studio').stdout.strip()=='{"retained": true}'
 sql("SELECT pgmq.drop_queue('existing'); DROP EXTENSION pgmq;",e,e+'_studio')
 print('existing queue ownership migration preserved data; queue drop passed')
 denied=sql('SELECT current_user,session_user;',other,e+'_studio',False)
 assert denied.returncode!=0, 'Studio accessed neighboring database'
 identity=sql('SELECT current_user,session_user;',e,e+'_studio').stdout.strip()
 assert identity==e+'_developer|'+e+'_studio',identity
 print('restricted Studio identity verified')
 meta_image=json.loads((root/'lab/studio-image.lock.json').read_text())['meta']['id']
 docker('run','-d','--rm','--name',meta,'--network',net,
        '-e','PG_META_HOST=0.0.0.0','-e','PG_META_PORT=8080','-e','PG_META_DB_HOST='+db,
        '-e','PG_META_DB_NAME='+e,'-e','PG_META_DB_USER='+e+'_studio','-e','PG_META_DB_PASSWORD='+password,
        '-e','PG_META_DB_SSL_MODE=disable',meta_image)
 address=json.loads(docker('inspect',meta).stdout)[0]['NetworkSettings']['Networks'][net]['IPAddress']
 url='http://'+address+':8080/extensions'
 for attempt in range(60):
  try:
   with urllib.request.urlopen(url,timeout=2) as response:available=json.load(response)
   break
  except (OSError,ValueError):time.sleep(.5)
 else:raise RuntimeError('postgres-meta did not become ready')
 with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(dict(name='pgmq',schema='pgmq',version='1.5.1',cascade=True)).encode(),headers={'content-type':'application/json'}),timeout=20) as response:
  assert response.status==200
 print('real postgres-meta activation passed')
 sql("SELECT pgmq.create('proof'); SELECT pgmq.send('proof','{\"ok\":true}'::jsonb);",e,e+'_studio')
 logical=docker('exec',db,'pg_dump','-U','supabase_admin','-d',e,'--data-only','--no-owner','--no-privileges').stdout
 assert 'COPY pgmq.q_proof' in logical
 assert 'COPY pgmq.meta' in logical
 sql("SELECT pgmq.drop_queue('proof'); SELECT pgmq.create('proof'); TRUNCATE pgmq.meta;",e,e+'_studio')
 sql(logical,e)
 assert sql('SELECT message FROM pgmq.q_proof;',e,e+'_studio').stdout.strip()=='{"ok": true}'
 assert sql("SELECT pgmq.send('proof','{}'::jsonb);",e,e+'_studio').stdout.strip()=='2'
 # The native restore contract retains role ownership. A no-owner import into
 # a differently named environment is a separate, unsupported role migration.
 full=docker('exec',db,'pg_dump','-U','supabase_admin','-d',e,'--clean','--if-exists').stdout
 sql(full,e)
 assert sql('SELECT count(*) FROM pgmq.q_proof;',e,e+'_studio').stdout.strip()=='2'
 assert sql("SELECT queue_name FROM pgmq.meta WHERE queue_name='proof';",e,e+'_studio').stdout.strip()=='proof'
 assert sql("SELECT extowner::regrole FROM pg_extension WHERE extname='pgmq';",e,e+'_studio').stdout.strip()==e+'_developer'
 print('full environment logical dump restored queue metadata, messages and native ownership')
 sql("SELECT pgmq.drop_queue('proof');",e,e+'_studio')
 with urllib.request.urlopen(urllib.request.Request(url+'/pgmq',method='DELETE'),timeout=20) as response:
  assert response.status==200
 with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(dict(name='pgmq',schema='pgmq',version='1.5.1',cascade=True)).encode(),headers={'content-type':'application/json'}),timeout=20) as response:
  assert response.status==200
 sql("SELECT pgmq.create('reopened'); SELECT pgmq.drop_queue('reopened');",e,e+'_studio')
 print('real postgres-meta delete and re-enable passed')
 print('logical queue dump/restore and detached queue deletion passed')
 print('queue create/send and drop/recreate passed')
 blocked=sql('CREATE EXTENSION pg_stat_statements;',e,e+'_studio',False)
 assert blocked.returncode!=0 and 'not available to an environment on a shared server' in blocked.stderr,blocked.stderr
 print('shared statistics denied; sibling database denied')
 print('PGMQ integration check passed')
finally:
 docker('rm','-f',meta,check=False);docker('rm','-f',db,check=False);docker('network','rm',net,check=False)
