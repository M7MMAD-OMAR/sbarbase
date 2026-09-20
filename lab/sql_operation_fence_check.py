"""Concurrent barrier assertions inside an already owned disposable container."""
import subprocess
import time
import uuid
import sql_operation_fence as fence


def run(container,admin,check,docker,sql):
    prefix='sbar_fence_'+uuid.uuid4().hex[:12]
    runtime='e_'+uuid.uuid4().hex[:24]
    token,claim=str(uuid.uuid4()),str(uuid.uuid4())
    args=(runtime,token,claim,1)
    processes=[]
    def command(script,database='postgres',user=admin):
        return docker('exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',user,'-d',database,data=script,check=False)
    def good(script):
        result=command(script)
        if result.returncode:raise RuntimeError('SQL barrier setup or mutation failed')
        return result.stdout.strip()
    def spawn(name,script,keep_open=False):
        tag=prefix+'_'+name
        process=subprocess.Popen(['docker','exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',admin,'-d','postgres'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        processes.append((process,tag))
        process.stdin.write(f"SET application_name='{tag}';\n"+script);process.stdin.flush()
        if not keep_open:process.stdin.close();process.stdin=None
        return process,tag
    def finish(process):
        if process.stdin is not None:process.stdin.close();process.stdin=None
        _,error=process.communicate(timeout=5)
        return process.returncode,error
    def await_lock(tag,granted):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=sql(container,f"SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid) WHERE a.application_name='{tag}' AND l.locktype='advisory' AND l.granted={'true' if granted else 'false'});").stdout.strip()
            if result=='t':return
            time.sleep(.02)
        raise RuntimeError('Expected SQL barrier lock state not observed')
    try:
        good('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES GRANT USAGE ON SCHEMAS TO anon,authenticated,service_role;')
        bootstrap_gate,bootstrap_gate_tag=spawn('bootstrap_gate','SELECT pg_advisory_lock(471193120992);\n',True)
        await_lock(bootstrap_gate_tag,True)
        blocked_bootstrap=fence.bootstrap().replace('REVOKE ALL ON TABLE', 'SELECT pg_advisory_lock(471193120992);\nREVOKE ALL ON TABLE',1)
        initializer,initializer_tag=spawn('initializer',blocked_bootstrap)
        await_lock(initializer_tag,False)
        check('registry metadata invisible before all revocations commit',good("SELECT count(*) FROM pg_namespace WHERE nspname='sbarbase_provision_guard';")=='0')
        check('bootstrap gate released',finish(bootstrap_gate)[0]==0)
        check('atomic private registry bootstrap completed',finish(initializer)[0]==0)
        for role in ('anon','authenticated','service_role'):
            denied=command(f'SET ROLE {role}; SELECT * FROM {fence.TABLE};')
            check(role+': registry denies even pre-existing default grants',denied.returncode!=0 and 'permission denied for schema' in denied.stderr)
        good('CREATE TABLE public.fence_events(value text);')
        denied=command(f'SELECT * FROM {fence.TABLE};',user='neighbor')
        check('registry hidden from unprivileged neighbor',denied.returncode!=0 and 'permission denied for schema' in denied.stderr)
        good(fence.register(*args));good(fence.register(*args))
        check('exact registration idempotent',good(f'SELECT count(*) FROM {fence.TABLE};')=='1')
        good(fence.guarded(*args,"INSERT INTO public.fence_events VALUES ('initial');"))
        wrong=command(fence.guarded(runtime,token,str(uuid.uuid4()),1,"INSERT INTO public.fence_events VALUES ('wrong');"))
        check('wrong claim cannot mutate',wrong.returncode!=0 and 'SQL operation is not active' in wrong.stderr)
        gate,gate_tag=spawn('gate','SELECT pg_advisory_lock(471193120991);\n',True)
        await_lock(gate_tag,True)
        active,active_tag=spawn('active',fence.guarded(*args,"SELECT pg_advisory_lock(471193120991); INSERT INTO public.fence_events VALUES ('active'); SELECT pg_advisory_unlock(471193120991);"))
        await_lock(active_tag,False)
        cancelled,cancelled_tag=spawn('cancelled',fence.revoke(*args))
        await_lock(cancelled_tag,False)
        check('revocation waits behind live guarded batch',cancelled.poll() is None)
        good(f"SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE application_name='{cancelled_tag}';")
        code,error=finish(cancelled)
        check('cancelled revocation is not success',code!=0 and 'canceling statement' in error)
        check('cancelled revocation leaves active authority',good(f"SELECT state FROM {fence.TABLE} WHERE token='{token}';")=='active')
        revoker,revoker_tag=spawn('revoke',fence.revoke(*args));await_lock(revoker_tag,False)
        delayed,delayed_tag=spawn('delayed',fence.guarded(*args,"INSERT INTO public.fence_events VALUES ('delayed');"));await_lock(delayed_tag,False)
        check('same database excludes a competing session',good(f'SELECT pg_try_advisory_lock({fence.lock_key(runtime)});')=='f')
        other=command(f'SELECT pg_try_advisory_lock({fence.lock_key(runtime)});','neighbor')
        check('same numeric key does not fence another database',other.returncode==0 and other.stdout.strip()=='t')
        check('gate releases successfully',finish(gate)[0]==0)
        check('already admitted SQL batch completes before revoke',finish(active)[0]==0)
        check('revocation commit acknowledged',finish(revoker)[0]==0)
        code,error=finish(delayed)
        check('queued old batch rechecks after lock and cannot mutate',code!=0 and 'SQL operation is not active' in error)
        check('only admitted writes exist',good('SELECT string_agg(value,\',\' ORDER BY value) FROM public.fence_events;')=='active,initial')
        check('revoked registration cannot resurrect',command(fence.register(*args)).returncode!=0)
        tombstone=(runtime,str(uuid.uuid4()),str(uuid.uuid4()),2)
        good(fence.revoke(*tombstone))
        check('revoke before registration leaves durable tombstone',command(fence.register(*tombstone)).returncode!=0)
        stale=(runtime,str(uuid.uuid4()),str(uuid.uuid4()),1)
        check('older never-registered attempt cannot become active',command(fence.register(*stale)).returncode!=0)
        fresh=(runtime,str(uuid.uuid4()),str(uuid.uuid4()),3)
        good(fence.register(*fresh))
        check('same attempt with different token cannot replace authority',command(fence.register(runtime,str(uuid.uuid4()),str(uuid.uuid4()),3)).returncode!=0)
        good(fence.revoke(*args))
        check('historical token revoke preserves newer active token',good(f"SELECT state FROM {fence.TABLE} WHERE token='{fresh[1]}';")=='active')
        mismatch=command(fence.revoke(runtime,fresh[1],str(uuid.uuid4()),3))
        check('mismatched revoke refuses without changing active token',mismatch.returncode!=0 and good(f"SELECT state FROM {fence.TABLE} WHERE token='{fresh[1]}';")=='active')
        good(fence.guarded(*fresh,'CREATE DATABASE guard_created ALLOW_CONNECTIONS false;'))
        check('guarded CREATE DATABASE is outside transaction block',good("SELECT NOT datallowconn FROM pg_database WHERE datname='guard_created';")=='t')
        check('reconnected old token remains denied',command(fence.guarded(*args,"INSERT INTO public.fence_events VALUES ('reconnect');")).returncode!=0)
    finally:
        try:
            tags=','.join("'"+tag+"'" for _,tag in processes)
            if tags:good(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name IN ({tags});")
        finally:
            for process,_ in processes:
                if process.poll() is None:process.kill()
                process.communicate(timeout=5)
