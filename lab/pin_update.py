"""Pin updates under the upstream policy, one component at a time.

Usage:
  /usr/bin/python3 lab/pin_update.py show
  /usr/bin/python3 lab/pin_update.py verify
  /usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component db \
      --tag postgres:17.1 --digest sha256:<64 hex> --note "why this release"

Rules enforced here (see docs/engineering/UPSTREAM-UPDATE-POLICY.md):
- exactly one component changes per invocation;
- the digest must be a pinned sha256, never a floating tag;
- a dated entry in docs/upstream/ is created and must be completed before the
  adoption is treated as reviewed;
- the previous digest is recorded in the entry as the rollback pin.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
LOCKS=('lab/images.lock.json','lab/distro-image.lock.json','lab/storage-image.lock.json','lab/studio-image.lock.json','lab/realtime-image.lock.json','lab/functions-image.lock.json')
DIGEST=re.compile(r'sha256:[a-f0-9]{64}')
FLOATING=('latest','main','master','edge','stable')


def components():
    """Every pinned component across the lock files, with its current digest."""
    found={}
    for relative in LOCKS:
        path=ROOT/relative
        lock=json.loads(path.read_text())
        for key,value in lock.items():
            if isinstance(value,dict) and isinstance(value.get('id'),str):
                found[(relative,key)]=value
            elif key=='id' and isinstance(value,str):
                found[(relative,'id')]=lock
    return found


def show():
    for (relative,key),entry in sorted(components().items()):
        print(f'{relative:34} {key:12} {entry.get("tag",""):28} {entry["id"]}')


def verify():
    problems=[]
    for (relative,key),entry in sorted(components().items()):
        label=f'{relative}:{key}'
        digest=entry.get('id')
        if not isinstance(digest,str) or not DIGEST.fullmatch(digest):
            problems.append(label+' is not pinned to a sha256 digest')
        tag=entry.get('tag')
        if tag is None:
            problems.append(label+' has no tag recorded, so the pin table cannot be read')
        elif any(part in ('latest','main','master','edge','stable') for part in str(tag).split(':')):
            problems.append(label+' uses a floating tag '+str(tag))
    for relative in LOCKS:
        try:json.loads((ROOT/relative).read_text())
        except (OSError,ValueError) as error:problems.append(relative+': '+str(error))
    for problem in problems:print('problem  '+problem)
    print(f'pin verification: {len(problems)} problem(s), {len(components())} pinned component(s)')
    return not problems


def changes(file,component,tag,digest):
    """Return the single change this invocation would make, or refuse."""
    lock=json.loads((ROOT/file).read_text())
    if component not in lock or not isinstance(lock[component],dict) or 'id' not in lock[component]:
        raise SystemExit('Unknown component '+component+' in '+file)
    if not DIGEST.fullmatch(digest):raise SystemExit('Digest must be a pinned sha256:<64 hex>')
    if tag is not None and any(part in FLOATING for part in str(tag).split(':')):
        raise SystemExit('Floating tag refused; pin an exact tag')
    previous=lock[component]['id']
    if previous==digest:raise SystemExit('Digest is unchanged; nothing to stage')
    return lock,previous


def entry_path(component,tag):
    stamp=datetime.date.today().isoformat()
    return ROOT/'docs'/'upstream'/f'{stamp}-{component}-{str(tag).replace(":","-").replace("/","-")}.md'


def stage(file,component,tag,digest,note):
    lock,previous=changes(file,component,tag,digest)
    path=entry_path(component,tag)
    if path.exists():raise SystemExit('An entry for this component and version already exists: '+str(path))
    # The entry is written first: a pin change without a review record is refused.
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(f'''# Upstream update: {component} {tag}

Date: {datetime.date.today().isoformat()}
Component: {component} ({file})
Previous pin (rollback): `{previous}`
New pin: `{digest}`

## What changed

{note or 'TODO: summarise the upstream release notes and changelog.'}

## Affected surfaces

TODO: SQL bootstrap, HBA authority, Auth, REST, Storage, routing, tests.

## Breaking changes and migrations

TODO: list every breaking change and the required migration, or state none.

## Decision

TODO: adopt or defer, with the reason. Do not start the adoption before this
section is complete, and run the full Python and Bun suites plus the live
integration checks against the new pin.
''')
    lock[component]['id']=digest
    if tag is not None:lock[component]['tag']=tag
    (ROOT/file).write_text(json.dumps(lock,indent=2)+'\n')
    print('staged',component,'in',file)
    print('previous pin (rollback):',previous)
    print('new pin:',digest)
    print('complete the review entry before adopting:',path)
    print('then run: bun run test && /usr/bin/python3 -m unittest discover -s lab -p "test_*.py"')


def main():
    parser=argparse.ArgumentParser(description='sbarbase pin updates')
    parser.add_argument('command',choices=('show','verify','stage'))
    parser.add_argument('--file',choices=LOCKS)
    parser.add_argument('--component')
    parser.add_argument('--tag')
    parser.add_argument('--digest')
    parser.add_argument('--note',default='')
    args=parser.parse_args()
    if args.command=='show':show();return
    if args.command=='verify':raise SystemExit(0 if verify() else 1)
    if not (args.file and args.component and args.digest):
        raise SystemExit('stage requires --file, --component and --digest')
    stage(args.file,args.component,args.tag,args.digest,args.note)


if __name__=='__main__':main()