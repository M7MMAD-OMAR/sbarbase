"""Database-level connection fence for a trusted local operator.
Does not fence file/S3 writes or already-authorized Storage transfers.
"""
import re
import time
import notification_producers


def validate(environment):
    if not re.fullmatch(r'e_[a-f0-9]{24}',environment):raise ValueError('Invalid environment')


def is_fenced(sql,environment):
    validate(environment)
    value=sql(f"SELECT NOT datallowconn OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('{environment}_auth','{environment}_rest','{environment}_storage') AND NOT rolcanlogin) FROM pg_database WHERE datname='{environment}';").stdout.strip()
    if value not in ('t','f',''):raise RuntimeError('Invalid database fence state')
    return value=='t'


def fence(sql,environment,timeout=10,*,catalog=None):
    validate(environment)
    if sql(f"SELECT count(*) FROM pg_database WHERE datname='{environment}';").stdout.strip()!='1':raise RuntimeError('Environment database unavailable')
    # Persist refusal before terminating existing sessions; retry is idempotent.
    sql(f'ALTER DATABASE {environment} ALLOW_CONNECTIONS false;')
    sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{environment}' AND pid<>pg_backend_pid();")
    deadline=time.monotonic()+timeout
    while True:
        active=sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{environment}';").stdout.strip()
        if active=='0':break
        if time.monotonic()>=deadline:raise RuntimeError('Database remains fenced but sessions have not drained')
        time.sleep(.1)
    if not is_fenced(sql,environment):raise RuntimeError('Database fence not retained')
    # Prepared transactions survive client termination and require operator resolution.
    if sql(f"SELECT count(*) FROM pg_prepared_xacts WHERE database='{environment}';").stdout.strip()!='0':raise RuntimeError('Database fenced with unresolved prepared transactions')
    # The fence is retained and durable. The catalog row and the envelope are committed
    # before this function returns, and a producer that cannot reach the catalog leaves
    # the kind unemitted rather than delaying the event or failing the fence.
    notification_producers.emit('fence.applied','critical','fence.applied|'+environment,
        {'environment':environment,'runtime':environment},'system:operator','operator_request',
        {'phase':'fenced'},catalog=catalog)
    return {'connections_refused':True,'sessions':0}


def unfence(sql,environment,*,catalog=None):
    """Explicit operator rollback only, never automatic during provisioning."""
    validate(environment)
    sql(f'ALTER DATABASE {environment} ALLOW_CONNECTIONS true;')
    if is_fenced(sql,environment):raise RuntimeError('Database still fenced')
    notification_producers.emit('fence.released','warning','fence.released|'+environment,
        {'environment':environment,'runtime':environment},'system:operator','operator_request',
        {'phase':'unfenced'},catalog=catalog)


def prepare_export(sql,environment,persist,timeout=10):
    """Disable scoped service logins while retaining trusted local dump access.
    Caller must stop service/file writers first and persist the supplied journal.
    """
    validate(environment)
    names=[environment+'_'+kind for kind in ('auth','rest','storage')]
    selected=','.join("'"+name+"'" for name in names)
    import json
    roles=json.loads(sql(f"SELECT coalesce(jsonb_agg(jsonb_build_object('name',rolname,'login',rolcanlogin) ORDER BY rolname),'[]') FROM pg_roles WHERE rolname IN ({selected});").stdout)
    if {r['name'] for r in roles}!=set(names) or not all(r['login'] for r in roles):raise RuntimeError('Service login inventory requires explicit reconciliation')
    if is_fenced(sql,environment):raise RuntimeError('Database already fenced')
    journal={'environment':environment,'phase':'preparing','original_logins':roles}
    persist(journal)
    sql('BEGIN; '+''.join('ALTER ROLE '+name+' NOLOGIN; ' for name in names)+'COMMIT;')
    # Never automatically reopen logins after a partial failure.
    sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{environment}' AND pid<>pg_backend_pid();")
    deadline=time.monotonic()+timeout
    while sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{environment}';").stdout.strip()!='0':
        if time.monotonic()>=deadline:raise RuntimeError('Service logins fenced; sessions still active')
        time.sleep(.1)
    if sql(f"SELECT count(*) FROM pg_roles WHERE rolname IN ({selected}) AND rolcanlogin;").stdout.strip()!='0':raise RuntimeError('Service login fence not retained')
    journal['phase']='services-fenced';persist(journal)
    return journal
