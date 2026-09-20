"""Encrypted logical DB+file recovery rehearsal for a quiescent local fixture."""
import base64
import hashlib
import json
import secrets
import subprocess
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
import run as lab


def recover_storage(db, storage, sql, http, admin, admin_key, public, credentials, accounts, service_keys, jwt, hba, check, evidence):
    source,neighbor,target='env_alpha','env_beta','recovered_alpha'
    def binary(args, data=None):
        result=subprocess.run(args,input=data,capture_output=True)
        if result.returncode:
            raise RuntimeError('Recovery operation failed; sensitive output withheld')
        return result.stdout
    def rows(database):
        tables=sql("SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('auth','storage','public') AND c.relkind='r' ORDER BY 1;",database).stdout.splitlines()
        digest=hashlib.sha256()
        for table in tables:
            digest.update(table.encode())
            digest.update(sql(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]') FROM {table} t;",database).stdout.encode())
        return digest.hexdigest()
    # Copy our fixed helper into the owned probe container, never into user services.
    lab.docker('cp',str(lab.ROOT/'lab/storage-files.cjs'),storage+':/tmp/sbarbase-storage-files.cjs')
    def files(tenant):
        return binary(['docker','exec','-i',storage,'node','/tmp/sbarbase-storage-files.cjs'],json.dumps({'operation':'snapshot','tenant':tenant}).encode())
    original,nearby=rows(source),rows(neighbor)
    original_files,nearby_files=files(source),files(neighbor)
    dump=binary(['docker','exec',db,'pg_dump','-U','supabase_admin','-Fc',source])
    manifest={'version':1,'source':source,'database_sha256':hashlib.sha256(dump).hexdigest(),
              'files_sha256':hashlib.sha256(original_files).hexdigest(),'images':evidence['images']}
    payload=json.dumps({'database':base64.b64encode(dump).decode(),'files':json.loads(original_files),
                        'environment_credentials':credentials[source]}).encode()
    key,nonce=secrets.token_bytes(32),secrets.token_bytes(12)
    aad=json.dumps(manifest,sort_keys=True).encode()
    cipher=AESGCM(key).encrypt(nonce,payload,aad)
    # Both are ignored local test artifacts; keeping them on this host is not off-site recovery.
    lab.secure_file(lab.PRIVATE/'backup-probe.key',base64.b64encode(key).decode())
    envelope={'manifest':manifest,'nonce':base64.b64encode(nonce).decode(),'ciphertext':base64.b64encode(cipher).decode()}
    (lab.STATE/'backup-probe.encrypted.json').write_text(json.dumps(envelope))
    try:
        AESGCM(key).decrypt(nonce,cipher[:-1]+bytes([cipher[-1]^1]),aad)
    except InvalidTag:
        check('backup corruption rejected before database restore',True)
    else:
        check('backup corruption rejected before database restore',False)
    restored=json.loads(AESGCM(key).decrypt(nonce,cipher,aad))
    recovered_dump=base64.b64decode(restored['database'])
    check('encrypted backup round trip preserves database payload',hashlib.sha256(recovered_dump).hexdigest()==manifest['database_sha256'])
    sql(f'CREATE DATABASE {target}; REVOKE ALL ON DATABASE {target} FROM PUBLIC;')
    binary(['docker','exec','-i',db,'pg_restore','-U','supabase_admin','--exit-on-error','-d',target],recovered_dump)
    check('restored Auth application and Storage rows match snapshot',rows(target)==original)
    binary(['docker','exec','-i',storage,'node','/tmp/sbarbase-storage-files.cjs'],json.dumps({'operation':'restore','tenant':target,'files':restored['files']}).encode())
    check('restored file bytes and extended attributes match snapshot',files(target)==original_files)
    # This is an isolated copy of the SAME environment, retaining its role owners
    # and JWT identity. It is not a transfer to an independent project/installation.
    sql(f'GRANT CONNECT ON DATABASE {target} TO {source}_storage;')
    hba_restore=hba[:-2]+[f'host {target} {source}_storage 0.0.0.0/0 scram-sha-256']+hba[-2:]
    lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='\n'.join(hba_restore)+'\n')
    sql('SELECT pg_reload_conf();')
    config=restored['environment_credentials']
    tenant={'anonKey':jwt(source,'anon'),'serviceKey':service_keys[source],'jwtSecret':config['jwt'],
            'databaseUrl':f'postgres://{source}_storage:{config["storage"]}@{db}:5432/{target}',
            'maxConnections':3,'features':{'s3Protocol':{'enabled':False}}}
    status,_=http(admin+'/tenants/'+target,'POST',json.dumps(tenant).encode(),{'apikey':admin_key,'content-type':'application/json'})
    check('recovery tenant registered against restored database',status==201)
    status,data=http(public+'/object/private/same.txt',headers={'authorization':'Bearer '+accounts[source]['access_token'],'x-forwarded-host':target+'.storage.internal'})
    check('restored private object downloadable with original environment identity',status==200 and data==source.encode())
    status,_=http(public+'/object/private/same.txt',headers={'authorization':'Bearer '+accounts[neighbor]['access_token'],'x-forwarded-host':target+'.storage.internal'})
    check('neighbor identity still denied by restored environment',status in (400,401,403))
    check('source database unchanged by recovery',rows(source)==original)
    check('neighbor database unchanged by recovery',rows(neighbor)==nearby)
    check('source files unchanged by recovery',files(source)==original_files)
    check('neighbor files unchanged by recovery',files(neighbor)==nearby_files)
    evidence['recovery_scope']='Quiescent fixture, AES-256-GCM envelope, logical dump plus file bytes/xattrs and environment credentials; same cluster/shared Storage recovery alias. Not enforced online consistency, off-host restore, new-role mapping, route cutover, full platform backup or PITR.'
