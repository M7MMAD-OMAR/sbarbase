import copy, json, sys, tempfile, subprocess
from pathlib import Path
sys.path.insert(0, 'lab')
from test_storage_write_settlement import manifest, DiskFixtureAuthority
from storage_write_settlement import Journal, SourceSettlement
results = []
for replacement in (True, 1.0):
    with tempfile.TemporaryDirectory() as root:
        declared = manifest()
        protocol = SourceSettlement(declared, Journal(Path(root)), DiskFixtureAuthority(declared))
        receipt = protocol.settle()
        wrong = copy.deepcopy(receipt)
        wrong['version'] = replacement
        original_digest = protocol.revalidate(receipt)
        changed_digest = protocol.revalidate(wrong)
        calls = []
        outcome = protocol.record_fixture_effect(wrong, 'fixture-critic-effect', lambda: calls.append('executed') or {'outcome': 'recorded'})
        source = "import {validateSourceSettlementReceipt} from './src/control/storage-settlement-contract.ts';const p=JSON.parse(await Bun.stdin.text());try {validateSourceSettlementReceipt(p);console.log('accepted')}catch(e){console.log('refused: '+e.message)}"
        control = subprocess.run(['bun', '-e', source], input=json.dumps(wrong), text=True, capture_output=True, check=True)
        results.append({'version': replacement, 'python_revalidation_accepted': True, 'receipt_digest_changed': original_digest != changed_digest, 'fixture_effect': outcome, 'calls': calls, 'typescript': control.stdout.strip()})
with tempfile.TemporaryDirectory() as root:
    declared = manifest()
    declared['generation'] = 1
    authority = DiskFixtureAuthority(declared)
    authority.observe_change = lambda value, count: value.update(generation=True)
    receipt = SourceSettlement(declared, Journal(Path(root)), authority).settle()
    results.append({'observed_generation': True, 'declared_generation': 1, 'settled_sequence': receipt['sequence']})
print(json.dumps(results, indent=2))
