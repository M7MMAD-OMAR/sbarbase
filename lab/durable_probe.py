"""The real host behind lab/durable_inventory.py and lab/durable_lifecycle.py.

Every read and write of the durable runtime's state goes through functions that already own it
(run.docker, runtime_files); nothing here names a state file. Tests replace this class with a stub.
"""
import json
import urllib.error
import urllib.request

import run as lab
import runtime_files

DB = 'sbarbase-durable-db'
JOURNALS = 'durable-lifecycle'


class LiveProbe:
    def __init__(self, state=None, private=None):
        self.state = state or runtime_files.STATE
        self.private = private or runtime_files.PRIVATE

    # Docker -----------------------------------------------------------------------------------
    def containers(self):
        ids = lab.docker('ps', '-a', '-q', '--no-trunc').stdout.split()
        if not ids:
            return []
        value = json.loads(lab.docker('inspect', *ids).stdout)
        if not isinstance(value, list):
            raise RuntimeError('Container inventory unavailable')
        return value

    def docker(self, *args):
        return lab.docker(*args)

    # PostgreSQL: the superuser of the shared cluster, exactly as lab/durable_runtime.py runs it ---
    def sql(self, query, database='postgres'):
        return lab.docker('exec', '-i', DB, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin',
                          '-d', database, '-qAt', data=query).stdout

    # State -------------------------------------------------------------------------------------
    def endpoints(self):
        value = runtime_files.read_endpoints(self.state, strict=True)
        if not isinstance(value, dict):
            raise RuntimeError('Published endpoints unreadable')
        return value

    def write_endpoints(self, value):
        runtime_files.write_endpoints(value, self.state)

    def secret_environments(self):
        """Only the names of the environments the runtime secret holds, never a value."""
        value = runtime_files.read_runtime_secret(self.private).get('environments')
        return set(value) if isinstance(value, dict) else set()

    def read_journal(self, runtime):
        path = self.state / JOURNALS / (runtime + '.json')
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return None

    def write_journal(self, runtime, value):
        folder = self.state / JOURNALS
        lab.private_dir(folder)
        lab.atomic(folder / (runtime + '.json'), value)

    def network_address(self, item, network):
        return ((item.get('NetworkSettings') or {}).get('Networks') or {}).get(network, {}).get('IPAddress') or ''

    def http_ok(self, url):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False
