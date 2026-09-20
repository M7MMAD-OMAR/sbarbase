"""Read-only observations for interrupted upstream provisioning, never replay authority."""
from contextlib import ExitStack,contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

OWNERS=('durable-upstream','recovery-target')
RUNTIME=re.compile(r'e_[a-f0-9]{24}')
TOKEN=re.compile(r'[a-f0-9-]{36}')


def read_json(path):
    try:
        with path.open('rb') as source:data=source.read(65537)
    except FileNotFoundError:return 'missing',None
    except OSError:return 'unreadable',None
    if len(data)>65536:return 'malformed',None
    try:value=json.loads(data)
    except (ValueError,UnicodeError):return 'malformed',None
    return 'present',value


@contextmanager
def existing_locks(state):
    with ExitStack() as stack:
        for name in ('worker.lock','effect.lock','operation.lock'):
            fd=os.open(state/name,os.O_RDWR)
            stack.callback(os.close,fd)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def docker(*args,data=None):
    result=subprocess.run(['docker',*args],input=data,text=True,capture_output=True,timeout=10)
    if result.returncode:raise RuntimeError('Docker observation unavailable')
    return result.stdout


def owned_inventory(command):
    ids=set()
    for owner in OWNERS:
        ids.update(command('ps','-aq','--no-trunc','--filter','label=io.sbarbase.owner='+owner).split())
    if any(not re.fullmatch('[a-f0-9]{64}',value) for value in ids):raise ValueError('Invalid inventory identity')
    if not ids:return []
    items=json.loads(command('inspect',*sorted(ids)))
    if not isinstance(items,list) or any(not isinstance(x,dict) for x in items) or {x.get('Id') for x in items}!=ids or len(items)!=len(ids):
        raise ValueError('Inventory changed during observation')
    result=[]
    for item in items:
        owner=(item.get('Config',{}).get('Labels') or {}).get('io.sbarbase.owner')
        if owner not in OWNERS or type(item.get('State',{}).get('Running')) is not bool:
            raise ValueError('Inventory ownership unavailable')
        name=item.get('Name','').lstrip('/')
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]*',name):raise ValueError('Invalid resource name')
        state=item['State']
        result.append({'id':item['Id'],'name':name,'owner':owner,'running':state['Running'],
                       'oom_killed':state.get('OOMKilled') is True,
                       'exec_ids_reported':len(item.get('ExecIDs') or [])})
    return sorted(result,key=lambda x:x['name'])


def valid_receipt(value):
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1 or value.get('phase') not in ('pending','completed'):return False
    if not isinstance(value.get('token'),str) or not TOKEN.fullmatch(value['token']):return False
    job=value.get('job')
    return (isinstance(job,dict) and isinstance(job.get('runtime'),str) and bool(RUNTIME.fullmatch(job['runtime']))
            and all(isinstance(job.get(k),str) and 0<len(job[k])<=200 for k in ('environment','claim'))
            and type(job.get('attempt')) is int and 0<job['attempt']<2**31)


def witness_status(state,receipt):
    status,witness=read_json(state/'effect-outcomes'/(receipt['token']+'.json'))
    if status!='present':return status,None
    if (not isinstance(witness,dict) or type(witness.get('version')) is not int or witness['version']!=1 or witness.get('phase')!='native-completed'
            or witness.get('token')!=receipt['token'] or witness.get('native')!=receipt.get('native')
            or receipt.get('native') not in ('durable-provision-v1','component-provision-v1')
            or witness.get('job')!=receipt['job'] or type(witness.get('exitCode')) is not int
            or witness['exitCode'] not in (0,75)
            or (witness['exitCode']==75 and receipt['native']!='durable-provision-v1')):
        return 'mismatch',None
    return 'matching',witness['exitCode']


def catalog_status(state,receipt,code):
    db=sqlite3.connect((state/'control.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        job=receipt['job']
        row=db.execute('SELECT runtime,state,attempt,claim FROM provision_jobs WHERE environment=?',(job['environment'],)).fetchone()
        if row is None:return {'status':'missing'}
        result={'status':'mismatch','state':row[1],'attempt':row[2]}
        prior=db.execute('SELECT runtime,claim,exit_code FROM provision_effect_results WHERE environment=? AND attempt=?',
                         (job['environment'],job['attempt'])).fetchone()
        if row[0]!=job['runtime'] or row[2]<job['attempt']:return result
        if prior is not None:
            if code is not None and prior==(job['runtime'],job['claim'],code):result['status']='historical_outcome_matches'
            return result
        if row[1]=='running' and row[2]==job['attempt'] and row[3]==job['claim']:result['status']='current_claim_matches'
        return result
    finally:db.close()


def database_status(inventory,runtime,command):
    if not isinstance(runtime,str) or not RUNTIME.fullmatch(runtime):raise ValueError('Invalid runtime identifier')
    sources=[x for x in inventory if x['name']=='sbarbase-durable-db' and x['owner']=='durable-upstream']
    if not sources:return {'status':'owned_source_not_observed'}
    source=sources[0]
    if not source['running']:return {'status':'source_stopped_not_queried'}
    # The caller has validated the identifier. Never request query text or secrets.
    query=("BEGIN READ ONLY; SET LOCAL statement_timeout='2s'; SET LOCAL lock_timeout='500ms'; "
           "SELECT json_build_object('database_exists',EXISTS(SELECT 1 FROM pg_database WHERE datname='"+runtime+"'),"
           "'allows_connections',(SELECT datallowconn FROM pg_database WHERE datname='"+runtime+"'),"
           "'sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname='"+runtime+"'),"
           "'scoped_roles',(SELECT count(*) FROM pg_roles WHERE rolname IN ('"+runtime+"_auth','"+runtime+"_rest','"+runtime+"_storage'))); ROLLBACK;")
    raw=command('exec','-i',source['id'],'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres',data=query)
    value=json.loads(raw)
    if (not isinstance(value,dict) or type(value.get('database_exists')) is not bool
            or (value.get('allows_connections') is not None and type(value.get('allows_connections')) is not bool)
            or any(type(value.get(k)) is not int or value[k]<0 for k in ('sessions','scoped_roles'))):
        raise ValueError('Invalid database observation')
    return {'status':'observed',**{k:value[k] for k in ('database_exists','allows_connections','sessions','scoped_roles')}}


def inspect_state(state,command=docker):
    report={'version':1,'observed_at':datetime.now(timezone.utc).strftime('%Y-%m-%d %I:%M:%S %p UTC'),
            'safe_to_replay':False,'scope':'Observation only; daemon-side completion remains unknown'}
    try:
        with existing_locks(state):
            report['local_ownership']='exclusive_snapshot'
            status,receipt=read_json(state/'worker-effect.json')
            report['receipt_status']=status
            valid=status=='present' and valid_receipt(receipt)
            if status=='present' and not valid:report['receipt_status']='malformed'
            if valid:
                report['receipt']={'phase':receipt['phase'],'attempt':receipt['job']['attempt'],
                    'runtime':receipt['job']['runtime'],'identity_digest':hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest()}
                witness,code=witness_status(state,receipt);report['native_witness']=witness
                if receipt['phase']=='completed':
                    code=receipt.get('exitCode') if type(receipt.get('exitCode')) is int and receipt['exitCode'] in (0,75) else None
                try:report['catalog']=catalog_status(state,receipt,code)
                except (sqlite3.Error,OSError):report['catalog']={'status':'unavailable'}
                report['next_action']=('settle_known_outcome_under_fresh_lease' if code is not None and report['catalog']['status'] in
                    ('current_claim_matches','historical_outcome_matches') else 'inspect_unresolved_effects')
            else:report['next_action']='no_pending_receipt' if status=='missing' else 'repair_invalid_evidence'
            try:
                report['containers']=owned_inventory(command);report['docker_status']='observed'
            except (OSError,ValueError,KeyError,TypeError,AttributeError,RuntimeError,subprocess.SubprocessError):
                report['docker_status']='unavailable';report['containers']=None
            if valid and report['containers'] is not None:
                try:report['database']=database_status(report['containers'],receipt['job']['runtime'],command)
                except (OSError,ValueError,KeyError,TypeError,AttributeError,RuntimeError,subprocess.SubprocessError):
                    report['database']={'status':'unavailable'}
            else:report['database']={'status':'not_queried'}
            report['status']='observed'
    except BlockingIOError:report.update(status='busy',local_ownership='unavailable')
    except FileNotFoundError:report.update(status='lock_files_missing',local_ownership='unavailable')
    except OSError:report.update(status='lock_files_unreadable',local_ownership='unavailable')
    return report
