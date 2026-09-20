"""Unresolved effects must block startup before any runtime mutations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import durable_runtime
import installation_runtime
import effect_receipt


class EffectReceiptTests(unittest.TestCase):
    def test_startup_and_direct_provision_cannot_bypass_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            for text in ('{broken',json.dumps({'phase':'pending'}),json.dumps({'phase':'completed'})):
                (state/'worker-effect.json').write_text(text)
                with patch.object(durable_runtime,'STATE',state),patch.object(durable_runtime,'inspect') as inspect:
                    instance=object.__new__(durable_runtime.Runtime)
                    with self.assertRaises(Exception):instance.start()
                    with self.assertRaises(Exception):instance.provision('e_fixture')
                    inspect.assert_not_called()
                    with patch.object(installation_runtime,'TargetRuntime') as target:
                        with self.assertRaisesRegex(RuntimeError,'reconcile'):
                            installation_runtime.main('up')
                        target.assert_not_called()

    def test_pending_publication_is_exclusive_and_durable_before_return(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker-effect.json'
            record={'version':1,'phase':'pending'}
            with patch.object(effect_receipt.os,'fsync',wraps=effect_receipt.os.fsync) as sync:
                effect_receipt.publish(path,record)
                self.assertEqual(sync.call_count,2)
            before=path.read_bytes()
            with self.assertRaises(FileExistsError):effect_receipt.publish(path,{'phase':'replaced'})
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_publication_sync_failure_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            with patch.object(effect_receipt.os,'fsync',side_effect=OSError('injected')):
                with self.assertRaises(OSError):effect_receipt.publish(state/'worker-effect.json',{'phase':'pending'})
            with self.assertRaises(RuntimeError):effect_receipt.require_settled(state)
