"""Real pinned PostgreSQL publication evidence, using disposable host state."""
import json
from pathlib import Path
import tempfile
import uuid
import atomic_hba
import hba_apply
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_reconcile
import hba_startup
import hba_target


def run(container,check,docker):
    observed=json.loads(docker('inspect',container).stdout)[0]
    target=hba_target.capture(docker,observed['Name'][1:],observed['Config']['Labels']['io.sbarbase.owner'],observed['Image'])
    original=docker('exec',container,'cat','/etc/postgresql/pg_hba.conf').stdout
    shared=None
    for valid in (True,False):
        with tempfile.TemporaryDirectory(prefix='sbar-hba-apply-') as directory:
            state=Path(directory)
            with hba_startup.acquire(state) as lease:
                if shared is None:shared=lease.initialize(docker,target=target)
                else:hba_generation.publish(state,target,shared.generation)
                snapshot=authority.read(docker,container,shared.generation)
                text=original if valid else original+'not-a-valid-hba-record\n'
                prepared=atomic_hba.prepare(docker,container,text)
                token=str(uuid.uuid4())
                lease.begin(docker,snapshot,prepared,token,target=target)
                before=journal.read_text(state/journal.NAME)
                if valid:
                    witness=hba_apply.execute(docker,state,lease.descriptors,target=target,startup=lease)
                    check('actual HBA apply has zero parser errors and reload acknowledgment',witness['parser_errors']==0 and witness['reload_acknowledged'] is True and witness['activation']=='unknown')
                    stored=json.loads((state/hba_apply.COMPLETIONS/(token+'.json')).read_text())
                    check('completion witness durably binds exact journal and desired bytes',stored=={'record':witness,'checksum':authority.digest(authority.canonical(witness))} and witness['journal_digest']==authority.digest(before) and witness['content_digest']==authority.digest(prepared.content))
                else:
                    try:hba_apply.execute(docker,state,lease.descriptors,target=target,startup=lease)
                    except RuntimeError as error:
                        if str(error)!='Applied HBA contains parser errors':raise
                    else:raise AssertionError('Invalid HBA reported success')
                    check('invalid applied HBA cannot produce completion witness',not (state/hba_apply.COMPLETIONS/(token+'.json')).exists())
                check(('valid' if valid else 'invalid')+' apply retains pending journal',journal.read_text(state/journal.NAME)==before)
                calls=[]
                def traced(*args,**kwargs):calls.append(args);return docker(*args,**kwargs)
                try:hba_apply.execute(traced,state,lease.descriptors,target=target,startup=lease)
                except FileExistsError:pass
                else:raise AssertionError('Execution attempt was replayed')
                check(('valid' if valid else 'invalid')+' execution cannot dispatch apply twice',not any(authority.APPLY in args for args in calls))
            retired=hba_reconcile.retire(docker,state,target=target)
            check(('valid' if valid else 'invalid')+' publication retirement preserves unknown activation',retired['observed_content']=='matches-desired' and retired['activation']=='unknown' and (state/journal.NAME).exists())
    # Fixture-only restoration, not an operation recovery path.
    atomic_hba.replace(docker,container,original)
    hba_apply.sql(docker,container,'SELECT pg_reload_conf();')
    check('fixture baseline restored after applied-operation probes',docker('exec',container,'cat','/etc/postgresql/pg_hba.conf').stdout.endswith(original))
