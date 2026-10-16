"""Conservative connection policy for the shared database cluster."""
SERVICE_LIMIT = 6
ENVIRONMENT_LIMIT = 3*SERVICE_LIMIT
SHARED_LIMIT = 2*SERVICE_LIMIT
OPERATIONS_RESERVE = 10
# Added to an environment's database limit while it runs Studio, Realtime or direct access.
STUDIO_CONNECTIONS = 6
# The developer login's own limit while direct database access is on (migrations, psql, an ORM).
DIRECT_CONNECTIONS = 10
# The pinned Realtime opens up to ten tenant connections (its Database.tenant_pool_requirements),
# two metadata connections and a probe, all as the environment's Realtime login.
REALTIME_CONNECTIONS = 16
# The cluster's max_connections, which the runtime raises at start (durable_runtime.py
# ensure_connection_capacity): eight environments at their base limit (8 x 18), the shared logins
# and the operations reserve take 166 of the 197 usable. Studio, Realtime and direct access are
# admitted while they fit against the database limits alone, without the reserve, which leaves
# them 41. The image's default of 100 held four. The database tier's process limit
# (resource_policy.TIERS['system.db'].pids) must stay above this, one process per connection.
MAX_CONNECTIONS = 200


def database_limit(studio=False, realtime=False, direct=False):
    """The connection limit of one environment database, with the extra logins it runs now."""
    return (ENVIRONMENT_LIMIT + (STUDIO_CONNECTIONS if studio else 0) + (REALTIME_CONNECTIONS if realtime else 0)
            + (DIRECT_CONNECTIONS if direct else 0))


def fits(environments, maximum, superuser_reserved, reserved):
    values = (environments, maximum, superuser_reserved, reserved)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Invalid connection budget measurement')
    return environments*ENVIRONMENT_LIMIT+SHARED_LIMIT+OPERATIONS_RESERVE <= maximum-superuser_reserved-reserved
