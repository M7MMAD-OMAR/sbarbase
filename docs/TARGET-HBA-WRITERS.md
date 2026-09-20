# Recovery-target and remaining HBA writers

Inventory and ownership rules for every place that writes `pg_hba.conf`.
Implementation checkpoint 2026-09-20.

## Authority state per database container

The source database uses the installation state root (`lab/durable_runtime.STATE`).
Every recovery-target database uses its own private authority state below that
root: `<installation state>/targets/<prefix>/`, with its own worker, effect and
operation locks, generation pin, journals, attempts, completions and outcomes.
`lab/hba_runtime.target_state` validates the prefix and
`prepare_target_state` creates the directory 0700; a world-readable or
traversing state is refused. `hba_runtime.TargetHBA` is the same owned protocol
as `SourceHBA`, pointed at that directory, and it adds `before_create`:

- a first-generation target must pass explicit creation evidence
  (`preexisting_volume=False`, true only when the caller verified absence
  immediately before creating the resources),
- a preexisting volume is retained state and requires explicit adoption of the
  existing container instead,
- a pending journal refuses preparation.

## Writer inventory

| Path | Kind | Status |
|---|---|---|
| `lab/durable_runtime.py` (`Runtime.hba`, `reload_hba`) | installation database, startup and services stage | owned (`SourceHBA`), integrated |
| `lab/recovery-restore-db.py` | fresh recovery-target database | owned (`TargetHBA.before_create` then `publish`), integrated 2026-09-20 |
| `lab/adopt-retained.py` | existing retained database without a pin (source or target) | owned (`hba_adoption`), integrated; both retained databases adopted |
| `lab/recovery-check-storage.py` | storage verification probe on the restored target | raw write, remains: it is a check-time probe that needs extra rules for its own run, not a production publication. Convert to a fresh owned writer instance before claiming an installation-wide all-writers guarantee. |
| `lab/run.py`, `lab/provision.py` | legacy local bootstrap scripts writing `$PGDATA/pg_hba.conf` | raw write, remains: legacy lab bootstrap path, not used by the installation runtime. Retire or convert with the installer work. |
| `lab/storage_probe.py`, `lab/storage_restore_probe.py`, `lab/upstream-environments.py`, `lab/export-fence-check.py`, `lab/partial-database-crash-check.py` | disposable fixtures and probes | raw by design inside throwaway containers; never used against retained resources |

## Rules

- A managed database container gets exactly one authority state, one generation
  pin and one publication per writer instance.
- Adoption is for existing retained containers; initialization is only for a
  container this run created, with verified absence immediately beforehand.
- Raw writes are acceptable only inside disposable fixtures, never against a
  retained database.
- Removing this file's inventory entries requires the corresponding path to be
  converted or deleted; `lab/test_recovery_owned_hba.py` fails if a listed path
  disappears from the table or if the restore path regains a raw write.

## Evidence

- `docs/evidence/target-hba-creation-checks.json`: 16 live checks on a
  disposable pinned PostgreSQL container for the fresh-target writer path
  (creation evidence, per-target state, one-shot generation, owned publication,
  a second publication refused, exact cleanup). The reload acknowledgment is
  asserted inside the owned publication, not by a separate probe check.
- `docs/evidence/retained-target-adoption.json` and
  `docs/evidence/retained-source-adoption.json`: an operation section (7 checks)
  and a verification section (12 checks) per role, replaced wholesale on every
  run and stamped with the producing script digest. Byte-for-byte rule
  preservation is proven from the journal expected digest, comparing the live
  file with exactly the first published revision marker removed.

## Explicit limits

The restore path's owned publication is wired and unit-guarded, but a full live
recovery restore executed end to end with the owned writer has not been re-run
since the change; run `lab/recovery-restore-db.py` against a fresh disposable
export before claiming that. The fresh-target writer evidence above proves the
writer on a disposable container, not the consuming restore path. Service-driven migrations and container-generation
migration remain separate gates.