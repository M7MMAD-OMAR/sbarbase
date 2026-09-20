"""Experimental two-database revocation coordinator; not replay authorization.

The caller exclusively owns the installation operation and admits no new job.
All old database create/drop/replace batches use the postgres guard. Target
batches use the target guard and cannot dispatch external or asynchronous work.
execute(database, script) returns stdout only after acknowledged psql success.
Each call uses a fresh one-shot backend on the same trusted cluster.
"""
import json
import re
import sql_operation_fence as fence


def observe(execute,runtime):
    fence.lock_key(runtime)
    raw=execute('postgres',f"SELECT json_build_object('cluster',system_identifier::text,'database',current_database(),'control_oid',(SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()),'target',(SELECT json_build_object('oid',oid::bigint,'allows_connections',datallowconn) FROM pg_catalog.pg_database WHERE datname='{runtime}')) FROM pg_catalog.pg_control_system();")
    value=json.loads(raw)
    if (not isinstance(value,dict) or 'target' not in value or value.get('database')!='postgres'
            or not isinstance(value.get('cluster'),str) or not re.fullmatch(r'[1-9][0-9]{0,31}',value['cluster'])
            or type(value.get('control_oid')) is not int or value['control_oid']<=0):
        raise RuntimeError('Control database identity unavailable')
    target=value.get('target')
    if target is not None and (not isinstance(target,dict) or type(target.get('oid')) is not int
            or target['oid']<=0 or type(target.get('allows_connections')) is not bool):
        raise RuntimeError('Target database identity unavailable')
    return value


def revoke_pair(execute,runtime,token,claim,attempt,checkpoint=lambda phase:None,*,expected_control_oid=None,expected_cluster=None,expected_target_oid=None):
    fence.identity(runtime,token,claim,attempt)
    initial=observe(execute,runtime)
    fence.backend_identity(expected_control_oid,expected_cluster)
    if expected_target_oid is not None:fence.backend_identity(expected_target_oid)
    if ((expected_control_oid is not None and initial['control_oid']!=expected_control_oid)
            or (expected_cluster is not None and initial['cluster']!=expected_cluster)
            or (expected_target_oid is not None and (initial['target'] is None or initial['target']['oid']!=expected_target_oid))):
        raise RuntimeError('Captured database identity changed before revocation')
    execute('postgres',fence.revoke(runtime,token,claim,attempt,initialize=True,expected_oid=initial['control_oid'],expected_cluster=initial['cluster']))
    checkpoint('control_committed')
    current=observe(execute,runtime)
    if (current['cluster'],current['control_oid'])!=(initial['cluster'],initial['control_oid']):
        raise RuntimeError('Control database identity changed')
    target=current['target']
    if expected_target_oid is not None and (target is None or target['oid']!=expected_target_oid):
        raise RuntimeError('Captured target identity changed while control revocation drained')
    if target is not None:
        if not target['allows_connections']:raise RuntimeError('Closed target requires explicit reconciliation')
        execute(runtime,fence.revoke(runtime,token,claim,attempt,initialize=True,expected_oid=target['oid'],expected_cluster=current['cluster']))
        checkpoint('target_committed')
    final=observe(execute,runtime)
    if final!=current:raise RuntimeError('Database identity changed during revocation')
    return {'version':1,'scope':'guarded_sql_only','runtime':runtime,'token':token,'claim':claim,'attempt':attempt,
            'cluster':final['cluster'],'control_oid':final['control_oid'],
            'control':'revoked','target':'absent' if target is None else 'revoked',
            'target_oid':None if target is None else target['oid']}


def register_target(execute,runtime,token,claim,attempt,checkpoint=lambda phase:None,*,expected_control_oid=None,expected_cluster=None):
    """Authorize a pinned target registration through the active control token.

    Only this path may initialize target authority. An absent or closed target
    cannot produce a dispatch. Retain the binding when retrying a dispatch;
    never replace it with fresh unguarded metadata.
    """
    fence.identity(runtime,token,claim,attempt)
    def authorized(database,query):
        return execute(database,fence.guarded(runtime,token,claim,attempt,query,expected_oid=expected_control_oid,expected_cluster=expected_cluster))
    binding=observe(authorized,runtime)
    target=binding['target']
    if target is None or not target['allows_connections']:
        raise RuntimeError('Target registration requires an existing open database')
    script=fence.register(runtime,token,claim,attempt,initialize=True,
                          expected_oid=target['oid'],expected_cluster=binding['cluster'])
    checkpoint('target_bound')
    execute(runtime,script)
    return {**binding,'runtime':runtime,'token':token,'claim':claim,'attempt':attempt}
