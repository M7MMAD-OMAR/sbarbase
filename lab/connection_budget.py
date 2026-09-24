"""Conservative connection policy for the pinned four-environment local runtime."""
SERVICE_LIMIT = 6
ENVIRONMENT_LIMIT = 3*SERVICE_LIMIT
SHARED_LIMIT = 2*SERVICE_LIMIT
OPERATIONS_RESERVE = 10
# Added to an environment's database limit while it runs Studio or Realtime.
STUDIO_CONNECTIONS = 6
# The pinned Realtime opens up to ten tenant connections (its Database.tenant_pool_requirements),
# two metadata connections and a probe, all as the environment's Realtime login.
REALTIME_CONNECTIONS = 16


def database_limit(studio=False, realtime=False):
    """The connection limit of one environment database, with the extra logins it runs now."""
    return ENVIRONMENT_LIMIT + (STUDIO_CONNECTIONS if studio else 0) + (REALTIME_CONNECTIONS if realtime else 0)


def fits(environments, maximum, superuser_reserved, reserved):
    values = (environments, maximum, superuser_reserved, reserved)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Invalid connection budget measurement')
    return environments*ENVIRONMENT_LIMIT+SHARED_LIMIT+OPERATIONS_RESERVE <= maximum-superuser_reserved-reserved
