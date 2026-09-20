"""The supervisor unit must be rendered, verified and installed, never hand-edited."""
from pathlib import Path
import unittest
from unittest.mock import patch
import install_server

ROOT=Path(install_server.__file__).resolve().parent.parent


class RenderingTests(unittest.TestCase):
    def render(self,**overrides):
        values={'root':ROOT,'home':Path('/srv/sbarbase'),'user':'sbarbase','bun_dir':'/srv/sbarbase/.bun/bin'}
        values.update(overrides)
        return install_server.rendered_unit(values['root'],values['home'],values['user'],values['bun_dir'])

    def test_every_shipped_default_path_is_replaced(self):
        rendered=self.render()
        self.assertNotIn('/opt/sbarbase',rendered)
        self.assertIn('WorkingDirectory='+str(ROOT),rendered)
        self.assertIn('ExecStart=/usr/bin/python3 '+str(ROOT)+'/lab/dev.py',rendered)
        self.assertIn('ExecStartPre=/usr/bin/python3 '+str(ROOT)+'/lab/install_server.py check',rendered)
        self.assertIn('ReadWritePaths='+str(ROOT)+' /srv/sbarbase/.secrets',rendered)
        self.assertIn('Environment=HOME=/srv/sbarbase',rendered)
        self.assertIn(':/srv/sbarbase/.bun/bin',rendered)

    def test_a_unit_whose_shape_changed_is_not_rewritten_blindly(self):
        broken=install_server.SERVICE_UNIT.read_text().replace('ExecStart=/usr/bin/python3 /opt/sbarbase/lab/dev.py','ExecStart=/bin/true')
        with self.assertRaises(SystemExit):
            install_server.rendered_unit(ROOT,Path('/srv/x'),'sbarbase','/srv/x/.bun/bin',text=broken)

    def test_a_missing_bun_directory_is_refused(self):
        with self.assertRaises(SystemExit):
            install_server.rendered_unit(ROOT,Path('/srv/x'),'sbarbase','')

    def test_the_service_user_is_applied(self):
        rendered=self.render(user='supabase-ops')
        self.assertIn('User=supabase-ops',rendered)
        self.assertIn('Group=supabase-ops',rendered)
        self.assertNotIn('User=sbarbase',rendered)

    def test_the_install_commands_are_exact_and_use_a_private_default_path(self):
        commands=install_server.unit_commands(self.render())
        self.assertEqual(len(commands),4)
        self.assertIn('install -m 0644',commands[0])
        self.assertIn('/etc/systemd/system/sbarbase.service',commands[0])
        self.assertIn('daemon-reload',commands[1])
        self.assertIn('enable --now sbarbase.service',commands[2])
        self.assertIn('is-active',commands[3])


class InstallGuardTests(unittest.TestCase):
    def test_installing_requires_root(self):
        with patch.object(install_server.os,'geteuid',return_value=1000), \
             patch.object(install_server,'run') as run:
            run.return_value=type('R',(),{'returncode':0,'stdout':'','stderr':''})()
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True)
        self.assertIn('requires root',str(raised.exception))

    def test_a_unit_that_fails_verification_is_never_installed(self):
        with patch.object(install_server.os,'geteuid',return_value=0), \
             patch.object(install_server,'run') as run, \
             patch.object(install_server.subprocess,'run') as install:
            run.return_value=type('R',(),{'returncode':1,'stdout':'','stderr':'bad unit'})()
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True)
            install.assert_not_called()
        self.assertIn('did not verify',str(raised.exception))

    def test_a_dry_run_writes_evidence_and_installs_nothing(self):
        with patch.object(install_server,'run') as verify, \
             patch.object(install_server.subprocess,'run') as install:
            verify.return_value=type('R',(),{'returncode':0,'stdout':'','stderr':''})()
            passed=install_server.supervise(apply=False)
            install.assert_not_called()
        self.assertTrue(passed)
        import json
        evidence=json.loads((ROOT/'docs'/'evidence'/'supervisor-unit.json').read_text())
        self.assertEqual(evidence['verify'],'passed')
        self.assertFalse(evidence['applied'])
        self.assertFalse(evidence['running_as_root'])
        self.assertNotIn('/opt/sbarbase',evidence['rendered'])
        self.assertIn('Not a substitute for the server acceptance run',evidence['scope'])


if __name__=='__main__':unittest.main()