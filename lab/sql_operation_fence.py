"""Experimental database-local guard for fixed native SQL, not a SQL sandbox.

Execute each generated script on one fresh psql backend via stdin with
ON_ERROR_STOP. Do not pool, reconnect or wrap the whole script in a transaction.
Not yet integrated into runtime provisioning or cross-database recovery.
"""
import re

UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
RUNTIME=re.compile(r'e_[a-f0-9]{24}')
TABLE='sbarbase_provision_guard.operations'


def bootstrap():
    return f'''BEGIN;
CREATE SCHEMA IF NOT EXISTS sbarbase_provision_guard;
REVOKE ALL ON SCHEMA sbarbase_provision_guard FROM PUBLIC;
CREATE TABLE IF NOT EXISTS {TABLE} (
 token uuid PRIMARY KEY, runtime text NOT NULL, claim uuid NOT NULL,
 attempt bigint NOT NULL CHECK (attempt>0),
 state text NOT NULL CHECK (state IN ('active','revoked')));
REVOKE ALL ON TABLE {TABLE} FROM PUBLIC;
DO $privileges$
DECLARE target_role text;
BEGIN
 FOR target_role IN SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN ('anon','authenticated','service_role') LOOP
  EXECUTE pg_catalog.format('REVOKE ALL ON SCHEMA sbarbase_provision_guard FROM %I',target_role);
  EXECUTE pg_catalog.format('REVOKE ALL ON TABLE {TABLE} FROM %I',target_role);
 END LOOP;
END $privileges$;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_operation ON {TABLE}(runtime) WHERE state='active';
COMMIT;
'''


def identity(runtime,token,claim,attempt):
    if (not isinstance(runtime,str) or not RUNTIME.fullmatch(runtime)
            or not isinstance(token,str) or not UUID.fullmatch(token)
            or not isinstance(claim,str) or not UUID.fullmatch(claim)
            or type(attempt) is not int or not 0<attempt<2**31):
        raise ValueError('Invalid SQL operation identity')
    return f"token='{token}' AND runtime='{runtime}' AND claim='{claim}' AND attempt={attempt}"


def lock_key(runtime):
    if not isinstance(runtime,str) or not RUNTIME.fullmatch(runtime):raise ValueError('Invalid runtime')
    return f"pg_catalog.hashtextextended('sbarbase:provision:{runtime}',0)"


def begin(runtime):
    return f'''\\set ON_ERROR_STOP on
SET default_transaction_isolation='read committed';
SET lock_timeout='5s';
SET statement_timeout='10s';
SELECT pg_catalog.pg_advisory_lock({lock_key(runtime)});
'''


def end(runtime):
    return f'SELECT pg_catalog.pg_advisory_unlock({lock_key(runtime)});\n'


def register(runtime,token,claim,attempt,*,initialize=False):
    exact=identity(runtime,token,claim,attempt)
    return begin(runtime)+(bootstrap() if initialize else '')+f'''DO $guard$
BEGIN
 IF EXISTS(SELECT 1 FROM {TABLE} WHERE runtime='{runtime}' AND token<>'{token}' AND attempt>={attempt}) THEN
  RAISE EXCEPTION 'Stale SQL operation registration';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM {TABLE} WHERE token='{token}') THEN
  INSERT INTO {TABLE} VALUES ('{token}','{runtime}','{claim}',{attempt},'active');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM {TABLE} WHERE {exact} AND state='active') THEN
  RAISE EXCEPTION 'SQL operation registration refused';
 END IF;
END $guard$;
'''+end(runtime)


def revoke(runtime,token,claim,attempt,*,initialize=False,expected_oid=None,expected_cluster=None):
    exact=identity(runtime,token,claim,attempt)
    if expected_oid is not None and (type(expected_oid) is not int or expected_oid<=0):raise ValueError('Invalid expected database identity')
    if expected_cluster is not None and (not isinstance(expected_cluster,str) or not re.fullmatch(r'[1-9][0-9]{0,31}',expected_cluster)):raise ValueError('Invalid expected cluster identity')
    identity_check='' if expected_oid is None else f"DO $identity$ BEGIN IF (SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())<>{expected_oid} THEN RAISE EXCEPTION 'Database identity changed'; END IF; END $identity$;\n"
    if expected_cluster is not None:identity_check+=f"DO $cluster$ BEGIN IF (SELECT system_identifier::text FROM pg_catalog.pg_control_system())<>'{expected_cluster}' THEN RAISE EXCEPTION 'Cluster identity changed'; END IF; END $cluster$;\n"
    return begin(runtime)+identity_check+(bootstrap() if initialize else '')+f'''DO $guard$
BEGIN
 INSERT INTO {TABLE} VALUES ('{token}','{runtime}','{claim}',{attempt},'revoked') ON CONFLICT(token) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM {TABLE} WHERE {exact}) THEN
  RAISE EXCEPTION 'SQL operation revocation identity mismatch';
 END IF;
 UPDATE {TABLE} SET state='revoked' WHERE {exact};
END $guard$;
'''+end(runtime)


def guarded(runtime,token,claim,attempt,query):
    exact=identity(runtime,token,claim,attempt)
    if not isinstance(query,str) or not query.strip() or '\\' in query:
        raise ValueError('Only fixed native SQL without psql metacommands is supported')
    return begin(runtime)+f'''DO $guard$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM {TABLE} WHERE {exact} AND state='active') THEN
  RAISE EXCEPTION 'SQL operation is not active';
 END IF;
END $guard$;
'''+query+'\n'+end(runtime)
