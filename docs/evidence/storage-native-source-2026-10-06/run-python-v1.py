import os,resource,subprocess,sys,unittest
from pathlib import Path
root=Path('/home/sbarah/.codex/worktrees/09c0/sbarbase')
sys.path.insert(0,str(root/'lab'))
hybrid=os.environ.get('SETTLEMENT_CHECK')=='hybrid'
selected={'test_sdk_fixture_syntax_is_valid_without_importing_or_running_vendor_services','test_python_fixture_receipt_roundtrips_strict_control_codec_without_native_admission','test_protocol_helper_negative_units_under_declared_bun_phase'}
if hybrid:
 original_run=subprocess.run
 def bounded_bun(argv,*args,**kwargs):
  if type(argv) not in (list,tuple) or Path(str(argv[0])).name!='bun':raise RuntimeError('Hybrid phase permits Bun source commands only')
  if kwargs.get('preexec_fn') is not None:raise RuntimeError('Unexpected source child setup')
  def limits():resource.setrlimit(resource.RLIMIT_AS,(8796093022208,8796093022208))
  kwargs['preexec_fn']=limits
  return original_run(argv,*args,**kwargs)
 subprocess.run=bounded_bun
suite=unittest.TestSuite()
def collect(item):
 if isinstance(item,unittest.TestSuite):
  for test in item:collect(test)
 elif (item.id().split('.')[-1] in selected)==hybrid:suite.addTest(item)
for module in ['test_storage_write_settlement','test_disposable_storage_settlement_drill','test_storage_native_authority']:
 collect(unittest.defaultTestLoader.loadTestsFromName(module))
if hybrid and suite.countTestCases()!=3:raise RuntimeError('Frozen hybrid test inventory differs')
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
