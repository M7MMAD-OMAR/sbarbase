"""Explicit, operator-driven container generation migration.

Design: docs/engineering/CONTAINER-GENERATION-MIGRATION.md. This module adds one caller of the
owned, single-attempt HBA pipeline; it never adds a second way to publish rules and
it never resets a registry or re-mints a generation on retry.

The migration record is a private directory beside the generation pin
(see hba_generation.MIGRATION). Its presence blocks ordinary startup, worker
preflight and a repeated migration until an operator either completes the
migration or reconciles the interrupted one. Every effect is checkpointed before
the next one begins, an uncertain step leaves the record in place, and the record
is removed only after everything else is durable.

Order of effects, as implemented here:

1. the intent is published exclusively and fsynced before any effect;
2. the retired container is stopped (with the operator's assertion) or verified
   gone, and its last observed identity is captured;
3. the retired generation's record and its last observed HBA digest are archived
   before the pin is replaced;
4. the retired container is removed and the replacement is created on the same
   pgdata volume (or, for a version 3 intent, on the rebuilt volume it names),
   carrying its resource tier and its per-device block IO limits;
5. the new generation is minted once, durably, then registered in the replacement,
   and the pin is replaced by an explicit archive-then-publish;
6. the desired rules are published through the owned pipeline with parser and
   reload acknowledgment;
7. the rules are re-derived from the inventory and compared against the retired
   digest, the outcome is archived, and only then is the record removed.

The pin is replaced in step 5, immediately before the rules publication, because
the owned pipeline reads the pin in hba_generation.require (through
hba_apply.execute) and in hba_generation.read_existing (through
SourceHBA.publish): a pin written after the rules are acknowledged cannot gate
that publication. Step 7 is where the retired generation's audit is completed, so
an interrupted migration never claims rules it did not re-derive.
"""
import json
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import asdict
from pathlib import Path
import effect_receipt
import database_volume
import hba_apply
import hba_authority as authority
import hba_generation
import hba_journal
import hba_runtime
import hba_settlement
import hba_startup
import hba_target
import resource_policy


CHECKPOINTS='checkpoints'
ARCHIVE='hba-migration-archive'
EVIDENCE='evidence.json'
COMPLETED='completed.json'
REMOVING='hba-migration.removing'
LIMIT=hba_journal.MAX_BYTES
PGDATA='/var/lib/postgresql/data'
HBA_PATH='/etc/postgresql/pg_hba.conf'
REVISION=re.compile(r'# sbarbase-hba-revision: [a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\n')
PHASES=('old-captured','retired-archived','new-captured','generation-minted',
        'generation-initialized','rules-published','archived')
INTENT_FIELDS={'version','migration','generation','old','volume','inventory','retired_state'}
# Version 2 records the replacement's image when it differs from the retired one: a generation
# change onto the image of a newer distribution lock. The image is fixed when the intent is
# published, so resuming never reads a lock that may have moved since.
INTENT_FIELDS_IMAGE=INTENT_FIELDS|{'image'}
# Version 3: onto another pgdata volume (a rebuilt cluster, P3), with or without another image.
# Version 4: a version 3 change that supersedes a stuck migration (P3 part 4). Its retired
# container is gone, so what it captured and archived comes from the superseded migration's archive.
INTENT_SHAPES={(1,frozenset(INTENT_FIELDS)),(2,frozenset(INTENT_FIELDS_IMAGE)),
              (3,frozenset(INTENT_FIELDS|{'new_volume'})),(3,frozenset(INTENT_FIELDS_IMAGE|{'new_volume'})),
              (4,frozenset(INTENT_FIELDS|{'new_volume','supersedes'})),
              (4,frozenset(INTENT_FIELDS_IMAGE|{'new_volume','supersedes'}))}
SUPERSEDED='superseded.json'
RETIRED_STATES=('stopped-will-not-return','absent-verified')
PLAN_FIELDS={'name','owner','image','tier','memory','cpus','network','env_file','command'}


def record_directory(state):
    return Path(state)/hba_generation.MIGRATION


def intent_path(state):
    return record_directory(state)/hba_generation.MIGRATION_INTENT


def archive_directory(state,migration):
    """One durable, append-only archive per migration, outside the record directory.

    The record is removed when the migration completes; this archive is what stays
    auditable: the retired generation's record, the retired pin's exact bytes, the
    last observed HBA digest and the migration's own evidence.
    """
    state=Path(state)
    authority.exact(migration,authority.UUID)
    parent=_private_directory(state/ARCHIVE,state,'Generation migration archive root')
    return _private_directory(parent/migration,parent,'Generation migration archive')


def present(state):
    return hba_startup.present(record_directory(state))


def require_absent(state):
    """A repeated migration refuses while any record, torn or whole, is present."""
    if present(state):
        raise RuntimeError('A generation migration record already exists; reconcile it before another migration')
    return state


def require_present(state):
    if not present(state):
        raise RuntimeError('No generation migration record to reconcile')
    return record_directory(state)


def validate(record):
    if not isinstance(record,dict) or type(record.get('version')) is not int \
            or (record['version'],frozenset(record)) not in INTENT_SHAPES:
        raise ValueError('Invalid HBA migration intent shape')
    for key in ('migration','generation'):authority.exact(record[key],authority.UUID)
    old=record['old']
    if not isinstance(old,dict) or set(old)!={'container_id','name','owner','image'}:
        raise ValueError('Invalid migration retired target')
    authority.exact(old['container_id'],authority.HEX)
    hba_target.policy(old['name'],old['owner'],old['image'])
    if not isinstance(record['volume'],str) or not record['volume']:
        raise ValueError('Migration pgdata volume required')
    authority.exact(record['inventory'],authority.HEX)
    if record['retired_state'] not in RETIRED_STATES:
        raise ValueError('Migration retired state must be explicit')
    if 'image' in record:
        hba_target.policy(old['name'],old['owner'],record['image'])
        if record['image']==old['image']:
            raise ValueError('A migration intent records an image only when it changes')
    if 'new_volume' in record:
        if not database_volume.rebuilt(record['new_volume']) or record['new_volume']==record['volume']:
            raise ValueError('A migration intent records another pgdata volume only when it is a database volume that changes')
    if 'supersedes' in record:
        authority.exact(record['supersedes'],authority.UUID)
        if record['supersedes']==record['migration'] or record['retired_state']!='stopped-will-not-return':
            raise ValueError('A superseding intent names another migration whose retired container was stopped')
    return record


def target_volume(intent):
    """The pgdata volume the replacement mounts: the one the intent recorded, or the retired one."""
    return intent.get('new_volume',intent['volume'])


# A data directory of the major version the image's server runs (postgres -V: "postgres (PostgreSQL) 17.6").
PROBE=('v=$(cat /data/PG_VERSION) && case "$v" in ""|*[!0-9]*) exit 1;; esac '
       '&& postgres -V | grep -q " $v\\."')


def retired_volumes(state):
    """Volumes an earlier move left behind: kept, and outdated from the moment it finished."""
    root=Path(state)/ARCHIVE
    found=set()
    for path in sorted(root.glob('*/'+EVIDENCE)) if root.is_dir() else []:
        record=_decode_envelope(_read_private(path,'Generation migration evidence'),'Generation migration evidence')
        if record.get('target_volume',record.get('volume'))!=record.get('volume'):found.add(record['volume'])
    return found


def require_cluster_volume(docker,volume,owner,image,state=None):
    """A volume the database may move onto: it exists, the runtime owns it, and it holds a
    PostgreSQL data directory (an empty one would start a new, empty cluster). Checked before the
    intent is written and before the retired container is stopped, and again on each rerun until
    the replacement runs on it: a read-only helper on the image the replacement will run, without
    network."""
    if state is not None and volume in retired_volumes(state):
        raise RuntimeError('The new pgdata volume is one an earlier move left behind; it holds an outdated cluster')
    inspected=docker('volume','inspect',volume,check=False)
    if inspected.returncode:
        raise RuntimeError('The new pgdata volume does not exist; it must already hold the rebuilt cluster')
    try:labels=json.loads(inspected.stdout)[0].get('Labels') or {}
    except (ValueError,IndexError,AttributeError,TypeError):raise RuntimeError('The new pgdata volume cannot be inspected')
    if labels.get('io.sbarbase.owner')!=owner:
        raise RuntimeError('The new pgdata volume is not owned by the runtime that would mount it')
    probe=docker('run','--rm','--pull','never','--network','none','--memory','64m','--memory-swap','64m',
                 '--cpus','0.25','--pids-limit','32','--entrypoint','sh','-v',volume+':/data:ro',image,
                 '-c',PROBE,check=False)
    if probe.returncode:
        raise RuntimeError('The new pgdata volume holds no PostgreSQL data directory of the major version the image runs; '
                           'it must already hold the rebuilt cluster')
    return True


def target_image(intent):
    """The image the replacement runs: the one the intent recorded, or the retired one."""
    return intent.get('image',intent['old']['image'])


def validate_checkpoint(record):
    if not isinstance(record,dict) or not {'version','phase','migration','intent'}.issubset(record):
        raise ValueError('Invalid HBA migration checkpoint shape')
    if type(record['version']) is not int or record['version']!=1 or record['phase'] not in PHASES:
        raise ValueError('Invalid HBA migration checkpoint shape')
    authority.exact(record['migration'],authority.UUID)
    authority.exact(record['intent'],authority.HEX)
    try:authority.canonical(record)
    except (TypeError,ValueError):
        raise ValueError('HBA migration checkpoint is not canonical text') from None
    return record


def _read_private(path,label):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'r',encoding='utf-8',newline='') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_size>LIMIT:
            raise ValueError(label+' must be a private owned regular file')
        text=source.read(LIMIT+1)
        if len(text.encode())>LIMIT:raise ValueError(label+' too large')
    return text


def _decode_envelope(text,label):
    envelope=json.loads(text,object_pairs_hook=authority.unique_object)
    if not isinstance(envelope,dict) or set(envelope)!={'record','checksum'}:
        raise ValueError('Invalid '+label+' envelope')
    if envelope['checksum']!=authority.digest(authority.canonical(envelope['record'])):
        raise ValueError(label+' checksum mismatch')
    return envelope['record']


def _publish_once(path,record,label):
    """Publish a record a rerun derives again: an existing one is kept when it is the same (a kill
    between this write and its checkpoint), and refused when it differs."""
    if hba_startup.present(path):
        if _decode_envelope(_read_private(path,label),label)!=record:
            raise RuntimeError(label+' differs from the one an earlier run wrote; preserve the migration record')
        return record
    return _publish_exclusive(path,record,label)


def _publish_exclusive(path,record,label,limit=LIMIT):
    """Publish a record whole under a name that must not exist yet. It is written and synced
    under a private temporary name first, then linked to its name (which refuses an existing
    one), so a kill leaves either no record or the whole one, never a torn file."""
    text=authority.encode(record)
    if len(text.encode())>limit:raise ValueError(label+' too large')
    pending=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.pending')
    descriptor=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            output.write(text);output.flush();os.fsync(output.fileno())
        os.link(pending,path)
    finally:
        pending.unlink(missing_ok=True)
    effect_receipt.sync_directory(path.parent)
    if _decode_envelope(_read_private(path,label),label)!=record:
        raise RuntimeError(label+' changed before use')
    return record


def _private_directory(path,parent,label):
    if not hba_startup.present(path):
        os.mkdir(path,0o700)
        effect_receipt.sync_directory(parent)
    metadata=path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700 or metadata.st_uid!=os.getuid():
        raise ValueError(label+' is not private')
    return path


def load(state):
    require_present(state)
    return validate(_decode_envelope(_read_private(intent_path(state),'HBA migration intent'),'HBA migration intent'))


def publish_intent(state,*,target,generation,volume,inventory,retired_state,image=None,new_volume=None,
                   migration=None,supersedes=None):
    """Publish the exclusive, fsynced migration intent; an existing record refuses. An image other
    than the retired one makes it a generation change onto that image (version 2); another pgdata
    volume makes it a change onto that volume (version 3, with or without the image); naming the
    stuck migration it supersedes makes it version 4, under the id that migration's archive gave."""
    state=Path(state)
    if retired_state not in RETIRED_STATES:
        raise ValueError('Migration retired state must be explicit')
    if retired_state=='absent-verified':
        raise RuntimeError('Absent retired container migration is unsupported without a verified archive; '
                           'preserve the generation pin and reconcile the retired state explicitly')
    if supersedes is None and pending_superseded(state) is not None:
        # Its retired container is gone; only the superseding migration can start from its archive.
        raise RuntimeError('A stuck migration was superseded and its replacement migration is not started yet; '
                           'finish it with lab/cluster_takeover.py')
    record={'version':1,'migration':migration or str(uuid.uuid4()),'generation':generation,
            'old':asdict(target),'volume':volume,'inventory':inventory,'retired_state':retired_state}
    if image is not None and image!=target.image:
        record={**record,'version':2,'image':image}
    if new_volume is not None and new_volume!=volume:
        record={**record,'version':3,'new_volume':new_volume}
    if supersedes is not None:
        if record['version']!=3:
            raise ValueError('A superseding migration moves onto a rebuilt volume')
        record={**record,'version':4,'supersedes':supersedes}
    record=validate(record)
    try:os.mkdir(record_directory(state),0o700)
    except FileExistsError:
        raise RuntimeError('A generation migration record already exists; reconcile it before another migration') from None
    metadata=record_directory(state).lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700 or metadata.st_uid!=os.getuid():
        raise ValueError('Generation migration record directory is not private')
    effect_receipt.sync_directory(state)
    # Written whole and linked to its name, as every record here: a kill leaves the directory
    # empty or holding only a temporary copy (removed by discard_unpublished), or the whole intent.
    _publish_exclusive(intent_path(state),record,'HBA migration intent')
    return record


def _checkpoint_path(state,phase):
    if phase not in PHASES:raise ValueError('Unknown generation migration phase')
    return record_directory(state)/CHECKPOINTS/(phase+'.json')


def checkpoint(state,phase,payload):
    """Publish one immutable checkpoint; an existing phase is re-bound, never rewritten."""
    path=_checkpoint_path(state,phase)
    record=validate_checkpoint({'version':1,'phase':phase,**payload})
    if hba_startup.present(path):
        stored=read_checkpoint(state,phase)
        if authority.canonical(stored)!=authority.canonical(record):
            raise RuntimeError('Conflicting generation migration checkpoint')
        return stored
    _private_directory(path.parent,_private_directory(record_directory(state),Path(state),'Generation migration record directory'),
                       'Generation migration checkpoint directory')
    _publish_exclusive(path,record,'HBA migration checkpoint')
    return record


def read_checkpoint(state,phase):
    record=_decode_envelope(_read_private(_checkpoint_path(state,phase),'HBA migration checkpoint'),'HBA migration checkpoint')
    record=validate_checkpoint(record)
    if record['phase']!=phase:raise ValueError('HBA migration checkpoint phase mismatch')
    return record


def done(state,phase):
    return hba_startup.present(_checkpoint_path(state,phase))


def _bind(intent,base):
    return {'migration':intent['migration'],'intent':authority.digest(authority.canonical(intent)),**base}


def _bindings(state,intent):
    """Every present checkpoint must belong to this exact intent."""
    digest=authority.digest(authority.canonical(intent))
    for phase in PHASES:
        if done(state,phase):
            record=read_checkpoint(state,phase)
            if record['migration']!=intent['migration'] or record['intent']!=digest:
                raise RuntimeError('Generation migration checkpoint belongs to another intent')
    return state


def _retired_directory(state,intent):
    return archive_directory(state,intent['migration'])


def _volume(mounts):
    entry=next((m for m in mounts if m['destination']==PGDATA),None)
    if entry is None:raise RuntimeError('pgdata mount missing')
    name=entry['name'] or entry['source']
    if not name:raise RuntimeError('pgdata volume identity unavailable')
    return name


def verify_absent(docker,intent):
    """The retired container must be verifiably gone; an inspection error is fatal."""
    listing=docker('ps','-a','--no-trunc','--format','{{.ID}} {{.Names}}',check=False)
    if listing.returncode:raise RuntimeError('Retired absence inspection unavailable')
    for line in listing.stdout.splitlines():
        fields=line.split()
        if not fields:continue
        names=set(fields[1].split(',')) if len(fields)>1 else set()
        if fields[0].startswith(intent['old']['container_id']) or intent['old']['name'] in names:
            raise RuntimeError('Retired container inspection unavailable; absence is not proven')
    return True


def retired_identity(docker,intent):
    """Inspect the retired container by its exact captured id. Absence is fatal here."""
    result=docker('inspect',intent['old']['container_id'],check=False)
    if result.returncode:raise RuntimeError('Retired container inspection unavailable')
    try:entries=json.loads(result.stdout)
    except ValueError:raise RuntimeError('Retired container inspection unavailable')
    if not isinstance(entries,list) or len(entries)!=1 or not isinstance(entries[0],dict):
        raise RuntimeError('Retired container inspection unavailable')
    info=entries[0]
    if (info.get('Id')!=intent['old']['container_id'] or info.get('Name')!='/'+intent['old']['name']
            or info.get('Image')!=intent['old']['image']
            or info.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=intent['old']['owner']):
        raise RuntimeError('Retired container identity changed')
    return info


def _copy_retired(docker,state,intent,source,destination,label):
    """Read one file out of the stopped retired container; a failure is fatal."""
    result=docker('cp',intent['old']['container_id']+':'+source,destination,check=False)
    if result.returncode or not hba_startup.present(destination):
        raise RuntimeError('Retired '+label+' unavailable')
    return destination


def retired_hba_digest(docker,state,intent):
    path=_copy_retired(docker,state,intent,HBA_PATH,_retired_directory(state,intent)/'retired-pg_hba.conf','HBA rules')
    return authority.digest(path.read_text())


def retired_registry(docker,state,intent):
    """No active authority anywhere in the retired state. Unreadable is fatal."""
    path=_copy_retired(docker,state,intent,authority.PATH,_retired_directory(state,intent)/'retired-registry.json','authority registry')
    text=path.read_text()
    record=authority.decode(text,intent['generation'])
    active=[token for token,item in record['operations'].items() if item['state']=='active']
    if active:raise RuntimeError('Retired HBA authority is active; reconcile it before migration')
    return {'generation':record['generation'],'operations':len(record['operations']),'registry':authority.digest(text)}


def retired_archive(docker,state,intent):
    """Archive the retired generation's record and last observed digest, before the pin."""
    pin_path=Path(state)/hba_generation.NAME
    pin_bytes=pin_path.read_text()
    pin=hba_generation.load(state)
    if pin['generation']!=intent['generation'] or pin['target']!=intent['old']:
        raise RuntimeError('Retired generation pin changed before archiving')
    digest=retired_hba_digest(docker,state,intent)
    registry=retired_registry(docker,state,intent)
    return _publish_once(_retired_directory(state,intent)/'retired-generation.json',
                              {'version':1,'kind':'retired-generation','migration':intent['migration'],
                               'generation':intent['generation'],'target':intent['old'],'pin':pin,
                               'pin_digest':authority.digest(pin_bytes),'hba_digest':digest,'registry':registry},
                              'Retired generation archive')


def capture_retired(docker,state,intent):
    """Stop and observe the retired container, or verify that it is gone."""
    if intent['retired_state']=='absent-verified':
        verify_absent(docker,intent)
        return {'container_id':None,'identity':'absent-verified','mounts':[],'volume':intent['volume'],
                'hba_digest':None,'registry':{'generation':intent['generation'],'operations':0,'registry':'not-observable'},
                'authority':'not-observable-with-retired-container'}
    info=retired_identity(docker,intent)
    if info.get('State',{}).get('Running'):
        stopped=docker('stop',intent['old']['container_id'],check=False)
        if stopped.returncode:raise RuntimeError('Retired container could not be stopped')
        info=retired_identity(docker,intent)
        if info.get('State',{}).get('Running'):raise RuntimeError('Retired container is still running')
    mounts=hba_target.mounts(info)
    volume=_volume(mounts)
    if volume!=intent['volume']:
        raise RuntimeError('Retired pgdata volume differs from the recorded volume')
    digest=retired_hba_digest(docker,state,intent)
    registry=retired_registry(docker,state,intent)
    return {'container_id':intent['old']['container_id'],'identity':'stopped-observed','mounts':mounts,
            'volume':volume,'hba_digest':digest,'registry':registry,'authority':'no-active-operation'}


def validate_replacement(intent,replacement):
    if not isinstance(replacement,dict) or set(replacement)!=PLAN_FIELDS:
        raise ValueError('Invalid generation migration replacement')
    for key in ('name','owner'):
        if replacement[key]!=intent['old'][key]:
            raise RuntimeError('Generation migration replacement changes the database identity')
    if replacement['image']!=target_image(intent):
        raise RuntimeError('Generation migration replacement image differs from the one its intent recorded')
    flags=resource_policy.container_flags(replacement['tier'])
    if (flags['memory'] is not None and flags['memory']!=replacement['memory']) or (flags['cpus'] is not None and float(flags['cpus'])!=float(replacement['cpus'])):
        raise RuntimeError('Generation migration tier disagrees with the requested limits')
    if not isinstance(replacement['network'],str) or not replacement['network']:
        raise ValueError('Generation migration network required')
    if not isinstance(replacement['env_file'],str) or not replacement['env_file']:
        raise ValueError('Generation migration environment file required')
    if not isinstance(replacement['command'],(tuple,list)) or not all(isinstance(item,str) for item in replacement['command']):
        raise ValueError('Generation migration command must be explicit text')
    return {**replacement,'command':tuple(replacement['command']),'flags':flags}


def retired_removed(docker,intent):
    """True when the retired container is proven gone (a rerun after its removal), False when it
    is still there; a failed inspection or listing is fatal rather than read as either."""
    if docker('inspect',intent['old']['container_id'],check=False).returncode==0:return False
    listing=docker('ps','-a','--no-trunc','--format','{{.ID}} {{.Names}}',check=False)
    if listing.returncode:raise RuntimeError('Retired container inspection unavailable')
    for line in listing.stdout.splitlines():
        fields=line.split()
        if fields and fields[0].startswith(intent['old']['container_id']):
            raise RuntimeError('Retired container inspection unavailable')
    return True


def replacement_identity(docker,identifier,intent,replacement):
    """The replacement container's inspection, refused unless it is exactly what was asked for."""
    result=docker('inspect',identifier,check=False)
    if result.returncode:return None
    try:info=json.loads(result.stdout)[0]
    except (ValueError,IndexError,KeyError,TypeError):raise RuntimeError('Replacement container inspection unavailable')
    labels=info.get('Config',{}).get('Labels',{}) or {}
    if (not isinstance(info.get('Id'),str) or info['Id']==intent['old']['container_id']
            or info.get('Name')!='/'+replacement['name'] or info.get('Image')!=replacement['image']
            or labels.get('io.sbarbase.owner')!=replacement['owner']
            or labels.get('io.sbarbase.tier')!=replacement['flags']['label']):
        raise RuntimeError('Replacement container identity changed')
    if _volume(hba_target.mounts(info))!=target_volume(intent):
        raise RuntimeError('Replacement pgdata volume differs from the recorded volume')
    return info


def ensure_running(docker,info):
    """Start a stopped replacement (a host restart, or a run killed before readiness)."""
    if info.get('State',{}).get('Running'):return False
    started=docker('start',info['Id'],check=False)
    if started.returncode:raise RuntimeError('Replacement container could not be started')
    return True


def create_replacement(docker,state,intent,replacement):
    """Remove the retired container and create its replacement on the target volume (the same
    one, or for a version 3 intent the rebuilt one it names).

    A rerun after the removal (the run stopped before the new-captured checkpoint, for
    instance while waiting for readiness) adopts the replacement it had created instead of
    creating a second one, starting it again when it is stopped."""
    captured=read_checkpoint(state,'old-captured')['retired']
    if captured['container_id'] is not None:
        if retired_removed(docker,intent):
            info=replacement_identity(docker,replacement['name'],intent,replacement)
            if info is not None:
                ensure_running(docker,info)
                return info['Id'],hba_target.mounts(info)
        else:
            info=retired_identity(docker,intent)
            if info.get('State',{}).get('Running'):raise RuntimeError('Retired container restarted during migration')
            removed=docker('rm',intent['old']['container_id'],check=False)
            if removed.returncode:raise RuntimeError('Retired container could not be removed')
    if target_volume(intent)!=intent['volume'] and docker('volume','inspect',target_volume(intent),check=False).returncode:
        # docker run would create an empty volume, and PostgreSQL would initialize a new cluster on it.
        raise RuntimeError('The new pgdata volume does not exist; it must already hold the rebuilt cluster')
    flags=replacement['flags']
    arguments=['run','-d','--name',replacement['name'],'--label','io.sbarbase.owner='+replacement['owner'],
               '--label','io.sbarbase.tier='+flags['label'],'--network',replacement['network'],
               '--memory',replacement['memory'],'--memory-swap',replacement['memory'],'--cpus',str(replacement['cpus']),
               '--pids-limit',str(flags['pids']),'--cpu-shares',str(flags['shares']),'--blkio-weight',str(flags['weight']),
               *resource_policy.io_flags(replacement['tier']),
               '--log-opt','max-size=5m','--log-opt','max-file=2','--env-file',replacement['env_file'],
               '-v',target_volume(intent)+':'+PGDATA,replacement['image'],*replacement['command']]
    created=docker(*arguments,check=False)
    if created.returncode:raise RuntimeError('Generation migration replacement could not be created')
    identifier=created.stdout.strip()
    authority.exact(identifier,authority.HEX)
    info=json.loads(docker('inspect',identifier).stdout)[0]
    if (info.get('Name')!='/'+replacement['name'] or info.get('Image')!=replacement['image']
            or info.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=replacement['owner']
            or info.get('Config',{}).get('Labels',{}).get('io.sbarbase.tier')!=flags['label']):
        raise RuntimeError('Replacement container identity changed')
    mounts=hba_target.mounts(info)
    if _volume(mounts)!=target_volume(intent):
        raise RuntimeError('Replacement pgdata volume differs from the recorded volume')
    return identifier,mounts


def wait_ready(docker,identifier,deadline=120):
    """Stable readiness on the same data directory before any registry write."""
    end=time.monotonic()+deadline
    while True:
        probe=docker('exec',identifier,'pg_isready','-h','127.0.0.1',check=False)
        if probe.returncode==0:
            role=docker('exec','-i',identifier,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin',
                        '-d','postgres',data="SELECT to_regrole('supabase_privileged_role') IS NOT NULL;",check=False)
            if role.returncode==0 and role.stdout.strip()=='t':return True
        if time.monotonic()>end:raise RuntimeError('Replacement database readiness timed out')
        time.sleep(1)


def marker_present(docker,identifier):
    script=(f'[ -d {authority.MARKER} ] && [ ! -L {authority.MARKER} ] && [ -f {authority.PATH} ] '
            f'&& [ ! -L {authority.PATH} ] && printf yes || printf no')
    observed=docker('exec',identifier,'sh','-c',script).stdout.strip()
    if observed not in ('yes','no'):raise RuntimeError('Backend authority observation unavailable')
    return observed=='yes'


def initialize_generation(docker,state,intent,minted,new_target):
    """Mint-once generation registration, then the pin replacement. Never a rewrite."""
    generation=minted['generation']
    pin=Path(state)/hba_generation.NAME
    archived=_retired_directory(state,intent)/hba_generation.NAME
    if hba_startup.present(pin):
        existing=hba_generation.load(state)
        if not (existing['target']==asdict(new_target) and existing['generation']==generation):
            if existing['target']!=intent['old'] or existing['generation']!=intent['generation']:
                raise RuntimeError('Generation pin changed during migration')
            os.replace(pin,archived)
            effect_receipt.sync_directory(archived.parent)
            effect_receipt.sync_directory(pin.parent)
    elif not hba_startup.present(archived):
        raise RuntimeError('Retired generation pin is missing')
    if not hba_startup.present(pin):
        saved=hba_generation.validate(_decode_envelope(_read_private(archived,'Retired generation pin'),'Retired generation pin'))
        if saved['generation']!=intent['generation'] or saved['target']!=intent['old']:
            raise RuntimeError('Retired generation pin archive changed')
        # Written whole, as every record here: a kill never leaves a torn pin.
        _publish_exclusive(pin,hba_generation.validate({'version':1,'generation':generation,'target':asdict(new_target)}),
                           'HBA generation pin',hba_generation.LIMIT)
        hba_generation.load(state)
    if docker('inspect','--format','{{.Id}}',new_target.container_id).stdout.strip()!=new_target.container_id:
        raise RuntimeError('Replacement container identity changed')
    if not marker_present(docker,new_target.container_id):
        authority.initialize(docker,new_target.container_id,generation)
    return hba_generation.read_existing(docker,state,target=new_target)


def publish_rules(docker,state,lease,new_target,desired):
    """The one owned single-attempt publication. No second writer is introduced."""
    writer=hba_runtime.SourceHBA(docker,state,new_target.name,new_target.owner,new_target.image,startup=lease)
    # Exactly what SourceHBA.ready would bind; publish re-verifies the pin and the container.
    writer.target=new_target
    return writer.publish(desired)


def rules_of(text):
    """The rules without the one revision line this pipeline prepends."""
    if not text.endswith('\n'):raise RuntimeError('HBA rules must be complete text')
    lines=text.splitlines(keepends=True)
    if lines and REVISION.fullmatch(lines[0]):return ''.join(lines[1:])
    return text


def compare_rules(docker,state,intent,captured,minted,desired):
    """Re-derive the rules from the inventory and state any difference explicitly."""
    observed=docker('exec',minted['container_id'],'cat',HBA_PATH).stdout
    if not observed.endswith('\n'):raise RuntimeError('Observed HBA rules are not complete text')
    retired_file=_retired_directory(state,intent)/'retired-pg_hba.conf'
    retired=retired_file.read_text() if hba_startup.present(retired_file) else None
    observed_rules=rules_of(observed)
    retired_rules=rules_of(retired) if retired is not None else None
    same_rules=retired_rules is not None and authority.digest(retired_rules)==authority.digest(observed_rules)
    inventory_holds=observed_rules==desired
    if inventory_holds and same_rules:difference='identical'
    elif inventory_holds:difference='re-derived-from-inventory'
    else:difference='different'
    return {'inventory_digest':authority.digest(desired),'observed_rules_digest':authority.digest(observed_rules),
            'observed_file_digest':authority.digest(observed),
            'retired_rules_digest':authority.digest(retired_rules) if retired_rules is not None else None,
            'retired_hba_digest':captured['hba_digest'],
            'inventory_matches_observed':inventory_holds,'retired_matches_observed':same_rules,'difference':difference}


def archive_evidence(state,intent,captured,minted,new_target,outcome,comparison):
    record={'version':1,'kind':'generation-migration','migration':intent['migration'],
            'retired_generation':intent['generation'],'retired_container':intent['old']['container_id'],
            'retired_state':intent['retired_state'],'volume':intent['volume'],
            'new_generation':minted['generation'],'new_container':minted['container_id'],
            'target':{'name':new_target.name,'owner':new_target.owner,'image':new_target.image},
            'retired_image':intent['old']['image'],'target_volume':target_volume(intent),
            'hba_operation':outcome['journal']['token'],'hba_application':outcome['application'],
            'hba_activation':outcome['activation'],'rules':comparison,
            'authority_observation':captured.get('authority')}
    if 'supersedes' in intent:record={**record,'supersedes':intent['supersedes']}
    return _publish_once(archive_directory(state,intent['migration'])/EVIDENCE,record,'Generation migration evidence')


def read_evidence(state,migration):
    """Read one archived migration outcome; it outlives the record on purpose."""
    return _decode_envelope(_read_private(archive_directory(state,migration)/EVIDENCE,'Generation migration evidence'),
                            'Generation migration evidence')


def outcome_for(state,token):
    """The archived publication outcome, read back, never inferred from a call."""
    return hba_settlement.read(state,token)




def _remove_record(state):
    """Remove the finished record in one step: it is renamed away first, so a kill can never leave
    part of it behind under the record's name; the renamed copy is then deleted."""
    state=Path(state)
    removing=state/REMOVING
    if hba_startup.present(removing):shutil.rmtree(removing)
    os.rename(record_directory(state),removing)
    effect_receipt.sync_directory(state)
    shutil.rmtree(removing)
    effect_receipt.sync_directory(state)


def discard_unpublished(state):
    """A record directory without its intent was never executed: the intent is linked into it
    whole, and execute reads the intent before any effect. Such a record (a kill between creating
    the directory and linking the intent, which may leave the temporary copy) is removed so the
    migration can start again. Anything else, a torn intent included, is kept and refused."""
    directory=record_directory(state)
    if not present(state):return False
    entries=os.listdir(directory)
    if any(not (name.startswith('.') and name.endswith('.pending')) for name in entries):return False
    for name in entries:(directory/name).unlink()
    os.rmdir(directory)
    effect_receipt.sync_directory(Path(state))
    return True


def _read_superseded(path):
    record=_decode_envelope(_read_private(path,'Superseded migration record'),'Superseded migration record')
    if (not isinstance(record,dict) or record.get('version')!=1 or record.get('kind')!='superseded-migration'
            or set(record)!=SUPERSEDED_FIELDS):
        raise RuntimeError('The superseded migration record is malformed; preserve it for review')
    intent=validate(record['intent'])
    authority.exact(record['superseded_by'],authority.UUID)
    if intent['migration']!=record['migration'] or record['superseded_by']==record['migration']:
        raise RuntimeError('The superseded migration record is malformed; preserve it for review')
    if not isinstance(record['checkpoints'],dict) or set(record['checkpoints'])!={'old-captured','retired-archived'}:
        raise RuntimeError('The superseded migration record is malformed; preserve it for review')
    for phase in ('old-captured','retired-archived'):
        stored=validate_checkpoint(record['checkpoints'][phase])
        if stored['phase']!=phase or stored['migration']!=intent['migration'] \
                or stored['intent']!=authority.digest(authority.canonical(intent)):
            raise RuntimeError('The superseded migration record does not hold its own checkpoints; preserve it for review')
    return record


SUPERSEDED_FIELDS={'version','kind','migration','superseded_by','intent','checkpoints','replacement','onto'}


def superseded_by(state,intent):
    """The archived record of the stuck migration a version 4 intent supersedes."""
    path=Path(state)/ARCHIVE/intent['supersedes']/SUPERSEDED
    if not hba_startup.present(path):
        raise RuntimeError('The superseded migration archive is missing; preserve the migration record')
    record=_read_superseded(path)
    if record['superseded_by']!=intent['migration']:
        raise RuntimeError('The superseded migration archive names another migration; preserve the migration record')
    return record


def pending_superseded(state):
    """A stuck migration archived and removed whose superseding intent is not published yet (a run
    stopped between the two), or None. More than one refuses."""
    root=Path(state)/ARCHIVE
    found=[]
    for path in sorted(root.glob('*/'+SUPERSEDED)) if root.is_dir() else []:
        record=_read_superseded(path)
        # Waiting only while neither migration finished: a stuck one finished after all (its own
        # evidence) was never superseded.
        if not hba_startup.present(root/record['superseded_by']/EVIDENCE) and not hba_startup.present(path.parent/EVIDENCE):
            found.append(record)
    if len(found)>1:
        raise RuntimeError('More than one superseded migration waits; preserve the archive for review')
    return found[0] if found else None


def _require_stuck(docker,state,intent,target):
    """The one state a migration is superseded from: the retired container captured, archived and
    then removed, its replacement never captured, and the pin still naming the retired container."""
    if not done(state,'old-captured') or not done(state,'retired-archived') \
            or any(done(state,phase) for phase in PHASES[2:]):
        raise RuntimeError('Only a migration stopped after archiving its retired container and before capturing '
                           'its replacement is superseded; reconcile this one')
    _bindings(state,intent)
    pin=pinned(state)
    if pin['generation']!=intent['generation'] or pin['target']!=intent['old'] or asdict(target)!=intent['old']:
        raise RuntimeError('The generation pin no longer names the stuck migration\'s retired container')
    if not retired_removed(docker,intent):
        raise RuntimeError('The retired container still exists; reconcile the migration instead')


def _proven_absent(docker,reference):
    """True when nothing answers to ``reference`` (a container id or name): its inspection fails and
    a listing that succeeded shows neither. A failed listing is fatal, never read as absence."""
    if docker('inspect',reference,check=False).returncode==0:return False
    listing=docker('ps','-a','--no-trunc','--format','{{.ID}} {{.Names}}',check=False)
    if listing.returncode:raise RuntimeError('Container listing unavailable; absence is not proven')
    for line in listing.stdout.splitlines():
        fields=line.split()
        if fields and (fields[0].startswith(reference) or reference in (fields[1].split(',') if len(fields)>1 else ())):
            raise RuntimeError('Container inspection unavailable; absence is not proven')
    return True


def _stuck_replacement(docker,intent,plan):
    """The id of the stuck replacement under the durable name, or None when nothing is there, proven."""
    info=replacement_identity(docker,plan['name'],intent,plan)
    if info is not None:return info['Id']
    if not _proven_absent(docker,plan['name']):
        raise RuntimeError('Replacement container inspection unavailable')
    return None


def _remove_superseded_replacement(docker,intent,plan,identifier):
    """Remove the stuck replacement named in the archive, proven again by its exact identity."""
    if identifier is None:return
    info=replacement_identity(docker,identifier,intent,plan)
    if info is None:
        if _proven_absent(docker,identifier):return
        raise RuntimeError('Replacement container inspection unavailable')
    if docker('rm','-f',identifier,check=False).returncode or not _proven_absent(docker,identifier):
        raise RuntimeError('The stuck replacement container could not be removed')


def stuck_refusal(docker,state,intent):
    """Why the recorded migration cannot be superseded, or None; changes nothing."""
    try:
        _require_stuck(docker,state,intent,hba_target.Target(**intent['old']))
    except (RuntimeError,ValueError) as error:
        return str(error)
    return None


def supersede(docker,state,*,target,stuck_replacement,inventory,image,new_volume):
    """Supersede the stuck migration with a version 4 intent onto a rebuilt volume, and return it.

    Every step can be repeated after a kill. The stuck intent and the checkpoints it reached are
    first archived whole beside its retired generation, naming the migration that supersedes it
    and what it moves onto; the replacement the archive names is then removed, after its
    identity is proven again; the record is removed in one step; and the new intent is published
    under the archived id. Nothing is deleted before it is archived."""
    state=Path(state)
    onto={'image':image,'new_volume':new_volume,'inventory':inventory}
    if present(state):
        intent=load(state)
        if 'supersedes' in intent:
            raise RuntimeError('A superseding migration is recorded; finish it with --reconcile')
        path=archive_directory(state,intent['migration'])/SUPERSEDED
        # Again on every rerun until the record is gone: the state must still be the stuck one.
        _require_stuck(docker,state,intent,target)
        if hba_startup.present(path):
            archived=_read_superseded(path)
            if authority.canonical(archived['intent'])!=authority.canonical(intent):
                raise RuntimeError('The superseded migration archive belongs to another intent; preserve both')
        else:
            plan=validate_replacement(intent,stuck_replacement(intent))
            identifier=_stuck_replacement(docker,intent,plan)
            archived=_publish_once(path,{'version':1,'kind':'superseded-migration','migration':intent['migration'],
                                         'superseded_by':str(uuid.uuid4()),'intent':intent,
                                         'checkpoints':{phase:read_checkpoint(state,phase)
                                                        for phase in ('old-captured','retired-archived')},
                                         'replacement':identifier,'onto':onto},
                                   'Superseded migration record')
        if archived['onto']!=onto:
            raise RuntimeError('The superseded migration was archived for another volume, image or inventory')
        _remove_superseded_replacement(docker,intent,validate_replacement(intent,stuck_replacement(intent)),
                                       archived['replacement'])
        _remove_record(state)
    else:
        archived=pending_superseded(state)
        if archived is None:
            raise RuntimeError('No stuck generation migration record to supersede')
        if archived['onto']!=onto:
            raise RuntimeError('The superseded migration was archived for another volume, image or inventory')
    stuck=archived['intent']
    if asdict(target)!=stuck['old']:
        raise RuntimeError('The superseded migration retired another container')
    return publish_intent(state,target=target,generation=stuck['generation'],volume=stuck['volume'],
                          inventory=inventory,retired_state=stuck['retired_state'],image=image,
                          new_volume=new_volume,migration=archived['superseded_by'],supersedes=stuck['migration'])


def finish(state):
    """The record is removed only after every other step is durable."""
    state=Path(state)
    _publish_once(record_directory(state)/COMPLETED,
                  {'version':1,'migration':load(state)['migration']},'Generation migration completion')
    _remove_record(state)
    return True


def execute(docker,state,*,replacement,desired):
    """Resume the migration record from its last durable checkpoint.

    Called for an interrupted migration. Every step is idempotent, no new
    generation is minted, and an uncertain step leaves the record in place so
    startup keeps refusing.
    """
    state=Path(state)
    completed=record_directory(state)/COMPLETED
    if hba_startup.present(completed):
        # Killed while the finished record was being removed: every step was durable before.
        record=_decode_envelope(_read_private(completed,'Generation migration completion'),'Generation migration completion')
        if not isinstance(record,dict) or record.get('version')!=1 or not hba_startup.present(archive_directory(state,record.get('migration',''))/EVIDENCE):
            raise RuntimeError('Generation migration completion is not backed by its evidence; preserve the migration record')
        _remove_record(state)
        return True
    if discard_unpublished(state):
        raise RuntimeError('The generation migration record was left before its intent was complete; nothing had '
                           'started, so it was removed. Start the migration again')
    intent=load(state)
    if hba_startup.present(Path(state)/ARCHIVE/intent['migration']/SUPERSEDED):
        raise RuntimeError('This migration is being superseded onto a rebuilt cluster; finish that with '
                           'lab/cluster_takeover.py')
    if intent['retired_state']=='absent-verified':
        raise RuntimeError('Absent retired container migration is unsupported without a verified archive; '
                           'preserve the migration record and reconcile the retired state explicitly')
    plan=validate_replacement(intent,replacement)
    if authority.digest(desired)!=intent['inventory']:
        raise RuntimeError('Generation migration inventory changed since the intent')
    _bindings(state,intent)
    with hba_startup.acquire(state,migration=True) as lease:
        if target_volume(intent)!=intent['volume'] and not done(state,'new-captured'):
            # Before the retired container is stopped or removed, and again on every rerun until
            # the replacement runs on it.
            require_cluster_volume(docker,target_volume(intent),intent['old']['owner'],target_image(intent),state)
        superseded=superseded_by(state,intent) if 'supersedes' in intent else None
        if not done(state,'old-captured'):
            retired=superseded['checkpoints']['old-captured']['retired'] if superseded else capture_retired(docker,state,intent)
            checkpoint(state,'old-captured',_bind(intent,{'retired':retired}))
        captured=read_checkpoint(state,'old-captured')['retired']
        if not done(state,'retired-archived'):
            archived=(superseded['checkpoints']['retired-archived']['retired_generation'] if superseded
                      else retired_archive(docker,state,intent))
            checkpoint(state,'retired-archived',_bind(intent,{'retired_generation':archived}))
        resumed=done(state,'new-captured')
        if not resumed:
            identifier,mounts=create_replacement(docker,state,intent,plan)
            wait_ready(docker,identifier)
            checkpoint(state,'new-captured',_bind(intent,{'container_id':identifier,'mounts':mounts}))
        replacement_captured=read_checkpoint(state,'new-captured')
        if resumed:
            # A rerun: the replacement may have stopped since (a host restart); later steps, the
            # rule comparison included, run in it.
            info=replacement_identity(docker,replacement_captured['container_id'],intent,plan)
            if info is None:raise RuntimeError('Replacement container inspection unavailable')
            if ensure_running(docker,info):wait_ready(docker,info['Id'])
        if not done(state,'generation-minted'):
            checkpoint(state,'generation-minted',_bind(intent,{'container_id':replacement_captured['container_id'],
                                                              'generation':str(uuid.uuid4())}))
        minted=read_checkpoint(state,'generation-minted')
        new_target=hba_target.Target(minted['container_id'],intent['old']['name'],intent['old']['owner'],target_image(intent))
        if not done(state,'generation-initialized'):
            initialize_generation(docker,state,intent,minted,new_target)
            checkpoint(state,'generation-initialized',_bind(intent,{'container_id':minted['container_id'],
                                                                   'generation':minted['generation']}))
        if not done(state,'rules-published'):
            outcome=publish_rules(docker,state,lease,new_target,desired)
            checkpoint(state,'rules-published',_bind(intent,{'container_id':minted['container_id'],
                                                             'generation':minted['generation'],
                                                             'token':outcome['journal']['token']}))
        comparison=compare_rules(docker,state,intent,captured,minted,desired)
        if comparison['inventory_matches_observed'] is not True:
            raise RuntimeError('Observed HBA rules differ from the migration inventory; preserve the migration record')
        if done(state,'archived'):
            archived=read_evidence(state,intent['migration'])
            if archived.get('rules',{}).get('inventory_matches_observed') is not True:
                raise RuntimeError('Archived HBA migration did not verify the inventory; preserve the migration record')
        else:
            if target_volume(intent)!=intent['volume']:
                # The runtime mounts the new volume from now on; written before the record goes.
                database_volume.publish(state,target_volume(intent))
            outcome=outcome_for(state,read_checkpoint(state,'rules-published')['token'])
            archive_evidence(state,intent,captured,minted,new_target,outcome,comparison)
            checkpoint(state,'archived',_bind(intent,{'container_id':minted['container_id'],
                                                      'generation':minted['generation'],
                                                      'difference':comparison['difference']}))
        return finish(state)


def migrate(docker,state,*,target,replacement,desired,volume,retired_state='stopped-will-not-return',image=None):
    """Start one migration. A record, torn or whole, refuses before any effect."""
    state=Path(state)
    require_absent(state)
    pinned=require_pin(state,target)
    intent=publish_intent(state,target=target,generation=pinned['generation'],volume=volume,
                          inventory=authority.digest(desired),retired_state=retired_state,image=image)
    return execute(docker,state,replacement=replacement,desired=desired),intent


def pinned(state):
    """The established pin: the retired generation and its last observed container."""
    path=Path(state)/hba_generation.NAME
    if not hba_startup.present(path):
        raise RuntimeError('Generation migration requires an established generation pin')
    return hba_generation.load(state)


def require_pin(state,target):
    record=pinned(state)
    if record['target']!=asdict(target):
        raise RuntimeError('Generation migration requires the pin of this exact retired container')
    return record
