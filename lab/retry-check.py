"""Inject provisioning interruptions into disposable databases, then retry."""
import json
import secrets
import run

results = []
for failure in ('roles', 'database', 'permissions'):
    e = 'retry_' + secrets.token_hex(4)
    credentials = {role: secrets.token_hex(32) for role in ('auth', 'rest')}
    def checkpoint(phase):
        if phase == failure:
            raise InterruptedError('Injected interruption')
    try:
        try:
            run.provision_environment(e, credentials, checkpoint)
        except InterruptedError:
            pass
        else:
            raise AssertionError('Fault injection did not run')
        run.provision_environment(e, credentials)
        run.sql('CREATE TABLE public.canary(value text); INSERT INTO public.canary VALUES (\'preserve\');',e)
        before=run.sql(f"SELECT oid FROM pg_database WHERE datname='{e}'").stdout.strip()
        run.provision_environment(e, credentials)
        assert run.sql('SELECT value FROM public.canary;',e).stdout.strip() == 'preserve'
        assert run.sql(f"SELECT oid FROM pg_database WHERE datname='{e}'").stdout.strip() == before
        assert run.sql(f"SELECT has_database_privilege('{e}_auth','{e}','CONNECT');").stdout.strip() == 't'
        assert run.sql(f"SELECT has_database_privilege('anon','{e}','CONNECT');").stdout.strip() == 'f'
        results.append({'interrupted_after':failure,'retry_preserved_database_and_data':True,'explicit_connection_grants':True})
    finally:
        # Only disposable names created by this invocation; never application DBs.
        run.sql(f'DROP DATABASE IF EXISTS {e};')
        for role in ('rest','auth'):
            run.sql(f'DROP ROLE IF EXISTS {e}_{role};')
(run.STATE/'retry-verification.json').write_text(json.dumps(results,indent=2))
print('3 injected failures recovered; repeat provisioning preserved each canary and database identity.')
