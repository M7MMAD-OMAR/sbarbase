#!/usr/bin/env python3
"""Read-only operator checks for a guarded source rollout and retained project removal.

No account credentials are read and no authentication or email requests are made.
The JSON summary is suitable for the checkpoint engine's JSON behavior parser.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys

REMOTE = r'''
import json, pathlib, sqlite3, subprocess
root=pathlib.Path.cwd()
def db(path): return sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
def command(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=25)
 return {'ok':p.returncode==0,'out':p.stdout.strip()}
def http(url,post=False):
 argv=['curl','-sS','--max-time','10','-w','\n%{http_code}',url]
 if post: argv+=['-H','content-type: application/json','--data','{}']
 got=command(argv)
 body,_,code=got['out'].rpartition('\n')
 return {'ok':got['ok'],'status':int(code) if code.isdigit() else 0,'body':body}
head=command(['git','rev-parse','HEAD'])['out']
clean=command(['git','diff','--quiet'])['ok'] and command(['git','diff','--cached','--quiet'])['ok']
unit=command(['systemctl','is-active','sbarbase.service'])['out']
state=json.loads((root/'.lab/upgrades/state.json').read_text())
server=json.loads((root/'.lab/upstream/server.json').read_text())
health=http(server['url'].rstrip('/')+'/health')
try: health_body=json.loads(health['body'])
except Exception: health_body={}
tokens=db(root/'.secrets/upstream/access-tokens.sqlite')
catalog=db(root/'.lab/upstream/control.sqlite')
token_version=tokens.execute('PRAGMA user_version').fetchone()[0]
catalog_version=catalog.execute('PRAGMA user_version').fetchone()[0]
columns=[x[1] for x in tokens.execute('PRAGMA table_info(access_tokens)')]
mcp=http(server['url'].rstrip('/')+'/mcp',True)
project=None
if PROJECT:
 row=catalog.execute('SELECT name FROM projects WHERE id=?',(PROJECT,)).fetchone()
 archived=catalog.execute('SELECT 1 FROM project_archives WHERE project=?',(PROJECT,)).fetchone()
 children=catalog.execute('SELECT e.id,l.state FROM environments e LEFT JOIN environment_lifecycle l ON l.environment=e.id WHERE e.project=?',(PROJECT,)).fetchall()
 project={'exists':row is not None,'name':row[0] if row else None,'archived':archived is not None,'children':children}
print(json.dumps({'head':head,'clean':clean,'unit':unit,'phase':state.get('phase'),'target':state.get('to'),'health_status':health['status'],'health':health_body,'token_version':token_version,'catalog_version':catalog_version,'permissions_column':'permissions' in columns,'mcp_status':mcp['status'],'project':project}))
'''

def curl(url):
    p = subprocess.run(['curl', '-fsS', '--max-time', '15', url], capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError('Public read failed')
    return p.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['rollout', 'deleted'])
    p.add_argument('--ssh-host', required=True)
    p.add_argument('--checkout', required=True)
    p.add_argument('--public-url')
    p.add_argument('--project')
    args = p.parse_args()
    if args.ssh_host.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_.@:-]+', args.ssh_host):
        p.error('Invalid SSH host')
    if args.project and not re.fullmatch(r'[a-f0-9-]{36}', args.project):
        p.error('Invalid project id')
    if args.mode == 'deleted' and not args.project:
        p.error('--project is required for deletion verification')
    if args.mode == 'rollout' and not args.public_url:
        p.error('--public-url is required for rollout verification')
    checks = []
    observed = {}
    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
    try:
        code = 'PROJECT='+repr(args.project)+'\n'+REMOTE
        remote = 'cd '+shlex.quote(args.checkout)+' && python3 -c '+shlex.quote(code)
        proc = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.ssh_host, remote], capture_output=True, text=True, timeout=65)
        if proc.returncode:
            raise RuntimeError('Remote read-only inspection failed')
        observed = json.loads(proc.stdout)
        if args.mode == 'rollout':
            local = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
            check('exact deployed commit', observed['head'] == local)
            check('clean remote tracked source', observed['clean'])
            check('guarded upgrade confirmed', observed['phase'] == 'confirmed' and observed['target'] == local)
            check('system service active', observed['unit'] == 'active')
            check('private health released', observed['health_status'] == 200 and observed['health'].get('held') is False)
            check('access-token schema migrated', observed['token_version'] == 1 and observed['permissions_column'])
            check('catalog archive schema migrated', observed['catalog_version'] >= 10)
            check('MCP rejects missing authentication', observed['mcp_status'] == 401)
            public_health = json.loads(curl(args.public_url.rstrip('/')+'/health'))
            check('public health released', public_health.get('held') is False)
            html = curl(args.public_url)
            scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
            from urllib.parse import urljoin, urlsplit
            assets = []
            for path in scripts:
                url = urljoin(args.public_url, path)
                if urlsplit(url).netloc != urlsplit(args.public_url).netloc:
                    raise RuntimeError('Unexpected cross-origin console script')
                assets.append(curl(url))
            check('current agent UI served', any('get_sbarbase_operations' in script for script in assets))
        else:
            project = observed['project']
            check('exact project removed from active use', not project['exists'] or project['archived'])
            check('no active retained child', all(state in ['deleted', 'purged'] for _, state in project['children']))
            check('no partial lifecycle deletion', all(state not in [None, 'deleting', 'restoring', 'purging', 'failed'] for _, state in project['children']))
    except Exception as error:
        check(type(error).__name__+': '+str(error)[:160], False)
    failed = sum(not item['passed'] for item in checks)
    print(json.dumps({'total': len(checks), 'failed': failed, 'skipped': 0, 'checks': checks, 'deployed_commit': observed.get('head')}))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
