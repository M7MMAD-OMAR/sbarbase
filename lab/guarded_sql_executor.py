"""Experimental executor for fixed provisioning SQL, not a replay coordinator.

Transport must use one fresh psql -X -qAt backend per call, with ON_ERROR_STOP,
stdin SQL, acknowledged success and sanitized errors. Caller owns exclusive
installation admission. No pooling, automatic retry or unguarded fallback.
"""
import sql_operation_fence as fence
import sql_operation_revoke as coordinator


class GuardedSQL:
    def __init__(self,transport,runtime,token,claim,attempt):
        fence.identity(runtime,token,claim,attempt)
        self.transport=transport
        self.identity=(runtime,token,claim,attempt)
        self.runtime=runtime
        self.failed=False
        self.target=None
        self.control=coordinator.observe(self._raw,runtime)
        self._raw('postgres',fence.register(*self.identity,initialize=True,
                  expected_oid=self.control['control_oid'],expected_cluster=self.control['cluster']))

    def _raw(self,database,script):
        result=self.transport(script,database=database,check=True)
        if result.returncode!=0:raise RuntimeError('Guarded SQL transport failed')
        return result.stdout

    def __call__(self,query,database='postgres',check=True):
        if self.failed:raise RuntimeError('Guarded SQL executor requires reconciliation')
        try:
            if check is not True or database not in ('postgres',self.runtime):
                raise ValueError('Guarded SQL requires acknowledged scoped execution')
            if database==self.runtime and self.target is None:
                self.target=coordinator.register_target(self._raw,*self.identity,
                    expected_control_oid=self.control['control_oid'],expected_cluster=self.control['cluster'])
            binding=self.control if database=='postgres' else self.target
            oid=binding['control_oid'] if database=='postgres' else binding['target']['oid']
            script=fence.guarded(*self.identity,query,expected_oid=oid,expected_cluster=binding['cluster'])
            result=self.transport(script,database=database,check=True)
            if result.returncode!=0:raise RuntimeError('Guarded SQL transport failed')
            return result
        except BaseException:
            # No recapture of target identity after uncertain or rejected dispatch.
            self.failed=True
            raise
