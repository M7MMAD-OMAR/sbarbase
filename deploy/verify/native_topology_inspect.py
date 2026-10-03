"""Connection-only native extension topology probe after original initialization."""
import json
import time

from native_defaults_inspect import main


def probe(report, sql, native, container):
    evidence = {'scope': 'native-cron-stage-and-worker-connection-decision', 'passed': False,
                'cases': [], 'limitations': ['No job writes, HTTP requests, worker pause contract or archive replay.',
                                             'Original startup diagnostics remain independently unaccepted.']}
    report['topology'] = evidence
    versions = {row['name']: row['default_version'] for row in report['available_extensions']}
    if versions.get('pg_cron') != '1.6.4' or versions.get('pg_net') != '0.20.4':
        raise RuntimeError('Native decision probe versions differ')
    maintenance = 'fixture_maintenance'
    stage = 'fixture_stage'
    app_oid = int(sql("SELECT oid FROM pg_database WHERE datname='postgres';"))
    evidence['application_oid'] = app_oid
    sql('CREATE DATABASE ' + maintenance + ' TEMPLATE template0;')
    sql('CREATE DATABASE ' + stage + ' TEMPLATE template0;', maintenance)
    evidence['maintenance_oid'] = int(sql("SELECT oid FROM pg_database WHERE datname='fixture_maintenance';", maintenance))
    if evidence['maintenance_oid'] == app_oid:
        raise RuntimeError('Maintenance identity equals application')
    sql('CREATE EXTENSION pg_cron;')
    if sql("SELECT extversion FROM pg_extension WHERE extname='pg_cron';") != '1.6.4':
        raise RuntimeError('Installed native cron version differs')
    refusal = native(['docker', 'exec', '-i', container, 'psql', '-X', '-qAt', '-v',
                      'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', stage],
                     data='CREATE EXTENSION pg_cron;', refusal='can only create extension in database postgres')
    if sql("SELECT count(*) FROM pg_extension WHERE extname='pg_cron';", stage) != '0':
        raise RuntimeError('Rejected stage extension was installed')
    evidence['cases'].append({'case': 'native-cron-postgres-only-installation', 'passed': True,
                              'stage_exit_code': refusal.returncode, 'stage_stderr': refusal.stderr})
    kinds = {'pg_cron launcher', 'pg_net 0.20.4 worker'}

    def workers():
        return json.loads(sql("SELECT coalesce(json_agg(row_to_json(a) ORDER BY backend_type),'[]'::json) "
                              "FROM (SELECT pid,backend_type,datid FROM pg_stat_activity WHERE datid="
                              + str(app_oid) + " AND backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) a;", maintenance))

    before = workers()
    if {row['backend_type'] for row in before} != kinds or len(before) != 2:
        raise RuntimeError('Original native worker pair differs')
    evidence['workers_before'] = before
    if int(sql("SELECT oid FROM pg_database WHERE datname='postgres';", maintenance)) != app_oid:
        raise RuntimeError('Application identity changed before fence')
    sql('ALTER DATABASE postgres ALLOW_CONNECTIONS false;', maintenance)
    if sql('SELECT current_database();', maintenance) != maintenance:
        raise RuntimeError('Maintenance unavailable during application fence')
    native(['docker', 'exec', '-i', container, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
            '-U', 'supabase_admin', '-d', 'postgres'], data='SELECT 1;',
           refusal='database "postgres" is not currently accepting connections')
    attached = workers()
    evidence['workers_after_connection_fence'] = attached
    if {row['pid'] for row in attached} != {row['pid'] for row in before}:
        raise RuntimeError('Unexpected original worker change before explicit drain')
    for worker in attached:
        value = sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid=" + str(worker['pid'])
                    + ' AND datid=' + str(app_oid) + " AND backend_type='" + worker['backend_type'] + "';", maintenance)
        if value != 't':
            raise RuntimeError('Exact inventoried worker termination failed')
    samples = []
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        samples.append(workers())
        time.sleep(.25)
    if samples[-1] or any(sample for sample in samples[4:]):
        raise RuntimeError('Native worker reconnected to fenced application')
    evidence['fenced_worker_samples'] = samples
    if int(sql("SELECT oid FROM pg_database WHERE datname='postgres';", maintenance)) != app_oid:
        raise RuntimeError('Application identity changed before reopening')
    sql('ALTER DATABASE postgres ALLOW_CONNECTIONS true;', maintenance)
    deadline = time.monotonic() + 8
    while True:
        after = workers()
        if len(after) == 2 and {row['backend_type'] for row in after} == kinds:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Native worker reconnect deadline')
        time.sleep(.25)
    if {row['pid'] for row in after} & {row['pid'] for row in before}:
        raise RuntimeError('Reconnected worker reused prior process identity')
    evidence['workers_after_reopening'] = after
    evidence['cases'].append({'case': 'native-maintenance-worker-fence-drain-reopen', 'passed': True})
    evidence['passed'] = True


if __name__ == '__main__':
    raise SystemExit(main(probe))
