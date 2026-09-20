"""Shared upstream Storage process with per-environment database credentials."""
import base64
import hashlib
import hmac
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import run as lab


def run_storage_probe(db, prefix, sql, launch, endpoint, credentials, accounts, check, evidence):
    pin = json.loads((lab.ROOT/'lab/storage-image.lock.json').read_text())
    info = json.loads(lab.docker('image', 'inspect', pin['id']).stdout)[0]
    evidence['images']['storage'] = pin
    admin_key, encryption_key, control_password = (secrets.token_hex(32) for _ in range(3))
    sql(f"CREATE ROLE storage_control LOGIN PASSWORD '{control_password}';")
    sql('CREATE DATABASE storage_metadata OWNER storage_control;')
    hba = ['local all supabase_admin trust', 'host storage_metadata storage_control 0.0.0.0/0 scram-sha-256']
    for e, v in credentials.items():
        v['storage'] = secrets.token_hex(32)
        sql(f"CREATE ROLE {e}_storage LOGIN NOINHERIT PASSWORD '{v['storage']}'; GRANT anon,authenticated,service_role TO {e}_storage; GRANT CONNECT ON DATABASE {e} TO {e}_storage;")
        sql(f"CREATE SCHEMA storage AUTHORIZATION {e}_storage; GRANT USAGE ON SCHEMA storage TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE {e}_storage IN SCHEMA storage GRANT ALL ON TABLES TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE {e}_storage IN SCHEMA storage GRANT ALL ON SEQUENCES TO anon,authenticated,service_role;", e)
        for role in ('auth','rest','storage'):
            hba.append(f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256')
    hba += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
    lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='\n'.join(hba)+'\n')
    sql('SELECT pg_reload_conf();')
    name = prefix+'-storage'
    launch(name,info['Id'],{
        'MULTI_TENANT':'true','MULTITENANT_DATABASE_URL':f'postgres://storage_control:{control_password}@{db}:5432/storage_metadata',
        'ENCRYPTION_KEY':encryption_key,'ADMIN_API_KEYS':admin_key,'DB_INSTALL_ROLES':'false',
        'STORAGE_BACKEND':'file','FILE_STORAGE_BACKEND_PATH':'/tmp/storage-data','REGION':'local',
        'FILE_SIZE_LIMIT':'1048576','DATABASE_MAX_CONNECTIONS':'3','MULTITENANT_DATABASE_MAX_CONNECTIONS':'3',
        'PG_QUEUE_ENABLE':'false','ENABLE_IMAGE_TRANSFORMATION':'false','S3_PROTOCOL_ENABLED':'false',
        'X_FORWARDED_HOST_REGEXP':r'^([a-z_]+)\.storage\.internal$',
        'LOG_LEVEL':'error'},'512m',.5)
    public,admin = endpoint(name,5000),endpoint(name,5001)

    def http(url, method='GET', body=None, headers=None):
        req=urllib.request.Request(url,data=body,method=method,headers=headers or {})
        try:
            response=urllib.request.urlopen(req,timeout=15)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            return response.status,response.read()

    for _ in range(60):
        try:
            if http(admin+'/tenants',headers={'apikey':admin_key})[0]==200:
                break
        except Exception:
            pass
        if lab.docker('inspect','--format','{{.State.Running}}',name).stdout.strip()!='true':
            # Record only a sanitized error category, never raw logs or connection URLs.
            logs=lab.docker('logs',name,check=False)
            combined=logs.stdout+logs.stderr
            markers=['ECONNREFUSED','ENCRYPTION_KEY','permission denied','does not exist','MULTITENANT_DATABASE_URL']
            evidence['storage_startup_categories']=[m for m in markers if m in combined]
            raise RuntimeError('Shared Storage exited during startup')
        time.sleep(.5)
    else:
        raise RuntimeError('Storage admin readiness timed out')
    check('shared Storage admin rejects missing API key', http(admin+'/tenants')[0]==401)

    def jwt(e,role):
        enc=lambda value:base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).rstrip(b'=').decode()
        msg=enc({'alg':'HS256','typ':'JWT'})+'.'+enc({'role':role,'iss':'storage-lab','exp':int(time.time())+3600})
        return msg+'.'+base64.urlsafe_b64encode(hmac.new(credentials[e]['jwt'].encode(),msg.encode(),hashlib.sha256).digest()).rstrip(b'=').decode()

    service_keys={e:jwt(e,'service_role') for e in credentials}
    for e,v in credentials.items():
        payload={'anonKey':jwt(e,'anon'),'serviceKey':service_keys[e],'jwtSecret':v['jwt'],
                 'databaseUrl':f'postgres://{e}_storage:{v["storage"]}@{db}:5432/{e}', 'maxConnections':3,
                 'features':{'s3Protocol':{'enabled':False},'imageTransformation':{'enabled':False}}}
        status,_=http(admin+'/tenants/'+e,'POST',json.dumps(payload).encode(),{'apikey':admin_key,'content-type':'application/json'})
        check(e+' registered in shared Storage',status==201)
        # Registration can return 201 even if migrations failed; check database state.
        migration=sql("SELECT to_regclass('storage.objects') IS NOT NULL;",e).stdout.strip()
        check(e+' Storage migration created object metadata',migration=='t')

    def api(e,path,method='GET',body=None,token=None,content_type='application/json'):
        return http(public+path,method,body,{'authorization':'Bearer '+(token or service_keys[e]),
                    'x-forwarded-host':e+'.storage.internal','content-type':content_type})

    for e in credentials:
        status,body=api(e,'/bucket','POST',json.dumps({'id':'private','name':'private','public':False}).encode())
        if status not in (200,201):
            parsed=json.loads(body)
            evidence['storage_bucket_error']={'status':status,'error':parsed.get('error'),'code':parsed.get('code'),'message':parsed.get('message')}
        check(e+' service token creates private bucket',status in (200,201))
        sql("CREATE POLICY own_objects ON storage.objects TO authenticated USING (owner_id=auth.uid()::text) WITH CHECK (owner_id=auth.uid()::text);",e)
        status,_=api(e,'/object/private/same.txt','POST',e.encode(),accounts[e]['access_token'],'text/plain')
        check(e+' user uploads private object',status in (200,201))
        status,data=api(e,'/object/private/same.txt',token=accounts[e]['access_token'])
        check(e+' same object path returns only its own bytes',status==200 and data==e.encode())
        for other in credentials:
            if other!=e:
                status,_=api(e,'/object/private/same.txt',token=accounts[other]['access_token'])
                check(e+' rejects other environment user token',status in (400,401,403))
                status,_=api(e,'/object/private/same.txt',token=service_keys[other])
                check(e+' rejects other environment service token',status in (400,401,403))
        status,_=api(e,'/object/private/same.txt',token=accounts[e]['other_access_token'])
        check(e+' private object hidden from second user in same environment',status!=200)
        status,_=api(e,'/object/public/private/same.txt',token=jwt(e,'anon'))
        check(e+' private object inaccessible through public route',status!=200)
    for e in credentials:
        status,_=api(e,'/bucket','POST',json.dumps({'id':'public','name':'public','public':True}).encode())
        check(e+' public fixture bucket created',status in (200,201))
        status,_=api(e,'/object/public/public.txt','POST',e.encode(),accounts[e]['access_token'],'text/plain')
        check(e+' public fixture object uploaded',status in (200,201))
    for e in credentials:
        status,data=api(e,'/object/private/same.txt',token=accounts[e]['access_token'])
        check(e+' original bytes survive same-name upload in neighbor',status==200 and data==e.encode())
        for target in (*credentials, 'storage_metadata', 'postgres'):
            command=['exec','-i',db,'sh','-c',
                     'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -At',
                     'storage-probe',db,e+'_storage',target]
            result=lab.docker(*command,data=credentials[e]['storage']+'\nSELECT 1;',check=False)
            check(e+' storage credential '+('accepted by ' if target==e else 'denied by ')+target,(result.returncode==0)==(target==e))
        check(e+' Storage tables owned by scoped login',sql(f"SELECT bool_and(pg_get_userbyid(c.relowner)='{e}_storage') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='storage' AND c.relkind='r';",e).stdout.strip()=='t')
    sdk_input={'storage':public,'tenants':{e:{'anonymousToken':jwt(e,'anon'),'token':accounts[e]['access_token']} for e in credentials}}
    sdk=subprocess.run(['bun','lab/storage-sdk-check.ts'],input=json.dumps(sdk_input),text=True,capture_output=True,cwd=lab.ROOT)
    if sdk.returncode:
        raise RuntimeError('Storage SDK gateway checks failed; secret-bearing output withheld')
    sdk_results=json.loads(sdk.stdout)
    for result in sdk_results['checks']:
        check(result['check'],result['passed'])
    evidence['storage_scope']='One original Storage process, file backend, separate tenant DB logins and JWT secrets; private upload/download and crossed-token rejection. SDK gateway and public/signed URL behavior also checked; no backups, S3 or durable worker integration.'
