"""Conservative connection policy for the pinned four-environment local runtime."""
SERVICE_LIMIT = 6
ENVIRONMENT_LIMIT = 3*SERVICE_LIMIT
SHARED_LIMIT = 2*SERVICE_LIMIT
OPERATIONS_RESERVE = 10


def fits(environments, maximum, superuser_reserved, reserved):
    values = (environments, maximum, superuser_reserved, reserved)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Invalid connection budget measurement')
    return environments*ENVIRONMENT_LIMIT+SHARED_LIMIT+OPERATIONS_RESERVE <= maximum-superuser_reserved-reserved
