# Native worker HBA interruption

Verified 2026-09-20 with two separate disposable installations:

```
/usr/bin/python3 lab/fresh-worker-check.py --hba-crash after-intent
/usr/bin/python3 lab/fresh-worker-check.py --hba-crash after-witness
```

Each run passes 90 checks, including the same 76-check healthy startup, worker, restart, SDK and cleanup baseline. These are overlapping runs, not 180 independent checks. Evidence: [registered intent](evidence/worker-hba-crash-after-intent.json), [durable apply witness](evidence/worker-hba-crash-after-witness.json).

## What actually dies

A second project environment is queued through the real catalog and provisioned by the actual worker, guardian and native source runtime. A fixture-only `sitecustomize.py` profiles the exact native entry point and function code object. It checks a normal expected return, exact services receipt identity and raw journal digest, fsyncs a checkpoint, disables itself and sends SIGKILL to its own native process.

The separate guardian hook records its reaped child's actual return code of negative SIGKILL, bound to the checkpoint PID and receipt. The test does not infer process death from a marker or saved PID, and it never signals another process by a saved PID. Production source contains no crash hook. The hook directory and diagnostics remain private under ignored fixture state.

## Outcomes

| Checkpoint | Observed HBA state | Explicit HBA-only reconciliation |
|---|---|---|
| After intent registration returns | Exact active token, unchanged baseline bytes, no apply attempt or completion file | Retire authority and archive the baseline observation |
| After apply returns normally | Exact active token and durable matching apply/reload witness | Retire authority and archive that exact witness |

Fresh worker/effect/operation locks establish that prior ownership ended. Before reconciliation, the normal worker refuses specifically because the HBA journal is pending, while installation startup also refuses unresolved state. Reconciliation performs no HBA apply or PostgreSQL SQL call and leaves file bytes unchanged.

In both cases, the pending worker receipt, services marker, exact running catalog claim and absence of native whole-job success remain unchanged. After the HBA journal clears, an uninstrumented worker still refuses specifically because effects reached the services stage. HBA settlement cannot authorize job replay or convert it to success.

## Limits

These tests kill the native worker at two completed checkpoints. They do not kill the whole supervisor, interrupt a Docker helper inside publication, simulate power loss, settle service migrations or establish automatic later-stage recovery. The HBA archive retains unknown activation. All fixture Docker resources were removed under fresh ownership and exact namespace checks. Retained source, recovery targets and unrelated resources were not used.

The full Python suite remains 217 passing tests. The unchanged recorded Bun suite is 73 tests/408 assertions. Independent review found no must-fix in the probe or its scoped evidence.
