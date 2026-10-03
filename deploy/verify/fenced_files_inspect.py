"""Native owned-volume object tree prerequisite, not full restore acceptance."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
from restore_files import TREE_SCRIPT
from run_checks import source_digest

SYNC_AUDIT = r'''
const observedFS=require('node:fs'), descriptors=new Map(), observations=[];
const originalOpen=observedFS.openSync.bind(observedFS);
const originalSync=observedFS.fsyncSync.bind(observedFS);
const originalRename=observedFS.renameSync.bind(observedFS);
observedFS.openSync=function(location,...args){
  const descriptor=originalOpen(location,...args); descriptors.set(descriptor,location); return descriptor;
};
observedFS.fsyncSync=function(descriptor){
  const result=originalSync(descriptor); observations.push(['sync',descriptors.get(descriptor)]); return result;
};
observedFS.renameSync=function(source,target){
  const result=originalRename(source,target); observations.push(['rename',source,target]); return result;
};
process.on('exit',()=>observedFS.writeFileSync('/tmp/fixture-sync-audit.json',JSON.stringify(observations)));
'''


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    owner = 'sbarbase-fixture-' + fixture
    container, network, volume = (fixture + '-fenced-' + part for part in ('storage', 'net', 'objects'))
    report = {'scope': 'native-object-tree-prerequisite', 'source_sha256': source_digest(),
              'commands': [], 'cases': [], 'passed': False,
              'limitations': ['Synthetic owned volume, pinned Storage Node image with sleeping entrypoint.',
                              'No actual Storage API writer barrier, full restore or process interruption proof.',
                              'No independent host, power-loss, HA/PITR or release acceptance.']}
    cid = None
    created_network = created_volume = False

    def native(args, check=True):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        shown = [('<public-script-sha256:' + hashlib.sha256(arg.encode()).hexdigest() + '>')
                 if arg.endswith(TREE_SCRIPT) else arg for arg in args]
        report['commands'].append({'args': shown, 'exit_code': result.returncode,
                                   'stdout': result.stdout, 'stderr': result.stderr})
        if 'warning' in result.stderr.lower() or check and (result.returncode or result.stderr):
            raise RuntimeError('Native object fixture command refused')
        return result

    def node(code):
        return native(['docker', 'exec', cid, 'node', '-e', code]).stdout.strip()

    def tree(action, name, expected=None, target='', refuse=False, audit=False):
        script = SYNC_AUDIT + TREE_SCRIPT if audit else TREE_SCRIPT
        result = native(['docker', 'exec', cid, 'node', '-e', script, action, name,
                         json.dumps(expected), target], check=not refuse)
        if refuse:
            if result.returncode != 1 or result.stderr != 'Object tree identity operation refused\n' or result.stdout:
                raise RuntimeError('Unsupported tree operation did not strictly refuse')
            return None
        return json.loads(result.stdout)

    def case(name, **evidence):
        report['cases'].append({'case': name, 'passed': True, **evidence})

    try:
        pin = json.loads((ROOT / 'lab/storage-image.lock.json').read_text())
        reference = image_identity.reference(pin)
        expected_id = image_identity.resolved_id(reference, image_identity.record(
            native(['docker', 'image', 'inspect', reference]).stdout))
        native(['docker', 'network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network])
        created_network = True
        native(['docker', 'volume', 'create', '--label', 'io.sbarbase.owner=' + owner, volume])
        created_volume = True
        v = image_identity.record(native(['docker', 'volume', 'inspect', volume]).stdout)
        if v['Name'] != volume or v['Labels']['io.sbarbase.owner'] != owner:
            raise RuntimeError('Owned volume admission changed')
        cid = native(['docker', 'run', '-d', '--pull=never', '--name', container,
                      '--label', 'io.sbarbase.owner=' + owner, '--network', network,
                      '--memory', '256m', '--memory-swap', '256m', '--cpus', '.25', '--pids-limit', '64',
                      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '0:0',
                      '--tmpfs', '/tmp:rw,mode=1777,size=16m', '--mount', 'type=volume,src=' + volume + ',dst=/data',
                      '--entrypoint', 'node', reference, '-e', 'setInterval(()=>{},1000)']).stdout.strip()
        record = image_identity.record(native(['docker', 'inspect', cid]).stdout)
        if record['Image'] != expected_id or record['Name'] != '/' + container \
                or record['Config']['Labels']['io.sbarbase.owner'] != owner or not record['State']['Running']:
            raise RuntimeError('Owned native Storage identity changed')
        node("const f=require('node:fs');const p='/data/sbarbase-lab/';"
             "for(const n of ['live','neighbor','stage/tenant'])f.mkdirSync(p+n,{recursive:true});"
             "f.writeFileSync(p+'live/object.bin','original');f.chmodSync(p+'live',0o750);"
             "f.chmodSync(p+'live/object.bin',0o640);f.writeFileSync(p+'neighbor/kept.bin','neighbor');"
             "f.writeFileSync(p+'stage/tenant/object.bin','archive');")
        neighbor = tree('identity', 'neighbor')
        archived = tree('identity', 'stage/tenant')
        synced = tree('sync', 'stage/tenant')
        if archived != synced:
            raise RuntimeError('Sync changed archived identity')
        reordered = {k: archived[k] for k in reversed(list(archived))}
        moved = tree('rename', 'stage/tenant', reordered, 'restored', audit=True)
        events = json.loads(node("process.stdout.write(require('node:fs').readFileSync('/tmp/fixture-sync-audit.json','utf8'))"))
        rename = ['rename', '/data/sbarbase-lab/stage/tenant', '/data/sbarbase-lab/restored']
        if not events or events[0] != rename or any(['sync', parent] not in events[1:]
                for parent in ('/data/sbarbase-lab/stage', '/data/sbarbase-lab')):
            raise RuntimeError('Rename did not sync both parent directories after mutation')
        if moved != archived or tree('identity', 'stage/tenant')['exists']:
            raise RuntimeError('Rename changed tree or retained source')
        case('sync-and-relative-rename-with-reordered-journal', identity=archived, native_sync_events=events)
        old = tree('identity', 'live')
        node("require('node:fs').appendFileSync('/data/sbarbase-lab/live/object.bin',' changed');")
        tree('rename', 'live', old, 'unexpected', refuse=True)
        if tree('identity', 'unexpected')['exists']:
            raise RuntimeError('Stale content refusal mutated target')
        case('changed-content-refused')
        changed = tree('identity', 'live')
        node("const f=require('node:fs'),p='/data/sbarbase-lab/live';"
             "f.renameSync(p,p+'-previous');f.mkdirSync(p);f.chmodSync(p,0o750);"
             "f.writeFileSync(p+'/object.bin','original changed');f.chmodSync(p+'/object.bin',0o640);")
        replaced = tree('identity', 'live')
        if replaced['sha256'] != changed['sha256'] or replaced['inode'] == changed['inode']:
            raise RuntimeError('Stale inode fixture did not preserve equal content')
        tree('remove', 'live', changed, refuse=True)
        case('equal-content-replaced-inode-refused')
        tree('rename', 'restored', moved, 'neighbor', refuse=True)
        if tree('identity', 'restored') != moved or tree('identity', 'neighbor') != neighbor:
            raise RuntimeError('Existing target refusal changed trees')
        case('existing-neighbor-target-refused')
        node("require('node:fs').symlinkSync('/data/sbarbase-lab/neighbor/kept.bin','/data/sbarbase-lab/live/link');")
        tree('identity', 'live', refuse=True)
        tree('identity', '../neighbor', refuse=True)
        case('symlink-and-traversal-refused')
        tree('remove', 'restored', dict(moved, foreign=True), refuse=True)
        if tree('identity', 'restored') != moved:
            raise RuntimeError('Malformed journal changed tree')
        case('foreign-journal-key-refused')
        tree('remove', 'restored', moved)
        if tree('identity', 'restored')['exists'] or tree('identity', 'neighbor') != neighbor:
            raise RuntimeError('Removal or neighboring tree integrity failed')
        case('identity-bound-remove-preserves-neighbor', neighbor=neighbor)
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        try:
            if cid:
                record = image_identity.record(native(['docker', 'inspect', cid]).stdout)
                if record['Name'] != '/' + container or record['Config']['Labels']['io.sbarbase.owner'] != owner:
                    raise RuntimeError('Cleanup container ownership changed')
                native(['docker', 'rm', '-f', cid])
            if created_network:
                native(['docker', 'network', 'rm', network])
            if created_volume:
                v = image_identity.record(native(['docker', 'volume', 'inspect', volume]).stdout)
                if v['Name'] != volume or v['Labels']['io.sbarbase.owner'] != owner:
                    raise RuntimeError('Cleanup volume ownership changed')
                native(['docker', 'volume', 'rm', volume])
        except Exception as error:
            report['passed'] = False
            report['cleanup_error'] = str(error)
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('scope', 'source_sha256', 'passed', 'error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
