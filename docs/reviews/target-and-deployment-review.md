# Adversarial review: recovery-target HBA authority, retained adoption, restore path and server deployment

Date: 2026-09-20
Reviewer: independent adversarial pass (static reading plus read-only unit tests)
Scope: `lab/hba_runtime.py` (target state, `prepare_target_state`, `TargetHBA`,
`before_create`), `lab/adopt-retained.py`, `lab/verify-retained.py`,
`lab/recovery-restore-db.py` (owned-writer region), `lab/install_server.py`,
`deploy/sbarbase.service`, `docs/SERVER-DEPLOYMENT.md`, the three new test
modules, and the claims in `docs/TARGET-HBA-WRITERS.md`.

Method: every file above was read in full; supporting modules
(`hba_adoption.py`, `hba_authority.py`, `hba_journal.py`, `hba_startup.py`,
`hba_ownership.py`, `hba_apply.py`, `hba_settlement.py`, `hba_reconcile.py`,
`hba_generation.py`, `hba_target.py`, `atomic_hba.py`, `effect_receipt.py`,
`durable_runtime.py`, `dev.py`, `installation_runtime.py`, `bootstrap.py`,
`bootstrap.ts`, `upstream-server.ts`, `recovery_reconcile.py`) were read to
establish what the reviewed code actually reaches. No container was started or
stopped, no live probe was run, and no repository file other than this review was
modified. The read-only suites `test_target_hba`, `test_recovery_owned_hba`,
`test_install_server`, `test_hba_adoption` and `test_hba_runtime` were executed
with `/usr/bin/python3 -m unittest` and pass (45 tests, OK).

Findings below are ordered by severity. Each names the evidence and a minimal
fix. Defects I could not establish are listed in the second section; claims that
I judged in the third section.

## Must-fix defects

### 1. The installer cannot pass its own pinned-image step (`images.lock.json` has no top-level `id`)

Evidence: `lab/install_server.py:164-168`.

```
for lock in LOCKS:                       # LOCKS includes 'images.lock.json'
    entry=json.loads((ROOT/'lab'/lock).read_text())
    digest=entry['id']                   # line 166
```

`lab/images.lock.json` is `{"db": {...}, "auth": {...}, "rest": {...}}`; it has
no top-level `id`. `entry['id']` raises `KeyError`, which is not caught, so
`install` aborts inside step 2 with a traceback before the
"Pinned image pull failed" branch at line 168 is ever reached. Reproduced by
reading the three lock files and indexing each one: `distro-image.lock.json` and
`storage-image.lock.json` yield a digest, `images.lock.json` raises `KeyError`.

Secondary concern in the same block: even for the two files that do carry a
top-level `id`, the pull is issued as `docker pull sha256:<hex>`. `images()` (line
77-79) deliberately matches every nested `id`, which shows the author knew the
pins are nested, and each lock file also carries a `digests` list in the
`repository@sha256:<hex>` form that `docker pull` requires. The bare-digest form
could not be executed here (no Docker), so it is recorded as an unverified risk,
but the minimal fix covers both: iterate every pin the way `images()` does and
pull the first entry of that entry's `digests` list, falling back to `id`.

Minimal fix: replace lines 164-168 with a walk of each lock entry (top level and
nested) and `docker pull` the `repository@digest` reference.

### 2. `smoke` reports success with the console API server down or absent

Evidence: `lab/install_server.py:205-213`.

```
server=STATE/'server.json'
console='missing'
if server.exists():
    pid=json.loads(server.read_text()).get('pid')
    console=f'server pid {pid}' if pid else 'no pid'
for name,value in checks:print(...)
print('console:',console)
ok=all(value==200 for name,value in checks if name.endswith('auth') or name.endswith('rest'))
```

`ok` is derived only from the endpoint checks. The console pid is printed and
never validated: a missing `server.json` prints `missing`, a stale pid prints
`server pid N`, and neither changes `ok`. `lab/upstream-server.ts:14` is the only
writer of `server.json`, and `install_server.install()` never starts that server
(see the closing text at `install_server.py:184-186`, which tells the operator to
start it separately). Therefore the documented happy path
(`docs/SERVER-DEPLOYMENT.md:72`, run `smoke` after install) returns exit 0 and
prints a console line while the console is not running at all. This is an error
path that reports success.

Minimal fix: after reading the pid, verify liveness (`os.kill(pid, 0)` in a
`try/except ProcessLookupError`) and include the console in `ok`; treat a missing
`server.json` as failure.

### 3. `authority.close()` is skipped on every failure path

Evidence: `lab/recovery-restore-db.py:113-118, 194-197`.

The three per-target ownership descriptors are acquired through an `ExitStack`
at line 113-118, but the stack is closed at line 197, after the `try/finally` and
after the evidence write at line 196. On any failure the `finally` at 194 runs
`cleanup_target`, which itself raises when cleanup is incomplete (line 57-58), so
the original exception propagates and line 197 is never executed. The three
`flock` descriptors are then held for the remainder of the process. The process
exits shortly after, so this is not a durable leak, but it is exactly the lock
discipline the protocol is built on, and it means a future in-process caller
would silently keep a target's authority locked.

Minimal fix: `with authority:` around the body, or move `authority.close()` into
the `finally` before `cleanup_target`.

### 4. Any failure after `ready()` leaves the target in a permanent dead end

Evidence: `lab/recovery-restore-db.py:62, 95-96, 138, 173`;
`lab/hba_adoption.py:249-254`; `lab/recovery_reconcile.py` (entire file).

`hba_writer.ready(..., created=True)` at line 138 calls
`hba_generation.initialize`, which durably writes
`<install state>/targets/<prefix>/hba-generation.json`
(`hba_generation.py:72-79`). If the run then fails anywhere between line 139 and
line 192 (role creation, database creation, `pg_restore`, boundary
reconciliation, HBA publication, table verification), the durable state left
behind is:

- `recovery-target.json` exists, so a rerun refuses at line 62
  ("Retained target descriptor exists; resume it explicitly");
- `targets/<prefix>/hba-generation.json` exists with that run's generation, so
  `lab/adopt-retained.py target` refuses: `hba_adoption.execute` loads the
  existing pin and raises "Preexisting generation pin conflicts with adoption
  intent" (`hba_adoption.py:251-254`), because the intent's generation is a fresh
  UUID;
- `lab/recovery_reconcile.py` only stops retained containers and writes the
  descriptor status; it never touches the per-target HBA state. The settlement
  and reconcile entry points (`hba_settlement.cancel_baseline`,
  `complete_applied`, `hba_reconcile.retire`) are reachable only from probes and
  tests, not from any operator command.

So the only way forward is hand-deleting private state files, which
`docs/SERVER-DEPLOYMENT.md:49` explicitly forbids ("Never delete a pin, journal
or receipt to bypass the gate"). The failure branch is not fault-injected today,
which is why this has not surfaced.

Minimal fix: make adoption accept an existing pin whose target and owner equal
the captured container and bind the adoption to that pinned generation, or add an
explicit per-target reconcile command that can retire an unstarted generation.

### 5. `adopt-retained.py` never checks the whole placement is stopped

Evidence: `lab/adopt-retained.py:60-69`.

The script inspects only the single database container and refuses if that
container is running (line 64-67). It never runs the placement-wide check that
its sibling does: `lab/recovery-restore-db.py:87` refuses when any container with
`io.sbarbase.owner=<owner>` is running. The module docstring (lines 9-14) lists
"every container of that placement is stopped" as an operational assumption, but
the analogous guard already exists one file over, so the asymmetry is an
oversight rather than a design decision. Adoption starts the database and then
stops it; a concurrently running source Auth/REST container would observe the
database disappearing and would itself invalidate the quiescence assumption the
whole operation rests on.

Minimal fix: before `publish_intent`, refuse when
`docker ps -q --filter label=io.sbarbase.owner=<owner>` is non-empty.

### 6. Target-role verification opens the source installation catalog read-write

Evidence: `lab/verify-retained.py:68-73`.

```
catalog=sqlite3.connect(STATE/'control.sqlite')
environments=sorted(...)
catalog.close()
environment=json.loads((STATE/'recovery-target.json').read_text())['environment'] if role=='target' else None
```

For `role == 'target'` this query result is discarded (line 71-73 replaces the
list with the single target environment), yet the connection is still opened
against the installation root's `control.sqlite` with the default read-write
mode. `sqlite3.connect` creates the database file when it is missing, and the
default rollback journal can write `-journal`/`-wal` files into the source's
private state directory. This is the one place a target-scoped operation reaches
into the source authority state, and it is the concrete exception to the
per-target isolation claim. `effect_receipt.py:130` shows the in-repo pattern
already in use (`...as_uri()+'?mode=ro'`).

Minimal fix: skip the catalog entirely when `role == 'target'`, or open it with
`?mode=ro` via the URI form.

### 7. The systemd unit does not put `bun` on `PATH`

Evidence: `deploy/sbarbase.service:9-28`; `lab/dev.py:100, 156`;
`lab/install_server.py:45`.

The unit sets `Environment=HOME=/home/sbarbase` and nothing else. `dev.py` (the
`ExecStart` target) requires `bun` twice (`bun run build:ui` at line 156 and
`bun lab/upstream-server.ts` at line 100), and `ExecStartPre` runs
`install_server.py check`, which classifies `not shutil.which('bun')` as a
blocker (line 45). A system service inherits systemd's default `PATH`
(`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`), which does not
contain a user-local Bun. The documented host installs Bun under the user's home
(`~/.bun/bin/bun`), so on the machine the docs were written against the unit
fails preflight and never starts, and `install_server.install()` itself cannot
run `bun install` or `bun run build:ui`.

Minimal fix: add `Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/sbarbase/.bun/bin`
(or the deployment's actual Bun directory) to the unit, and state that path in
`docs/SERVER-DEPLOYMENT.md`'s prerequisites.

### 8. The documented evidence counts do not match the evidence files, and the evidence merge accumulates checks across code revisions

Evidence: `docs/TARGET-HBA-WRITERS.md:49-56`; `docs/evidence/target-hba-creation-checks.json`
(`count` 17, 17 entries); `docs/evidence/retained-source-adoption.json` (14
checks); `docs/evidence/retained-target-adoption.json` (15 checks);
`lab/adopt-retained.py:93-97`; `lab/verify-retained.py:101-104`.

The doc says "20 live checks" for the fresh-target file (it records 17) and "12
checks each" for the two adoption files (they record 15 and 14). The mismatch is
not only arithmetic: `verify-retained.py` currently emits exactly 12 check names,
and `adopt-retained.py` emits 6, with 3 overlapping, so a single target pass
produces 15. `retained-source-adoption.json` contains 14 because it still holds
two names the current script no longer emits
(`source still stopped by exact identity`, `inventory rules for every catalog
environment are present`). Both scripts merge into the existing JSON rather than
replacing it (`{**existing.get('checks',{}),**checks}`) and then recompute
`passed` as the AND over the merged set, so an evidence file can report `passed:
true` for a mixture of checks produced by different code revisions, including
names that no longer exist in the code. That directly weakens any claim sourced
from these files.

Minimal fix: correct the counts in `docs/TARGET-HBA-WRITERS.md`, and either
replace the check map instead of merging, or record the producing script's
version/hash alongside it and refuse to merge across revisions.

### 9. `install` takes no operation lock

Evidence: `lab/install_server.py:157-175`; contrast
`lab/installation_runtime.py:58-59` and `lab/recovery-restore-db.py:203-204`.

`install` creates `STATE` and `PRIVATE`, pulls images, runs `bun install`, builds
the console and starts the runtime without holding any lock. Every other
mutating entry point in the lab takes `operation.lock`. Two concurrent installs
(or an install concurrent with `dev.py`) both pass `preflight` and write
`.lab/upstream` and `.secrets/upstream` before any collision is detected; the
collision is then detected only indirectly, when the child's `hba_startup.acquire`
raises `BlockingIOError` out of `installation_runtime.py`, producing a raw
traceback in `install_server`'s captured stderr rather than a clean refusal.

Minimal fix: take `fcntl.flock` on `STATE/operation.lock` (or `supervisor.lock`)
for the duration of `install`, and report a clean message when it is held.

### 10. The fresh-target writer binds by container name, not by the created identity

Evidence: `lab/recovery-restore-db.py:130, 138`; `lab/hba_runtime.py:80-90`.

`docker run` at line 130 discards its stdout, and the container id passed to
`ready()` is re-derived at line 138 by inspecting the name again. `ready()` then
captures by name and only compares the captured id to that value, so the binding
proves "the container now called `<prefix>-db` is the one I just inspected",
not "the container I created". The same shape appears when `before_create` is
consumed by any future caller. With absence verified immediately before creation
(line 107-108) and the readiness loop requiring a live Supabase cluster, I could
not construct a reachable exploit, but the creation evidence the docs promise is
weaker than it reads.

Minimal fix: capture the id returned by `docker run` and pass that exact value as
`launched_cid`.

### 11. The bootstrap file's permissions and type are never checked

Evidence: `lab/install_server.py:177-181, 219`;
`docs/SERVER-DEPLOYMENT.md:34-35`.

The docs promise "a 0600 JSON object", but the code does `Path(bootstrap_file).read_text()`
with no `lstat` check for regular file, owner or mode, and the path is arbitrary
operator input. The secret is still handled correctly otherwise (piped on stdin,
never in argv: `install_server.py:179`; upstream output suppressed:
`bootstrap.ts:17-19`), so this is hygiene rather than exposure, but the documented
guarantee is not enforced.

Minimal fix: `lstat` the file and refuse unless it is a regular file owned by the
caller with mode `0o600`.

### 12. `prepare_target_state` can create the installation state root with umask permissions

Evidence: `lab/hba_runtime.py:118-132`.

`parent.mkdir(mode=0o700, parents=True, exist_ok=True)` at line 123 creates
`<installation state>` as well when it is missing, using the process umask (0755
under a normal umask), and the mode check at line 125-126 only covers
`<installation state>/targets`. Contents are 0600 files, so this exposes a
directory listing, not secret bytes, but the module's own contract ("Target
authority parent directory must be private") is not upheld for the root.

Minimal fix: require `<installation state>` to exist and be a private directory
(same `S_ISDIR` plus `0o700` check) before creating `targets`.

## Claims I could not falsify

- **`before_create` refuses a pre-existing volume and a pending journal.**
  `lab/hba_runtime.py:149-154` rejects a non-bool `preexisting_volume`, refuses
  `True` with "requires explicit adoption", and calls
  `absent(self.state/hba_journal.NAME)` before setting `fresh`. Covered by
  `lab/test_target_hba.py:76-100`. I found no path that bypasses it.
- **`prepare_target_state` rejects traversal and non-private directories.**
  `lab/hba_runtime.py:36-40` requires `sbarbase-restore-[a-f0-9]{12}` from
  `TARGET_PREFIX`, and lines 124-131 reject a world-readable state directory with
  `lstat` (so symlinks fail `S_ISDIR`). `test_target_hba.py:23-39` exercises both.
- **Two targets keep distinct locks and pins.** `test_target_hba.py:41-57` shows
  distinct state directories, distinct lock identities and that one target's
  generation pin is invisible to the other. My one qualification is finding 6.
- **No secret is printed or passed in argv.** `install_server.py:179` pipes the
  bootstrap payload on stdin (`install`'s `--bootstrap-file` argv entry is a path,
  not a secret); `bootstrap.py:29-46` reads stdin and passes it to the child on
  stdin with `pass_fds` only for the lock; `bootstrap.ts:17-19` deliberately
  suppresses upstream errors. The stderr echoed on failure
  (`install_server.py:175, 180`) comes from scripts that print fixed messages.
  Finding 11 is about enforcement of the file mode, not about leakage.
- **A listed-but-down endpoint fails `smoke`.** A missing base becomes the string
  `'missing endpoint'` and a non-2xx response becomes a stringified exception, so
  neither equals `200`. I also could not use the `all(...)` vacuous-truth on an
  empty check list, because `'management-auth'` is always present. The real
  problem is finding 2, not an always-true predicate.
- **Initialization under a mismatched generation or a changed container is
  refused.** `hba_generation.require` (line 57-61) and `hba_target.require`
  (line 43-49) both reject a changed identity or generation; the fresh path
  additionally requires `created == self.fresh` (line 83-84).
- **The installer cannot silently install over a running installation.** I tried
  the concurrent-supervisor case and it does fail, though late and with a raw
  traceback (finding 9), because `Runtime.start` refuses running recovery targets
  (`durable_runtime.py:178-179`) and the child's lock acquisition fails against a
  live `dev.py`. The residual gaps are per-target pending state
  (`targets/<prefix>/hba-operation.json`) and `recovery-target.json`, neither of
  which `install_server.state()` (lines 118-136) inspects.

## Documented claims assessed

- **"byte-for-byte rule preservation proven"** (`docs/TARGET-HBA-WRITERS.md:55-56`,
  `docs/RESUME-CHECKPOINT.md:355-361`, `docs/HERMES-HANDOFF.md:31-34`): **not
  overstated** for the two recorded adoptions, with two qualifications. The check
  at `lab/verify-retained.py:63-66, 83-84` compares the marker-stripped live bytes
  against the journal's `expected` digest, which is the digest of the pre-adoption
  file captured in `hba_adoption.execute` (`hba_adoption.py:287-290`), and the
  evidence files record `hba_preserved_digest == hba_before_digest` with exactly
  one revision marker for both roles, so the rules really are byte-identical. The
  qualifications are that the comparison would spuriously fail for any
  pre-adoption file that already carried a `# sbarbase-hba-revision:` line (it
  strips all such lines from the live file but not from `expected`), so the method
  is narrower than it reads; and the evidence files are merged across code
  revisions (finding 8).
- **"fresh-target writer proven end to end"** (`lab/target-hba-check.py:3`,
  `docs/TARGET-HBA-WRITERS.md:49-53`, `docs/RESUME-CHECKPOINT.md:386-387`):
  **overstated.** The probe runs the writer against a disposable container with a
  synthetic rule set. Its own check list contains a tautology
  (`check('replaying the same writer is refused',True)` at line 100) and a label
  that overstates what it tests ("no parser errors and reload acknowledged", line
  98-99, queries only `pg_hba_file_rules.error`). The consuming restore path has
  never been re-run with the owned writer, which `docs/TARGET-HBA-WRITERS.md:58-63`
  itself concedes, and finding 4 shows the failure branches of that path are not
  merely untested but malformed. "End to end" should be reserved for a restore
  that actually runs the writer.
- **"deployment path ready"** (`docs/SERVER-DEPLOYMENT.md:4-7`, "the deployment
  path is written and its preflight is proven"): **overstated.** `install` cannot
  complete its pinned-image step (finding 1); the shipped unit cannot find `bun`
  (finding 7); `smoke`, the documented post-install verification, exits 0 with the
  console down and is run before the console is ever started (finding 2); and
  `install` holds no lock (finding 9). The docs already admit no real-server
  rehearsal (lines 105-107), but "written and its preflight is proven" reads as
  more than "the preflight classifier has unit tests".
- **"per-target isolation"** (`docs/TARGET-HBA-WRITERS.md:6-15`): **not
  overstated for the authority state itself.** `target_state` cannot escape
  `<installation state>/targets/<prefix>`, and every lock, pin, journal, attempt,
  completion and outcome for a target is bound to that directory
  (`lab/hba_runtime.py:36-40, 135-140`), while the source is bound to the
  installation root (`lab/durable_runtime.py:97`). The exceptions are finding 6
  (target verification opens the source catalog) and finding 12 (the root can be
  created with umask permissions). I could not falsify the claim that a target
  writer cannot touch the source's authority files or vice versa.

## Summary

Twelve must-fix defects are established with file and line references. Two of
them (1 and 2) make the new deployment path non-functional or misleading on its
documented happy path, one (4) turns any mid-run restore failure into a permanent
dead end that the docs' own rule forbids clearing, and one (3) breaks the lock
discipline the protocol exists to provide. The four documented claims split
two and two: byte-for-byte rule preservation and per-target isolation survive
scrutiny with narrow qualifications, while "fresh-target writer proven end to
end" and "deployment path ready" do not.