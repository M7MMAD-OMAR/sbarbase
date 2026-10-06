import argparse,fcntl,json,os,re,resource,selectors,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path('/home/sbarah/.codex/worktrees/09c0/sbarbase')
OUT=Path(__file__).resolve().parent
PYTHON_AS=268435456
BUN_AS=8796093022208
MAX_RSS=805306368
MAX_OUTPUT=16777216
ROLE_SECONDS=180
CHECK_SECONDS=120
resource.setrlimit(resource.RLIMIT_AS,(PYTHON_AS,BUN_AS))
parser=argparse.ArgumentParser();parser.add_argument('--check',choices=['python','hybrid','bun'],required=True);args=parser.parse_args()
import hashlib
frozen=json.loads((OUT/'profile.json').read_text())
for entry in frozen['bindings']:
 path=Path(entry['path'])
 if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise SystemExit('Frozen source/profile identity changed: '+str(path))
ledger=os.open(OUT/'budget.json',os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(ledger,fcntl.LOCK_EX)
with os.fdopen(os.dup(ledger),'r') as stream:previous=stream.read()
budget=json.loads(previous) if previous else {'active_seconds':0,'output_bytes':0,'checks':[]}
remaining=ROLE_SECONDS-budget['active_seconds']
if remaining<=0 or budget['output_bytes']>=MAX_OUTPUT:raise SystemExit('Frozen source role budget exhausted')
argv=[sys.executable,str(OUT/'run-python.py')] if args.check!='bun' else ['/home/sbarah/.bun/bin/bun','test','tests/storage-settlement-contract.test.ts']
env=dict(os.environ);env['SETTLEMENT_CHECK']=args.check;env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONMALLOC']='malloc';env['PYTHON_JIT']='0'
def limits():
 os.setsid();ceiling=BUN_AS if args.check=='bun' else PYTHON_AS
 resource.setrlimit(resource.RLIMIT_AS,(ceiling,BUN_AS if args.check=='hybrid' else ceiling))
 resource.setrlimit(resource.RLIMIT_CPU,(120,120));resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_OUTPUT,MAX_OUTPUT))
start=time.monotonic();proc=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,preexec_fn=limits)
selector=selectors.DefaultSelector()
for stream in (proc.stdout,proc.stderr):os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
chunks=[];size=0;known={proc.pid};peak=0;reason=None
while selector.get_map():
 elapsed=time.monotonic()-start
 rss=0
 for pid in list(known):
  try:
   if os.getpgid(pid)!=proc.pid:continue
   children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split();known.update(map(int,children))
   status=Path(f'/proc/{pid}/status').read_text();match=re.search(r'^VmRSS:\s+(\d+) kB$',status,re.M);rss+=int(match[1])*1024 if match else 0
  except (OSError,ProcessLookupError,ValueError):pass
 peak=max(peak,rss)
 if elapsed>min(CHECK_SECONDS,remaining):reason='Active time bound exceeded'
 if rss>MAX_RSS:reason='Aggregate owned process resident-memory bound exceeded'
 if size+budget['output_bytes']>MAX_OUTPUT:reason='Combined output bound exceeded'
 if reason:
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
 for key,_ in selector.select(.02):
  try:data=os.read(key.fileobj.fileno(),65536)
  except BlockingIOError:continue
  if data:size+=len(data);chunks.append(data)
  else:selector.unregister(key.fileobj);key.fileobj.close()
 if reason and time.monotonic()-start>min(CHECK_SECONDS,remaining)+2:break
proc.wait(timeout=3)
try:os.killpg(proc.pid,signal.SIGKILL)
except ProcessLookupError:pass
raw=b''.join(chunks);stamp=len(budget['checks'])+1;log=OUT/f'check-{args.check}-{stamp}.log';log.write_bytes(raw)
elapsed=time.monotonic()-start;budget['active_seconds']+=elapsed;budget['output_bytes']+=size
budget['checks'].append({'phase':args.check,'argv':argv,'seconds':elapsed,'output_bytes':size,'peak_group_rss':peak,'returncode':proc.returncode,'reason':reason,'log':log.name})
os.lseek(ledger,0,os.SEEK_SET);os.ftruncate(ledger,0);os.write(ledger,json.dumps(budget,sort_keys=True).encode());os.fsync(ledger);os.close(ledger)
text=raw.decode(errors='replace')
if args.check=='bun':
 passes=re.findall(r'(?m)^\s*(\d+) pass\s*$',text);failures=re.findall(r'(?m)^\s*(\d+) fail\s*$',text);omissions=re.findall(r'(?m)^\s*(\d+) (?:skip|todo)\s*$',text)
 passed=int(passes[0]) if len(passes)==1 else 0;failed=int(failures[0]) if len(failures)==1 else 1;skipped=sum(map(int,omissions));total=passed+failed+skipped
else:
 counts=re.findall(r'Ran (\d+) tests?',text);total=int(counts[-1]) if counts else 0
 failed=sum(int(x) for x in re.findall(r'(?:failures|errors)=(\d+)',text));skipped=sum(int(x) for x in re.findall(r'skipped=(\d+)',text))
if proc.returncode or reason or not total:failed=max(failed,1)
import hashlib
print(json.dumps({'total':total,'failed':failed,'skipped':skipped,'native_accepted':False,'reason':reason,'raw_sha256':hashlib.sha256(raw).hexdigest(),'raw_log':str(log),'peak_group_rss':peak,'role_seconds_used':budget['active_seconds'],'role_output_bytes':budget['output_bytes']}))
raise SystemExit(1 if failed or skipped else 0)
