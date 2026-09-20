"""The mirrored user unit must stay faithful to the shipped system unit."""
from pathlib import Path
import re
import unittest
import supervised_run_check as supervised

ROOT=Path(__file__).resolve().parent.parent


def directives(text):
    found={}
    for line in text.splitlines():
        match=re.match(r'(ExecStartPre|ExecStart|WorkingDirectory|Environment|Restart|TimeoutStopSec)=(.+)',line.strip())
        if match:found.setdefault(match.group(1),[]).append(match.group(2))
    return found


class MirrorFidelityTests(unittest.TestCase):
    def test_the_mirror_uses_the_same_python_and_entry_points(self):
        shipped=directives((ROOT/'deploy'/'sbarbase.service').read_text())
        mirror=directives(supervised.unit_text())
        # The shipped unit runs from /opt/sbarbase on a server; the mirror runs from
        # this checkout, so compare the interpreter and the script path inside it.
        def entry(value):
            parts=value.split()
            script='/'.join(parts[1].split('/')[-2:]) if len(parts)>1 else ''
            return parts[0],script,' '.join(parts[2:])
        for key in ('ExecStartPre','ExecStart'):
            self.assertIn(key,shipped,key)
            self.assertIn(key,mirror,key)
            self.assertEqual([entry(item) for item in shipped[key]],
                             [entry(item) for item in mirror[key]],
                             key+' differs from the shipped unit')

    def test_the_mirror_keeps_the_preflight_gate_and_the_working_directory(self):
        mirror=directives(supervised.unit_text())
        self.assertTrue(mirror['ExecStartPre'][0].endswith('lab/install_server.py check'))
        self.assertEqual(mirror['WorkingDirectory'][0],str(ROOT))
        self.assertIn('PATH=',mirror['Environment'][1])
        self.assertEqual(mirror['Restart'][0],'no')

    def test_the_shipped_unit_still_gates_on_the_preflight(self):
        shipped=directives((ROOT/'deploy'/'sbarbase.service').read_text())
        self.assertTrue(shipped['ExecStartPre'][0].endswith('lab/install_server.py check'))
        self.assertTrue(shipped['ExecStart'][0].endswith('lab/dev.py'))

    def test_the_scope_states_what_this_does_not_prove(self):
        source=(ROOT/'lab'/'supervised_run_check.py').read_text()
        self.assertIn('Not the shipped system unit',source)
        self.assertIn('not an empty-host install',source)

    def test_the_temporary_unit_is_always_removed(self):
        source=(ROOT/'lab'/'supervised_run_check.py').read_text()
        finally_block=source.split('finally:')[1]
        self.assertIn('UNIT_PATH.unlink(missing_ok=True)',finally_block)
        self.assertIn("record('temporary unit file removed'",finally_block)


if __name__=='__main__':unittest.main()