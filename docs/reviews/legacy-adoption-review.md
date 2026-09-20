# Adversarial review: legacy source HBA adoption

Date: 2026-09-20
Subject: `lab/hba_adoption.py` (249 lines), against `docs/HBA-LEGACY-ADOPTION-DESIGN.md`
Also read: `lab/test_hba_adoption.py`, `lab/hba_adoption_crash_check.py`, `docs/HBA-LEGACY-ADOPTION.md`, and the supporting modules it builds on.

Method: static reading of the module, its tests, its crash probe and its supporting readers. The unit suite was read and executed (`/usr/bin/python3 -m unittest test_hba_adoption`, 7 tests, all pass; the docker handle is mocked) and the host interpreter's `Path.exists()` behaviour was measured (`/usr/bin/python3` 3.14.7). The live crash probe was not run (it starts Docker containers) by instruction; its code was read, and the evidence file `docs/evidence/hba-adoption-crash-checks.json` was not treated as proof for any claim.

Result: 5 must-fix findings.

## Must-fix findings

### MF-1. Resume cannot complete after the `source-stopped` checkpoint

Code path. `execute` (`lab/hba_adoption.py:188`) unconditionally runs three database-dependent steps on every invocation, independent of which checkpoints already exist:

- `_wait_ready` at `lab/hba_adoption.py:211`, which polls `pg_isready` and SQL for up to 120 s and then raises;
- the generation block at `lab/hba_adoption.py:213-223`, whose `hba_generation.read_existing` (`lab/hba_generation.py:64-69`) calls `hba_target.observed`, which requires `State.Running is True` (`lab/hba_target.py:31-34`);
- the byte re-check at `lab/hba_adoption.py:239`, `hba_apply.file_digest`, which runs `docker exec sha256sum`.

The checkpoint order is `hba-completed` (`:224`), `source-stopped` (`:241`), `completed` (`:246`).

Why it is wrong. A SIGKILL between `lab/hba_adoption.py:245` and `:246`, or a failed `os.unlink` at `:247`, leaves `source-stopped.json` on disk with the container stopped. The next `execute` skips the start block (`:201`), then fails at `:211` (readiness on a stopped database), and would fail again at `:217` and `:239` even if readiness were skipped. Adoption can never reach the `completed` checkpoint; `docs/HBA-LEGACY-ADOPTION.md:27` ("A later `execute` resumes from durable checkpoints only") is false for this window. The crash probe does not cover it: its `after-hba` phase stops the child inside `checkpoint` when the phase name is `hba-completed` (`lab/hba_adoption_crash_check.py:74-79`), which is before the stop at `:242`, so the `source-stopped` to `completed` window is untested.

Minimal fix. Perform the database-dependent steps only while the HBA stage is outstanding. Gate `_wait_ready` and the generation block on `not _done(state,'hba-completed')`, and make the `:235-240` verification conditional on `not _done(state,'source-stopped')`. When both `hba-completed` and `source-stopped` are present, go straight to the `completed` checkpoint at `:246`.

### MF-2. The generation pin is published before initialization can refuse, so a refusal strands adoption

Code path. `lab/hba_adoption.py:218-222`:

```
hba_generation.publish(state,target,intent['generation'])
authority.initialize(docker,target.container_id,intent['generation'])
hba_generation.read_existing(docker,state,target=target);mode='initialized'
```

The container-side INIT refuses when a backend marker already exists (`lab/hba_authority.py:25-27`: `[ ! -e ...authority.json ]`, `mkdir ...-initialized`), and with the production docker wrapper (`check=True` in `lab/hba_adoption_crash_check.py:32-33`) that nonzero exit raises out of `authority.initialize`.

Why it is wrong. The host pin is written durably (`lab/hba_generation.py:29-39`, O_EXCL, fsynced) before the container is touched. After a definite refusal (a preexisting authority backend marker) or a crash between `lab/hba_adoption.py:219` and `:221`, `hba-generation.json` asserts a generation the container does not have. Every later run takes the `pin.exists()` branch (`:213-217`) and fails in `hba_generation.read_existing` or `authority.decode` (`lab/hba_authority.py:86-89`), so adoption is permanently incomplete with a false durable pin. `docs/HBA-LEGACY-ADOPTION-DESIGN.md:15` requires refusing preexisting backend markers; the refusal must not itself write false durable state. The same window also means the code conflates "pin exists" with "INIT was dispatched": a crash before `:221` leaves a pin for an uninitialized generation, and no recovery path exists for that case.

Minimal fix. Before `hba_generation.publish`, probe the container for the authority backend marker (the same `[ -d MARKER ] && [ ! -L MARKER ] && [ -f PATH ] && [ ! -L PATH ]` shape used by `lab/hba_authority.py:115`) and refuse without writing the pin when it is present. If a definite nonzero INIT exit must stay distinguishable from an uncertain dispatch, record that distinction instead of leaving an unqualified pin that asserts the generation.

### MF-3. Unreadable checkpoints are treated as absent, which can replay the HBA stage

Code path. `_done` (`lab/hba_adoption.py:163-164`) and the pre-write test in `checkpoint` (`lab/hba_adoption.py:137`) use `Path.exists()`. On this host, `Path.exists()` returns False when access is denied (measured with `/usr/bin/python3` 3.14.7: a file inside a 0000-mode parent reports `exists() -> False`). The house rule is the opposite: `hba_startup.require_clear` (`lab/hba_startup.py:16-21`) treats only `FileNotFoundError` as clear and lets permission errors stay fatal, as does its own comment.

Why it is wrong. The HBA-guard at `lab/hba_adoption.py:224` is `if not _done(state,'hba-completed')`. If that checkpoint is unreadable, the guard reads "not done" and the stage is re-entered. By then the journal was already consumed by `hba_settlement.complete_owned` (`:233`) and its token revoked (`lab/hba_reconcile.py:51-55`), so `lease.begin` (`:231`) republishes a journal and `authority.update` (`lab/hba_authority.py:130-142`) registers a fresh active permit, and `hba_apply.execute` (`:232`) applies and reloads again. The same tolerance in `_done(state,'database-started')` re-enters the start block at `:201-210`. This contradicts the one-shot intent of the registry and `docs/HBA-LEGACY-ADOPTION.md:37` ("the HBA stage simply runs once"). The consequence is bounded (same rules, fresh revision marker) but it is a replay of an apply that the design treats as one-shot, and it is reachable whenever a checkpoint is unreadable or otherwise stat-denied.

Minimal fix. Replace the existence tests with one helper that `os.lstat`s the phase path, returns "absent" only on `FileNotFoundError`, and re-raises every other `OSError`; use it in `_done` and in the pre-write check at `:137`.

### MF-4. Existing checkpoint files are read without the checks every other reader applies

Code path. `lab/hba_adoption.py:138` and `:154` (`path.read_text()` in both branches of `checkpoint`) and `:237` (`stored.read_text()` for the completed HBA record). These reads follow symlinks (no `O_NOFOLLOW`), do not check `S_ISREG`, owner or mode, and do not verify the envelope checksum.

Why it is wrong. Every other reader in this module and its neighbours enforces those invariants: `load` (`lab/hba_adoption.py:115-119`), `hba_apply.read_record` (`lab/hba_apply.py:103-118`), `hba_settlement.read` (`lab/hba_settlement.py:51-64`), `hba_generation.load` (`lab/hba_generation.py:42-54`). A checkpoint that is a symlink, foreign-owned, or group/world-readable is accepted and its payload trusted. The payload matters: `_done` is existence-only, the value returned by `checkpoint` is the `completed` result, and `:239` compares the live HBA digest against the digest taken from the record read at `:237`. The 0700 directory check at `:145-149` limits who can place a file, but a leftover file from an earlier run with looser permissions, or a symlink, is not rejected, and the `:237` read happens outside that directory check.

Minimal fix. Route existing-checkpoint decoding through one checked reader that opens with `O_RDONLY|O_NOFOLLOW|O_NONBLOCK`, `fstat`s for `S_ISREG`, uid, `0o600` and the size limit, then verifies the envelope checksum, and use it at `:138`, `:154` and `:237`.

### MF-5. Captured mount identity is recorded but never revalidated

Code path. `publish_intent` captures the pgdata mount and the full mount list at `lab/hba_adoption.py:96-101` into `mounts` and `volume`. `execute` verifies only Id, Name, Image and owner, via `stopped_identity` (`lab/hba_adoption.py:78-84`) and `hba_target.observed` (`lab/hba_target.py:26-36`). `intent['mounts']` and `intent['volume']` are read nowhere after `load`.

Why it is wrong. `docs/HBA-LEGACY-ADOPTION-DESIGN.md:13` requires the intent to carry the pgdata volume and mount identity, and `:15` requires preserving application services and recovery targets. Because nothing compares the live mounts to the captured inventory, a source whose pgdata mount was detached, rebound, or swapped between `publish_intent` and `execute` is started and adopted as if it were the captured source, and HBA rules are applied to a database backed by different data. This is a TOCTOU between capture and action with no detection at any point.

Minimal fix. Recompute `_mounts` from the inspect result at execute time and compare it, and the derived volume, against `intent['mounts']` and `intent['volume']` before `docker('start')` at `:207` and before the final stop observation at `:244`; refuse on any difference.

## Lesser observations, not must-fix

- `checkpoint` compares the stored and new records with `mode` excluded from both sides (`lab/hba_adoption.py:140-141`) while the race branch compares it (`:155`). The two rules differ. It is inert today because a resumed phase never rewrites an existing checkpoint.
- `volume` is `pgdata['name'] or pgdata['source']` (`lab/hba_adoption.py:100`), which is the empty string for a tmpfs-backed pgdata. The crash probe runs its fixture exactly that way (`lab/hba_adoption_crash_check.py:54`), and `_mounts` records tmpfs mounts with empty name and source (`lab/hba_adoption.py:63-65`). `validate` accepts an empty string (`:44`), so the recorded pgdata volume can be empty.
- The `generation-initialized` checkpoint (`lab/hba_adoption.py:223`) is written but never consulted; `execute` always re-runs the generation block. The claim that the checkpoints distinguish that phase (`docs/HBA-LEGACY-ADOPTION.md:20`) is about existence, not gating.
- `checkpoint` creates the `hba-adoption-checkpoints` directory (`lab/hba_adoption.py:145`) but the parent `state` directory entry for it is never fsynced (only the checkpoints directory itself is, at `:159`).
- `lab/hba_adoption.py:203-205` discriminates two failure modes by matching the message text `'not stopped'`; this is the only such test in the module.

## Claims I could not falsify

- Stop failure reported as complete. `docker('stop', ...)` (`lab/hba_adoption.py:242`) precedes `stopped_identity` (`:244`) and the checkpoint (`:245`); with `check=True` a nonzero stop raises before the checkpoint, and a still-running container raises in `inspect_exact` (`lab/hba_adoption.py:74`). `test_uncertain_stop_keeps_adoption_incomplete` exercises this.
- Wrong-order checkpoints. Each checkpoint is written only after the condition it asserts was observed: `database-started` after start plus identity re-observation (`:207-210`), `generation-initialized` after initialize plus read (`:221-223`), `hba-completed` after settlement returns (`:233-234`), `source-stopped` after the stopped observation (`:244-245`), `completed` last (`:246`). I found no order in which a failure is checkpointed as success.
- Replay of INIT. INIT is dispatched only when no pin exists (`:218-221`), and the pin is durable and O_EXCL. I found no path that repeats it. The separate problem is that the pin can exist without INIT having been dispatched (MF-2).
- Replay of the HBA apply on the normal paths. The stage is gated by `_done('hba-completed')` (`:224`), and a completed adoption refuses replay (`:191`, probe `:153-159`). The only HBA replay I could construct is the unreadable-checkpoint path in MF-3.
- Checkpoint identity confusion across intents. Checkpoints embed the adoption UUID and the intent digest (`lab/hba_adoption.py:167-168`) and conflicting writes raise (`:142`, `:155`). I could not construct a reachable state in which checkpoints from a different intent are trusted to produce success: a second `publish_intent` into the same state directory is blocked either by `_done('completed')` (`:191`) or by the pin target check (`:197`, `:215`). This holds only because the intent file is immutable (O_EXCL, never rewritten) and is unlinked only after completion; `_done` itself does not re-bind a checkpoint to the loaded intent, so the property is a consequence of the intent lifecycle, not of a check.
- Path traversal and symlink acceptance for the intent. `NAME`, `CHECKPOINTS` and `PHASES` are fixed and the phase name is allow-listed (`lab/hba_adoption.py:128-130`), so no caller-controlled path reaches the filesystem. The intent is opened `O_NOFOLLOW` and validated for `S_ISREG`, owner, `0o600` and the size limit (`:115-119`). Symlinked checkpoints are a real gap and are MF-4, not an intent gap.
- Group or world-readable evidence produced by this module. Every file this module creates is opened with mode `0o600` at creation (`lab/hba_adoption.py:105`, `:152`), with no open-then-chmod window, and the checkpoint directory is created 0700 and re-checked (`:145-149`). The exposure is on the read side (MF-4), where existing evidence with looser permissions is accepted.
- Absence of the intent being treated as clear. `load` (`lab/hba_adoption.py:113`) does not catch `FileNotFoundError`, and `execute` calls it unconditionally (`:192`), so a missing intent raises rather than permitting adoption.
- Torn-write blocking. `publish_intent` writes non-atomically under O_EXCL (`lab/hba_adoption.py:105-107`) and `checkpoint` likewise (`:152-158`); a torn intent or checkpoint blocks the next run rather than being accepted. The design intends this for the intent (`docs/HBA-LEGACY-ADOPTION-DESIGN.md:13`).
- Unit-test coverage of these findings. The suite (7 tests) does not cross the `source-stopped` to `completed` window, does not use an unreadable or symlinked checkpoint, does not change mounts between capture and execute, does not present a preexisting backend marker, and asserts pinned generation identity in the probe, not the suite.