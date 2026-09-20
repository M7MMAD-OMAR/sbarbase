"""Actual inherited flocks and SQLite claims gate worker HBA intent."""
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
import hba_target
import hba_generation
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_ownership as ownership


def child(state,worker,effect,operation):
    os.dup2(worker,3);os.dup2(effect,4)
    config=json.loads((state/'input.json').read_text())
    os.environ['SBARBASE_EFFECT_TOKEN']=config['receipt']
    snapshot=authority.Snapshot(**config['snapshot'])
    prepared=atomic_hba.Prepared(**config['prepared'])
    target=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'c'*64)
    def dispatch(*args,**kwargs):
        if args==('inspect','a'*64):
            return SimpleNamespace(stdout=json.dumps([{'Id':'a'*64,'Name':'/fixture-db','Image':target.image,'Config':{'Labels':{'io.sbarbase.owner':config.get('owner','fixture')}},'State':{'Running':True}}]))
        saved=journal.load(state/journal.NAME)
        (state/'dispatched.json').write_text(json.dumps(saved['identity']))
    try:ownership.begin_worker(dispatch,state,operation,config['runtime'],snapshot,prepared,config['token'],target=target)
    except Exception:return 2
    return 0


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=Path(self.temp.name)
        self.runtime='e_'+'a'*24;self.receipt=str(uuid.uuid4());self.claim=str(uuid.uuid4())
        generation=str(uuid.uuid4())
        config={'receipt':self.receipt,'runtime':self.runtime,'token':str(uuid.uuid4()),
                'snapshot':{'container_id':'a'*64,'generation':generation,'text':authority.encode({'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}})},
                'prepared':{'container_id':'a'*64,'expected_digest':'b'*64,'content':'local all all trust\n'}}
        (self.state/'input.json').write_text(json.dumps(config))
        hba_generation.publish(self.state,hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'c'*64),generation)
        self.record={'version':1,'phase':'pending','token':self.receipt,'native':'durable-provision-v1','stageProtocol':1,'hbaProtocol':1,
                     'job':{'environment':'fixture','runtime':self.runtime,'claim':self.claim,'attempt':1}}
        (self.state/'worker-effect.json').write_text(json.dumps(self.record))
        (self.state/'effect-stages').mkdir();self.stage('services',2)
        with closing(sqlite3.connect(self.state/'control.sqlite')) as db,db:
            db.execute('CREATE TABLE provision_jobs(environment TEXT,runtime TEXT,claim TEXT,attempt INTEGER,state TEXT)')
            db.execute('INSERT INTO provision_jobs VALUES (?,?,?,?,?)',('fixture',self.runtime,self.claim,1,'running'))
        self.fds={}
        for name in ('worker','effect','operation'):
            fd=os.open(self.state/(name+'.lock'),os.O_CREAT|os.O_RDWR,0o600)
            high=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,10);os.close(fd)
            self.fds[name]=high;self.addCleanup(os.close,high)
            fcntl.flock(high,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def stage(self,name,index):
        (self.state/'effect-stages'/(self.receipt+'.json')).write_text(json.dumps({**self.record,'stage':name,'stageIndex':index}))

    def invoke(self,overrides=None):
        fds={**self.fds,**(overrides or {})}
        return subprocess.run(['/usr/bin/python3',str(Path(__file__).resolve()),'--child',str(self.state),*[str(fds[k]) for k in ('worker','effect','operation')]],pass_fds=tuple(fds.values()),capture_output=True,timeout=10).returncode

    def assert_refused(self,overrides=None):
        self.assertEqual(self.invoke(overrides),2)
        self.assertFalse((self.state/journal.NAME).exists());self.assertFalse((self.state/'dispatched.json').exists())

    def test_real_inherited_leases_and_services_claim_authorize_exact_identity(self):
        self.assertEqual(self.invoke(),0)
        identity=json.loads((self.state/'dispatched.json').read_text())
        self.assertEqual(identity,{'kind':'worker','runtime':self.runtime,'receipt':self.receipt,'claim':self.claim,'attempt':1})

    def test_same_inode_competing_open_description_cannot_claim_ownership(self):
        for name in self.fds:
            with self.subTest(name=name):
                fd=os.open(self.state/(name+'.lock'),os.O_RDWR)
                high=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,10);os.close(fd)
                try:self.assert_refused({name:high})
                finally:os.close(high)

    def test_wrong_container_owner_cannot_publish_intent(self):
        path=self.state/'input.json';config=json.loads(path.read_text());config['owner']='foreign'
        path.write_text(json.dumps(config));self.assert_refused()

    def test_wrong_inode_rejected(self):
        fd=os.open(self.state/'wrong.lock',os.O_CREAT|os.O_RDWR,0o600)
        high=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,10);os.close(fd)
        try:self.assert_refused({'operation':high})
        finally:os.close(high)

    def test_aliased_lock_inodes_do_not_count_as_distinct_ownership(self):
        (self.state/'effect.lock').unlink()
        os.link(self.state/'worker.lock',self.state/'effect.lock')
        self.assert_refused({'effect':self.fds['worker']})

    def test_legacy_or_invalid_protocol_receipt_cannot_begin(self):
        for version in (None,True,2):
            with self.subTest(version=version):
                self.record['hbaProtocol']=version
                (self.state/'worker-effect.json').write_text(json.dumps(self.record))
                self.stage('services',2)
                self.assert_refused()

    def test_non_services_stage_cannot_begin_hba_intent(self):
        self.stage('database',1);self.assert_refused()

    def test_stale_catalog_claim_cannot_begin_hba_intent(self):
        with closing(sqlite3.connect(self.state/'control.sqlite')) as db,db:db.execute('UPDATE provision_jobs SET claim=?',(str(uuid.uuid4()),))
        self.assert_refused()

    def test_missing_receipt_cannot_begin_hba_intent(self):
        (self.state/'worker-effect.json').unlink();self.assert_refused()

    def test_symlink_lock_path_cannot_authorize_even_same_target_inode(self):
        path=self.state/'operation.lock';moved=self.state/'original.lock';path.rename(moved);path.symlink_to(moved)
        self.assert_refused()


if __name__=='__main__':
    if len(sys.argv)==6 and sys.argv[1]=='--child':raise SystemExit(child(Path(sys.argv[2]),*map(int,sys.argv[3:])))
    unittest.main()
