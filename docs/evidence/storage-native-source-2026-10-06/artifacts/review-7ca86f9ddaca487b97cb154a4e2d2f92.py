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
METADATA_RESERVE=1024
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
if not budget.get('metadata_reservation_v1'):
 reserve=METADATA_RESERVE*sum(row['phase'] in ('python','hybrid','bun') for row in budget['checks'])
 budget['output_bytes']+=reserve;budget['metadata_reservation_v1']=reserve
remaining=ROLE_SECONDS-budget['active_seconds']
allowance=MAX_OUTPUT-budget['output_bytes']-METADATA_RESERVE
if remaining<=0 or allowance<=0:raise SystemExit('Frozen source role budget exhausted')
argv=[frozen['python_executable'],str(OUT/'run-python.py')] if args.check!='bun' else ['/home/sbarah/.bun/bin/bun','test','tests/storage-settlement-contract.test.ts']
env=dict(os.environ);env['SETTLEMENT_CHECK']=args.check;env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONMALLOC']='malloc';env['PYTHON_JIT']='0';env['LC_ALL']='C';env['PYTHONUTF8']='1'
def limits():
 os.setsid();ceiling=BUN_AS if args.check=='bun' else PYTHON_AS
 resource.setrlimit(resource.RLIMIT_AS,(ceiling,BUN_AS if args.check=='hybrid' else ceiling))
 resource.setrlimit(resource.RLIMIT_CPU,(120,120));resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_OUTPUT,MAX_OUTPUT))
start=time.monotonic();proc=None;selector=selectors.DefaultSelector()
chunks=[];size=0;observed_size=0;known=set();peak=0;reason=None
window=min(CHECK_SECONDS,remaining);deadline=max(0,window-min(4,window/4))
def kill_owned_group():
 if proc is not None:
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
def interrupted(signum,frame):
 raise RuntimeError('Source supervisor interrupted by signal '+str(signum))
signal.signal(signal.SIGTERM,interrupted)
signal.signal(signal.SIGINT,interrupted)
try:
 proc=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,preexec_fn=limits)
 known.add(proc.pid)
 for stream in (proc.stdout,proc.stderr):os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
 # Pipe closure does not mean the owned process has ended.
 while selector.get_map() or proc.poll() is None:
  elapsed=time.monotonic()-start;rss=0
  for pid in list(known):
   try:
    if os.getpgid(pid)!=proc.pid:continue
    children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split();known.update(map(int,children))
    status=Path(f'/proc/{pid}/status').read_text();match=re.search(r'^VmRSS:\s+(\d+) kB$',status,re.M);rss+=int(match[1])*1024 if match else 0
   except (OSError,ProcessLookupError,ValueError):pass
  peak=max(peak,rss)
  if elapsed>deadline:reason='Active time bound exceeded'
  if rss>MAX_RSS:reason='Aggregate owned process resident-memory bound exceeded'
  if reason:kill_owned_group()
  for key,_ in selector.select(.02):
   try:data=os.read(key.fileobj.fileno(),65536)
   except BlockingIOError:continue
   if data:
    observed_size+=len(data);left=max(0,allowance-size);kept=data[:left]
    if kept:chunks.append(kept);size+=len(kept)
    if len(data)>left:
     reason='Combined retained output bound exceeded';kill_owned_group()
   else:selector.unregister(key.fileobj);key.fileobj.close()
  if reason and time.monotonic()-start>window:break
except BaseException as error:
 reason='Source supervision failed: '+type(error).__name__+': '+str(error)[:200]
finally:
 # Cleanup and accounting run for closed pipes, exceptions and interruptions.
 signal.signal(signal.SIGTERM,signal.SIG_IGN);signal.signal(signal.SIGINT,signal.SIG_IGN)
 kill_owned_group()
 if proc is not None:
  try:proc.wait(timeout=max(.05,min(2,window/4)))
  except subprocess.TimeoutExpired:reason='Owned test process cleanup unresolved'
  for stream in (proc.stdout,proc.stderr):
   if stream is not None:stream.close()
 selector.close()
 raw=b''.join(chunks);stamp=len(budget['checks'])+1;log=OUT/f'check-{args.check}-{stamp}.log'
 try:log.write_bytes(raw)
 finally:
  elapsed=time.monotonic()-start;budget['active_seconds']+=elapsed;budget['output_bytes']+=size+METADATA_RESERVE
  returncode=proc.returncode if proc is not None else 1
  budget['checks'].append({'phase':args.check,'argv':argv,'seconds':elapsed,'output_bytes':size+METADATA_RESERVE,'retained_output_bytes':size,'observed_output_bytes':observed_size,'metadata_reserved_bytes':METADATA_RESERVE,'peak_group_rss':peak,'returncode':returncode,'reason':reason,'log':log.name})
  os.lseek(ledger,0,os.SEEK_SET);os.ftruncate(ledger,0);os.write(ledger,json.dumps(budget,sort_keys=True).encode());os.fsync(ledger);os.close(ledger)
text=raw.decode(errors='replace')
if args.check=='bun':
 passes=re.findall(r'(?m)^\s*(\d+) pass\s*$',text);failures=re.findall(r'(?m)^\s*(\d+) fail\s*$',text);omissions=re.findall(r'(?m)^\s*(\d+) (?:skip|todo)\s*$',text)
 passed=int(passes[0]) if len(passes)==1 else 0;failed=int(failures[0]) if len(failures)==1 else 1;skipped=sum(map(int,omissions));total=passed+failed+skipped
else:
 counts=re.findall(r'Ran (\d+) tests?',text);total=int(counts[-1]) if counts else 0
 failed=sum(int(x) for x in re.findall(r'(?:failures|errors)=(\d+)',text));skipped=sum(int(x) for x in re.findall(r'skipped=(\d+)',text))
if returncode or reason or not total:failed=max(failed,1)
summary=json.dumps({'total':total,'failed':failed,'skipped':skipped,'native_accepted':False,'reason':reason,'raw_sha256':hashlib.sha256(raw).hexdigest(),'raw_log':str(log),'peak_group_rss':peak,'role_seconds_used':budget['active_seconds'],'role_output_bytes':budget['output_bytes']})
if len(summary.encode())+1>METADATA_RESERVE:raise SystemExit('Reserved source metadata output exceeded')
print(summary)
raise SystemExit(1 if failed or skipped else 0)
