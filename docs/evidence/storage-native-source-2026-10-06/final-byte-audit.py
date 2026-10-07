import ast,fcntl,hashlib,json,os,resource,stat,sys,time
from pathlib import Path
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))
resource.setrlimit(resource.RLIMIT_CPU,(120,120))
resource.setrlimit(resource.RLIMIT_FSIZE,(16777216,16777216))
root=Path('/home/sbarah/.codex/worktrees/09c0/sbarbase')
owned=Path(__file__).resolve().parent
start=time.monotonic()
fd=os.open(owned/'budget.json',os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX)
with os.fdopen(os.dup(fd)) as stream:budget=json.load(stream)
if budget['active_seconds']>=180 or budget['output_bytes']>=16777216:raise SystemExit('Role budget exhausted')
module=ast.parse((root/'deploy/verify/capability_registry.py').read_text())
values={}
for node in module.body:
 if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
  name=node.targets[0].id
  if name in ('REFERENCE','REFERENCE_BUNDLE','LEDGER','PROOF_DIRECTORY','SOURCE_DIRECTORIES'):
   values[name]=ast.literal_eval(node.value)
  if name=='SOURCE_FILES':
   values[name]=tuple(values[x.id] if isinstance(x,ast.Name) else ast.literal_eval(x) for x in node.value.elts)
names=set(values['SOURCE_FILES'])
for directory in values['SOURCE_DIRECTORIES']:
 for current,directories,files in os.walk(root/directory,followlinks=False):
  for entry in directories+files:
   if (Path(current)/entry).is_symlink():raise SystemExit('Source symlink')
  directories[:]=[x for x in directories if x!='__pycache__' and (Path(current)/x).relative_to(root).as_posix()!=values['PROOF_DIRECTORY']]
  names.update((Path(current)/x).relative_to(root).as_posix() for x in files if not x.endswith(('.pyc','.pyo')))
def row(path):
 p=root/path
 for parent in [p,*p.parents]:
  if parent==root:break
  if parent.is_symlink():raise SystemExit('File symlink')
 if not p.is_file():raise SystemExit('Missing file')
 data=p.read_bytes()
 return {'path':path,'bytes':len(data),'mode':stat.S_IMODE(p.stat().st_mode),'sha256':hashlib.sha256(data).hexdigest()}
inventory=[row(name) for name in sorted(names)]
canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
public={'algorithm':'sbarbase-public-source-v1','files':len(inventory),'sha256':hashlib.sha256(canonical(inventory)).hexdigest()}
changed=json.loads((owned/'delivery-paths.json').read_text())
delta=[row(name) for name in changed]
for item in delta:
 data=(root/item['path']).read_bytes()
 if any(chr(code).encode('utf-8') in data for code in (0x2013,0x2014)):
  raise SystemExit('Long dash in delivery file: '+item['path'])
result={'native_accepted':False,'public_source':public,'source_inventory':inventory,'delivery_files':delta,'audit_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
out=owned/'final-byte-audit.json';payload=json.dumps(result,indent=2)+'\n'
if budget['output_bytes']+len(payload.encode())+1024>16777216:raise SystemExit('Static audit output allowance exceeded')
out.write_text(payload)
elapsed=time.monotonic()-start
budget['active_seconds']+=elapsed;budget['output_bytes']+=len(payload.encode())+1024;budget['checks'].append({'phase':'final-static-byte-audit','seconds':elapsed,'output_bytes':len(payload.encode())+1024,'returncode':0,'log':out.name,'reason':None})
os.lseek(fd,0,os.SEEK_SET);os.ftruncate(fd,0);os.write(fd,json.dumps(budget,sort_keys=True).encode());os.fsync(fd);os.close(fd)
print(json.dumps({'native_accepted':False,'public_source':public,'delivery_count':len(delta),'seconds':elapsed}))
