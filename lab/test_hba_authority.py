"""Real shell/CAS tests in a private filesystem, not container or crash proof."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
import atomic_hba
import hba_authority as authority


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sbar-hba-authority-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.hba=self.root/'pg_hba.conf'
        self.hba.write_text('local all all trust\n')
        self.cid='a'*64
        self.generation=str(uuid.uuid4())
        self.token=str(uuid.uuid4())
        self.identity={'kind':'startup','id':str(uuid.uuid4())}
        self.initial=authority.initialize(self.docker,self.cid,self.generation)
        self.prepared=atomic_hba.Prepared(self.cid,authority.digest(self.hba.read_text()),'local all all reject\n')
        self.binding=authority.operation_binding(self.prepared,self.identity)

    def docker(self,*args,data=None):
        self.assertEqual(args[0],'exec')
        args=list(args[1:])
        if args[0]=='-i':args.pop(0)
        self.assertEqual(args.pop(0),self.cid)
        self.assertEqual(args[:2],['sh','-c'])
        args[2]=args[2].replace('/etc/postgresql',str(self.root))
        return subprocess.run(args,input=data,text=True,capture_output=True,check=True,timeout=10)

    def register(self):
        return authority.update(self.docker,self.initial,self.token,self.binding)

    def test_active_apply_and_revoke_blocks_old_permit_without_hba_change(self):
        active=self.register()
        permit=authority.authorize(active,self.prepared,self.token,self.identity)
        revoked=authority.update(self.docker,active,self.token,self.binding,revoke=True)
        with self.assertRaises(subprocess.CalledProcessError) as error:authority.apply(self.docker,permit)
        self.assertEqual(error.exception.returncode,75)
        self.assertEqual(self.hba.read_text(),'local all all trust\n')
        with self.assertRaisesRegex(RuntimeError,'revoked'):authority.update(self.docker,revoked,self.token,self.binding)
        with self.assertRaisesRegex(RuntimeError,'not authorized'):authority.authorize(revoked,self.prepared,self.token,self.identity)

    def test_active_permit_applies_once(self):
        active=self.register()
        permit=authority.authorize(active,self.prepared,self.token,self.identity)
        authority.apply(self.docker,permit)
        self.assertEqual(self.hba.read_text(),self.prepared.content)
        with self.assertRaises(subprocess.CalledProcessError) as error:authority.apply(self.docker,permit)
        self.assertEqual(error.exception.returncode,74)

    def test_revocation_before_registration_cannot_be_erased_by_stale_snapshot(self):
        revoked=authority.update(self.docker,self.initial,self.token,self.binding,revoke=True)
        with self.assertRaises(subprocess.CalledProcessError) as error:self.register()
        self.assertEqual(error.exception.returncode,74)
        self.assertEqual(authority.read(self.docker,self.cid,self.generation),revoked)
        with self.assertRaisesRegex(RuntimeError,'revoked'):authority.update(self.docker,revoked,self.token,self.binding)

    def test_other_active_operation_and_binding_changes_refused(self):
        active=self.register()
        with self.assertRaisesRegex(RuntimeError,'Another'):authority.update(self.docker,active,str(uuid.uuid4()),self.binding)
        with self.assertRaisesRegex(RuntimeError,'binding mismatch'):authority.update(self.docker,active,self.token,'b'*64,revoke=True)

    def test_missing_registry_cannot_be_reinitialized(self):
        (self.root/Path(authority.PATH).name).unlink()
        with self.assertRaises(subprocess.CalledProcessError):authority.read(self.docker,self.cid,self.generation)
        with self.assertRaises(subprocess.CalledProcessError):authority.initialize(self.docker,self.cid,self.generation)

    def test_missing_initialization_marker_blocks_update(self):
        (self.root/Path(authority.MARKER).name).rmdir()
        with self.assertRaises(subprocess.CalledProcessError):self.register()

    def test_corruption_duplicate_keys_and_generation_fail_closed(self):
        text=self.initial.text
        for bad in [text.replace('"version":1','"version":2'),text.replace('"version":1','"version":1,"version":1')]:
            with self.assertRaises(RuntimeError):authority.decode(bad,self.generation)
        with self.assertRaisesRegex(RuntimeError,'generation'):authority.decode(text,str(uuid.uuid4()))

    def test_uncertain_acknowledgment_is_not_retried(self):
        calls=[]
        def lost(*args,**kwargs):
            result=self.docker(*args,**kwargs)
            calls.append(result)
            raise RuntimeError('acknowledgment lost')
        with self.assertRaisesRegex(RuntimeError,'lost'):authority.update(lost,self.initial,self.token,self.binding)
        self.assertEqual(len(calls),1)
        observed=authority.read(self.docker,self.cid,self.generation)
        self.assertEqual(authority.decode(observed.text,self.generation)['operations'][self.token]['state'],'active')
        with self.assertRaises(subprocess.CalledProcessError):self.register()


if __name__=='__main__':unittest.main()
