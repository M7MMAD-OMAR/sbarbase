"""Logical restore rehearsal inside the lab; no production recovery claim."""
import hashlib
import json
import secrets
import subprocess
import run

source = 'a_prod'
neighbor = 'b_prod'
target = 'restore_' + secrets.token_hex(4)


def snapshot(database):
    # Includes test Auth user identities and application data, not passwords/tokens.
    query = """SELECT 'users:' || coalesce(jsonb_agg(x ORDER BY x.id)::text,'[]') FROM (SELECT id,email FROM auth.users) x;
SELECT 'items:' || coalesce(jsonb_agg(x ORDER BY x.id)::text,'[]') FROM public.lab_items x;"""
    return hashlib.sha256(run.sql(query,database).stdout.encode()).hexdigest()


before_source = snapshot(source)
before_neighbor = snapshot(neighbor)
dump = subprocess.run(['docker','exec',run.DB,'pg_dump','-U','postgres','-Fc',source],capture_output=True)
if dump.returncode:
    raise SystemExit('Lab dump failed; no sensitive output displayed')
try:
    run.sql(f'CREATE DATABASE {target}; REVOKE ALL ON DATABASE {target} FROM PUBLIC;')
    # Backup payload contains Auth data. Keep it in memory and never log it.
    restore = subprocess.run(['docker','exec','-i',run.DB,'pg_restore','-U','postgres','--exit-on-error','-d',target],input=dump.stdout,capture_output=True)
    if restore.returncode:
        raise RuntimeError('Restore failed; no sensitive output displayed')
    assert snapshot(target) == before_source
    assert snapshot(source) == before_source
    assert snapshot(neighbor) == before_neighbor
    result = {'source':source,'neighbor':neighbor,'restored_data_matches':True,'source_unchanged':True,'neighbor_unchanged':True,
              'scope':'Logical database restore to a fresh database in the same lab cluster. Not PITR, off-host recovery, object storage, or routing cutover.'}
    (run.STATE/'restore-verification.json').write_text(json.dumps(result,indent=2))
    print('Logical restore matched Auth user identities and application rows; source and neighbor unchanged.')
finally:
    run.sql(f'DROP DATABASE IF EXISTS {target};')
