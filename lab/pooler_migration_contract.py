"""Pool capacity and migration decisions, without allocating native resources.

Call under the installation's existing exclusive admission and SQL identity
fence. This module does not acquire that fence or establish management authority.
"""
from dataclasses import dataclass
import re
import connection_budget as base


def count(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid '+name)
    return value


def runtime_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'e_[a-f0-9]{24}', value):
        raise ValueError('Invalid environment')
    return value


@dataclass(frozen=True)
class Pool:
    """One database/role/mode pool on each Supavisor replica.

    Session and transaction pools have distinct backend reservations. Frontend
    clients can queue but do not add PostgreSQL capacity. Include every admitted
    role and replica; default_pool_size is not a cluster-wide ceiling.
    """
    role: str
    mode: str
    size: int
    clients: int
    replicas: int = 1

    def validate(self, runtime):
        if self.role != runtime+'_developer':
            raise ValueError('Only the scoped developer role is admitted')
        if self.mode not in ('session', 'transaction'):
            raise ValueError('Invalid pool mode')
        count(self.size, 'pool size', 1)
        count(self.clients, 'client limit', 1)
        count(self.replicas, 'replica count', 1)
        if self.clients < self.size:
            raise ValueError('Client limit is below pool size')


@dataclass(frozen=True)
class Environment:
    runtime: str
    studio: bool = False
    realtime: bool = False
    direct: bool = False
    pools: tuple[Pool, ...] = ()

    def reservation(self):
        runtime_name(self.runtime)
        if any(type(v) is not bool for v in (self.studio, self.realtime, self.direct)):
            raise ValueError('Invalid service state')
        seen = set()
        backends = clients = 0
        for pool in self.pools:
            pool.validate(self.runtime)
            if pool.mode in seen:
                raise ValueError('Duplicate role/mode pool')
            seen.add(pool.mode)
            backends += pool.size*pool.replicas
            clients += pool.clients*pool.replicas
        # Direct and pooled developer sessions share one finite role limit.
        developer = (base.DIRECT_CONNECTIONS if self.direct else 0)+backends
        return {'database': base.database_limit(self.studio, self.realtime, False)+developer,
                'developer': developer, 'pool_backends': backends, 'pool_clients': clients}


def capacity(environments, maximum, superuser_reserved, reserved, *, metadata=0):
    """Desired installed reservations, including unchanged neighboring environments.

    metadata is the aggregate finite Supavisor control DB reservation, including
    all replicas. Measurements and the proposal must belong to the same locked
    admission window. This calculation alone cannot make concurrent admission safe.
    """
    for name, value in (('maximum', maximum), ('superuser reserve', superuser_reserved),
                        ('reserved slots', reserved), ('pool metadata', metadata)):
        count(value, name)
    if maximum < superuser_reserved+reserved:
        raise ValueError('Reserved slots exceed cluster maximum')
    reservations = {}
    for environment in environments:
        if environment.runtime in reservations:
            raise ValueError('Duplicate environment')
        reservations[environment.runtime] = environment.reservation()
    if any(item['pool_backends'] for item in reservations.values()) and metadata == 0:
        raise ValueError('Pooler metadata reservation is required')
    usable = maximum-superuser_reserved-reserved
    promised = base.SHARED_LIMIT+metadata+sum(item['database'] for item in reservations.values())
    headroom = usable-promised
    return {'environments': reservations, 'usable': usable, 'promised': promised,
            'headroom': headroom, 'operations_reserve': base.OPERATIONS_RESERVE,
            'fits': headroom >= base.OPERATIONS_RESERVE}


def migration_connection(runtime, mode, *, database, role, prepared_transactions=0,
                         max_prepared_transactions=0):
    """Validate the retained direct session before a migration or recovery drill.

    Never disable max_prepared_transactions to simplify pooling. Existing prepared
    transactions need inventory and explicit commit/rollback reconciliation after
    a crash. No SQL, ACL, owner or HBA changes are made here.
    """
    runtime_name(runtime)
    count(prepared_transactions, 'prepared transaction count')
    count(max_prepared_transactions, 'prepared transaction setting')
    if mode != 'direct':
        raise ValueError('Migrations require the direct connection')
    if database != runtime or role != runtime+'_developer':
        raise ValueError('Migration connection crosses the environment boundary')
    if prepared_transactions > max_prepared_transactions:
        raise ValueError('Prepared transaction inventory exceeds capacity')
    return {'database': database, 'role': role, 'mode': 'direct',
            'reconcile_prepared': prepared_transactions > 0,
            'max_prepared_transactions': max_prepared_transactions}


INVENTORY_SQL = """SELECT current_database(), current_user,
 current_setting('max_connections'), current_setting('superuser_reserved_connections'),
 current_setting('reserved_connections'), current_setting('max_prepared_transactions');
SELECT datname, datconnlimit, datallowconn, datdba FROM pg_database ORDER BY datname;
SELECT rolname, rolconnlimit, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles ORDER BY rolname;
SELECT gid, prepared, owner, database FROM pg_prepared_xacts ORDER BY database, gid;
SELECT nspname, nspowner, nspacl FROM pg_namespace ORDER BY nspname;
SELECT oid, relnamespace, relname, relowner, relacl, relrowsecurity, relforcerowsecurity
 FROM pg_class ORDER BY relnamespace, relname;"""
