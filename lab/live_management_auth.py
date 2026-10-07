"""Native management MFA via a bounded private Bun subprocess pipe."""
import json
import shutil
import subprocess
from pathlib import Path


def management_login(base, operator):
    executable = shutil.which('bun')
    if not executable:
        raise RuntimeError('Native management probe requires Bun')
    result = subprocess.run(
        [executable, str(Path(__file__).with_name('live-management-auth-bridge.ts'))],
        input=json.dumps({'base': base, 'email': operator['email'],
                          'password': operator['password']}),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=65,
        check=False,
    )
    if result.returncode or len(result.stdout) > 16384:
        raise RuntimeError('Native management probe authentication refused')
    value = json.loads(result.stdout)
    if set(value) != {'access_token'} or not isinstance(value['access_token'], str):
        raise RuntimeError('Native management probe authentication refused')
    return 200, value
