"""Regression guard: the restore path must publish HBA through owned authority."""
import re
from pathlib import Path
import unittest

LAB=Path(__file__).resolve().parent


class OwnedHbaWriterTests(unittest.TestCase):
    def test_restore_path_has_no_raw_hba_write(self):
        source=(LAB/'recovery-restore-db.py').read_text()
        self.assertNotIn('cat > /etc/postgresql/pg_hba.conf',source)
        self.assertNotIn('pg_reload_conf',source)

    def test_restore_path_uses_the_target_writer(self):
        source=(LAB/'recovery-restore-db.py').read_text()
        self.assertRegex(source,r'hba_runtime\.TargetHBA\(')
        self.assertRegex(source,r'\.before_create\(preexisting_volume=')
        self.assertRegex(source,r'\.ready\(created,created=True\)')
        self.assertIn("created=json.loads(lab.docker('inspect',db).stdout)[0]['Id']",source)

    def test_restore_path_requires_verified_absence_before_creation_evidence(self):
        source=(LAB/'recovery-restore-db.py').read_text()
        absence=source.index("lab.docker(kind,'inspect',name,check=False).returncode==0")
        evidence=source.index('hba_writer.before_create(preexisting_volume=False)')
        self.assertLess(absence,evidence)

    def test_other_raw_writers_are_inventoried(self):
        inventory=(LAB.parent/'docs'/'TARGET-HBA-WRITERS.md').read_text()
        for path in ('lab/recovery-check-storage.py','lab/run.py','lab/provision.py','lab/storage_probe.py'):
            with self.subTest(path=path):
                self.assertIn(path,inventory)


if __name__=='__main__':unittest.main()