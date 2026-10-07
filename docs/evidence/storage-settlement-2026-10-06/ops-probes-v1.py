import copy, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[0]))
sys.path.insert(0, "/home/sbarah/.codex/worktrees/09c0/sbarbase/lab")
from test_storage_write_settlement import manifest, DiskFixtureAuthority
from storage_write_settlement import SourceSettlement, Journal, validate_manifest
for field, altered in [("version", True), ("management_epoch", 8.0), ("sequence", 7.0)]:
    with tempfile.TemporaryDirectory() as root:
        declared=manifest()
        authority=DiskFixtureAuthority(declared)
        protocol=SourceSettlement(declared, Journal(Path(root)), authority)
        receipt=protocol.settle()
        changed=copy.deepcopy(receipt)
        changed[field]=altered
        protocol.revalidate(changed)
        called=[]
        protocol.record_fixture_effect(changed, "fixture-proof", lambda: called.append("executed") or {"outcome":"recorded"})
        print(json.dumps({"probe":"receipt-substitution", "field":field,"type":type(altered).__name__,"executed":called}))
class CachedAuthority(DiskFixtureAuthority):
    def __init__(self, declared):
        super().__init__(declared)
        self.current=None
    def observe(self, declared):
        if self.current is None: self.current=super().observe(declared)
        return self.current
    def stop(self, declared, token):
        super().stop(declared, token)
        for writer in self.current["writers"]: writer.update(state="stopped",pids=[])
    def reconcile(self, declared, token):
        material=super().reconcile(declared, token)
        self.current["effects"]["queue"]["inventory_sha256"]="f"*64
        return material
with tempfile.TemporaryDirectory() as root:
    declared=manifest()
    authority=CachedAuthority(declared)
    protocol=SourceSettlement(declared, Journal(Path(root)), authority)
    receipt=protocol.settle()
    with Journal(Path(root)).locked() as journal:
        record=journal.records[-1]
        print(json.dumps({"probe":"cached-observation", "before":"9"*64, "after":record["details"]["observation"]["effects"]["queue"]["inventory_sha256"],"phase":record["phase"],"receipt":receipt["evidence"]}))
for field, value in [("launch_paths", [{}]), ("neighbors", [[]])]:
    declared=manifest()
    declared[field]=value
    try: validate_manifest(declared)
    except Exception as error: print(json.dumps({"probe":"malformed-manifest","field":field,"type":type(error).__name__,"message":str(error)}))
