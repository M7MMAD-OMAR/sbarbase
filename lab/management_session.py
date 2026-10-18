"""How long a password sign-in may keep a management session alive. Mirrors src/control/management-session.ts."""
import os
import re

DEFAULT_SECONDS = 2592000
MIN_SECONDS = 3600
MAX_SECONDS = 2592000


def management_session_seconds(value=None):
    """SBARBASE_MANAGEMENT_SESSION_SECONDS; a missing, malformed or out of range value is the default."""
    if value is None:
        value = os.environ.get('SBARBASE_MANAGEMENT_SESSION_SECONDS')
    if value is None or not re.fullmatch(r'[0-9]{1,8}', value):
        return DEFAULT_SECONDS
    seconds = int(value)
    return seconds if MIN_SECONDS <= seconds <= MAX_SECONDS else DEFAULT_SECONDS
