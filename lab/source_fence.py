"""Database-level connection fence for a trusted local operator.
Does not fence file/S3 writes or already-authorized Storage transfers.
"""
import re
import time


def validate(environment):
    if not re.fullmatch(r'e_[a-f0-9]{24}',environment):raise ValueError('Invalid environment')


def is_fenced(sql,environment):
    validate(environment)
    value=sql(f"SELECT NOT datallowconn FROM pg_database WHERE datname='{environment}';").stdout.strip()
    if value not in ('t','f',''):raise RuntimeError('Invalid database fence state')
    return value=='t'


def fence(sql,environment,timeout=10):
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
    return {'connections_refused':True,'sessions':0}


def unfence(sql,environment):
    """Explicit operator rollback only, never automatic during provisioning."""
    validate(environment)
    sql(f'ALTER DATABASE {environment} ALLOW_CONNECTIONS true;')
    if is_fenced(sql,environment):raise RuntimeError('Database still fenced')
