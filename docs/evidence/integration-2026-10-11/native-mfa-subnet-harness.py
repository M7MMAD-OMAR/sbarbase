"""Integration-only harness: original MFA fixture with bounded subnet admission."""
import importlib.util
import json
from pathlib import Path
import sys
root=Path('/home/sbarah/R/Projects/P/sbarbase')
sys.path.insert(0,str(root/'lab'))
sys.path.insert(0,str(root/'deploy/verify'))
import fixture_subnet
spec=importlib.util.spec_from_file_location('native_mfa',root/'lab/management-mfa-fixture.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original=module.docker
output=Path('/tmp/sbarbase-integration-20261011/native-mfa-admitted')
output.mkdir(exist_ok=True)
def docker(*args,data=None):
    if args[:2]==('network','create'):
        name=args[-1]
        receipt=fixture_subnet.observe("identity-"+name,output)
        fixture_subnet.admit_receipt(json.dumps(receipt),"identity-"+name)
        result=original(*args[:-1],'--subnet',receipt['subnet'],name,data=data)
        record=json.loads(original('network','inspect',name))[0]
        (output/'network-inspect.json').write_text(json.dumps(record,indent=2)+'\n')
        if record['Driver']!='bridge' or not record['Internal'] or record['IPAM']['Config'][0]['Subnet']!=receipt['subnet']:
            raise RuntimeError('Owned fixture network identity differs')
        return result
    return original(*args,data=data)
module.docker=docker
sys.argv=[str(root/'lab/management-mfa-fixture.py'),'--output',str(output/'report.json')]
raise SystemExit(module.main())
