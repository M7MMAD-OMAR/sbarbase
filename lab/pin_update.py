"""Pin updates under the upstream policy, one component at a time.

Usage:
  /usr/bin/python3 lab/pin_update.py show
  /usr/bin/python3 lab/pin_update.py verify
  /usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component db \
      --tag postgres:17.1 --digest sha256:<64 hex> --note "why this release"
  /usr/bin/python3 lab/pin_update.py stage --file lab/realtime-image.lock.json \
      --tag public.ecr.aws/supabase/realtime:v2.143.4 --digest sha256:<64 hex>

Rules enforced here (see docs/engineering/UPSTREAM-UPDATE-POLICY.md):
- exactly one component changes per invocation;
- the digest must be a pinned sha256, never a floating tag;
- the repository stays the same, and the lock's repository digest proof is
  rewritten with the new digest so image_identity accepts the staged pin;
- a flat lock (one pin at its top level) takes no --component;
- a dated entry in docs/upstream/ is created and must be completed before the
  adoption is treated as reviewed;
- the previous digest is recorded in the entry as the rollback pin.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import image_identity

ROOT=Path(__file__).resolve().parent.parent
# Every lock file and its shape: the keys runtime code reads from a keyed lock (a deleted key is a
# missing pin), or None for a lock the runtime reads as one pin at its top level.
LOCKS={'lab/images.lock.json':('db','auth','rest'),'lab/distro-image.lock.json':None,'lab/storage-image.lock.json':None,
       'lab/studio-image.lock.json':('studio','meta'),'lab/realtime-image.lock.json':None,'lab/functions-image.lock.json':None}
FLOATING=('latest','main','master','edge','stable')


def floating(tag):
    """A moving version: latest, main and the like, alone or as a prefix such as latest-alpine,
    latest_alpine or stable.17.
    Only the version part counts, so a repository that happens to be called main is not refused."""
    last=str(tag).rsplit('/',1)[-1]
    if ':' not in last:return False
    version=last.rsplit(':',1)[1].lower()
    return any(version==word or (version.startswith(word) and version[len(word)] in '-_.') for word in FLOATING)


PIN_FIELDS=('tag','id','digests')


def flat(lock):
    """A lock written as one pin at its top level."""
    return isinstance(lock,dict) and any(field in lock for field in PIN_FIELDS)


def pins(relative,lock):
    """The pins of one parsed lock. A flat lock is one pin; in a keyed lock every key is a pin, so a
    removed, null or malformed pin is reported by verify instead of silently skipped."""
    if not isinstance(lock,dict):return {}
    if flat(lock):return {(relative,'id'):lock}
    return {(relative,key):value for key,value in lock.items()}


def components():
    """Every pinned component across the lock files, with its current digest."""
    found={}
    for relative in LOCKS:found.update(pins(relative,json.loads((ROOT/relative).read_text())))
    return found


def problem(entry):
    """Why one pin cannot be admitted, or None. The runtime admits a pin only through image_identity,
    so this holds the lock to the same proof."""
    if not isinstance(entry,dict):return 'is not a pin object'
    digest=entry.get('id')
    if not isinstance(digest,str) or not image_identity.DIGEST.fullmatch(digest):return 'is not pinned to a sha256 digest'
    tag=entry.get('tag')
    if tag is None:return 'has no tag recorded, so the pin table cannot be read'
    if floating(tag):return 'uses a floating tag '+str(tag)
    try:image_identity.reference(entry)
    except image_identity.IdentityError as error:return str(error)
    return None


def show():
    try:found=components()
    except (OSError,ValueError) as error:raise SystemExit('Cannot read the lock files: '+str(error))
    for (relative,key),entry in sorted(found.items()):
        entry=entry if isinstance(entry,dict) else {}
        print(f'{relative:34} {key:12} {str(entry.get("tag","")):28} {entry.get("id","(no digest)")}')


def shape_problems(relative,lock):
    """Lock-level reasons the runtime would refuse this parsed lock: its shape for the file, no pins,
    or a missing required key. Each pin's own proof is problem()."""
    required=LOCKS[relative]
    if required is None and not flat(lock):return [relative+' must pin one component at its top level']
    if required is not None and flat(lock):return [relative+' must pin each component under its own key']
    mine=pins(relative,lock)
    return ([] if mine else [relative+' pins no component'])+[f'{relative}:{key} is missing' for key in required or () if (relative,key) not in mine]


def lock_problems(relative,lock):
    """Every reason the runtime would refuse this parsed lock: its shape and each pin's own proof."""
    return shape_problems(relative,lock)+[f'{relative}:{key} {reason}' for (relative,key),entry in sorted(pins(relative,lock).items())
                                          if (reason:=problem(entry))]


def verify():
    problems,count=[],0
    for relative in LOCKS:
        try:lock=json.loads((ROOT/relative).read_text())
        except (OSError,ValueError) as error:problems.append(relative+': '+str(error));continue
        problems+=lock_problems(relative,lock)
        count+=len(pins(relative,lock))
    for line in problems:print('problem  '+line)
    print(f'pin verification: {len(problems)} problem(s), {count} pinned component(s)')
    return not problems


def label(file,component):
    """The name a review entry uses: the lock key, or the file's own name for a flat lock."""
    if component not in (None,'id'):return component
    return Path(file).name.removesuffix('.lock.json').removesuffix('-image')


def changes(file,component,tag,digest):
    """Return the single change this invocation would make, or refuse."""
    try:lock=json.loads((ROOT/file).read_text())
    except (OSError,ValueError) as error:raise SystemExit(f'Cannot read {file}: {error}')
    if not isinstance(lock,dict):raise SystemExit(file+' is not a lock object')
    required=LOCKS[file]
    if required is None and component not in (None,'id'):
        raise SystemExit(file+' pins one component at its top level; pass no --component')
    if required is not None and component not in required:
        raise SystemExit(f'{file} pins {", ".join(required)}; name one with --component')
    if (found:=lock_problems(file,lock)):
        raise SystemExit('The lock must verify before staging: '+'; '.join(found))
    entry=pins(file,lock)[(file,component or 'id')]  # present and valid: lock_problems checked both
    if not image_identity.DIGEST.fullmatch(digest):raise SystemExit('Digest must be a pinned sha256:<64 hex>')
    previous=entry['id']
    if previous==digest:raise SystemExit('Digest is unchanged; nothing to stage')
    current=entry['tag']
    tag=current if tag is None else str(tag)
    if floating(tag):raise SystemExit('Floating tag refused; pin an exact tag')
    if ':' not in tag.rsplit('/',1)[-1]:raise SystemExit('Tag needs an exact version: '+tag)
    name,version=tag.rsplit(':',1)
    repository=current.rsplit(':',1)[0]
    try:same=image_identity.repository(name)==image_identity.repository(repository)
    except image_identity.IdentityError:same=False
    if not same:raise SystemExit('The repository must stay '+repository+'; a different image is not an update')
    tag=repository+':'+version  # keep the lock's own spelling of the repository
    staged=dict(entry,tag=tag,id=digest,digests=[repository+'@'+digest])
    try:image_identity.reference(staged)
    except image_identity.IdentityError as error:raise SystemExit('Staged pin is not a valid immutable reference: '+str(error))
    return lock,entry,previous,staged


def entry_suffix(tag):
    """The release part of a review entry name, `<image>-<version>.md`; the full name is
    `<date>-<label>-<image>-<version>.md`."""
    return str(tag).rsplit('/',1)[-1].replace(':','-')+'.md'


def review_entries(tag,name=None,upstream=None):
    """Existing review entries for this release, on any date; with a label, only that component's."""
    return sorted((upstream or ROOT/'docs'/'upstream').glob(f'*-{name+"-" if name else ""}{entry_suffix(tag)}'))


def entry_path(name,tag):
    return ROOT/'docs'/'upstream'/f'{datetime.date.today().isoformat()}-{name}-{entry_suffix(tag)}'


def stage(file,component,tag,digest,note):
    lock,entry,previous,staged=changes(file,component,tag,digest)
    name=label(file,component)
    path=entry_path(name,staged['tag'])
    if (existing:=review_entries(staged['tag'],name)):raise SystemExit('An entry for this component and version already exists: '+str(existing[0]))
    text=f'''# Upstream update: {name} {staged['tag']}

- Date: {datetime.date.today().isoformat()}
- Component: {name} ({file})
- Previous pin (rollback): `{entry['tag']}` at `{previous}`
- New pin: `{staged['tag']}` at `{digest}`

## What changed

{note or 'TODO: summarise the upstream release notes and changelog.'}

## Affected surfaces

TODO: SQL bootstrap, HBA authority, Auth, REST, Storage, routing, tests.

## Breaking changes and migrations

TODO: list every breaking change and the required migration, or state none.

## Rollback

Revert the adoption change, or put this exact previous pin back in {file} and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{json.dumps({key:entry[key] for key in PIN_FIELDS if key in entry},indent=2)}
```

TODO: state whether this release runs schema or data migrations inside
environment databases; if it does, an image rollback is not enough, and the
backup taken before the change and its restore are the way back.

## Decision

TODO: adopt or defer, with the reason. Do not start the adoption before this
section is complete, and run the full Python and Bun suites plus the live
integration checks against the new pin.
'''
    # The entry is written first: a pin change without a review record is refused.
    path.parent.mkdir(parents=True,exist_ok=True)
    entry.update(staged)
    target=ROOT/file
    temporary=target.with_name(target.name+'.staging')
    try:
        path.write_text(text)
        temporary.write_text(json.dumps(lock,indent=2)+'\n')
        os.replace(temporary,target)  # a crash leaves the old lock or the new one, never a torn file
    except BaseException:
        temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)  # no review entry for a pin that did not change, so a retry is possible
        raise
    print('staged',name,'in',file)
    print('previous pin (rollback):',previous)
    print('new pin:',digest)
    print('complete the review entry before adopting:',path)
    print('then run: bun run test && /usr/bin/python3 -m unittest discover -s lab -p "test_*.py"')


def main():
    parser=argparse.ArgumentParser(description='sbarbase pin updates')
    parser.add_argument('command',choices=('show','verify','stage'))
    parser.add_argument('--file',choices=tuple(LOCKS))
    parser.add_argument('--component')
    parser.add_argument('--tag')
    parser.add_argument('--digest')
    parser.add_argument('--note',default='')
    args=parser.parse_args()
    if args.command=='show':show();return
    if args.command=='verify':raise SystemExit(0 if verify() else 1)
    if not (args.file and args.digest):
        raise SystemExit('stage requires --file and --digest, plus --component for a lock with several pins')
    stage(args.file,args.component,args.tag,args.digest,args.note)


if __name__=='__main__':main()