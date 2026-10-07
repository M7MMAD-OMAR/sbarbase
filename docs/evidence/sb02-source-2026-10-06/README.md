# SB-02 source admission evidence

This ledger accepts only the SOURCE preparation checkpoint. Supported-host acceptance, actual resource and filesystem behavior, clean installation, reboot and complete application recovery remain unproven. The delta commit is the commit that first introduces this ledger, separate from imported baseline `d7cbaca764eec368a0f89c78b11b38470580f317`.

The current source gate executed 54 owned host/entrypoint fixtures and 211 focused caller tests, with no failures or skips. Fixture Docker and service effects are inert doubles. Python is the development verifier; it is not a primary host installation dependency. Public host prerequisites and complete declared inputs are specified in `docs/engineering/HOST-PREFLIGHT.md`.

The source manifest binds every checkpoint file by bytes and mode. `source.sha256` can be checked from the repository root with `sha256sum -c docs/evidence/sb02-source-2026-10-06/source.sha256`. The workflow source hash also includes original project filesystem identity, so transplanting the source to another checkout requires fresh workflow evidence rather than reusing its accepted projection.

The retained workflow journal and artifacts preserve genuine failing checks and independent failing reviews before repair. Initial caller isolation omitted the new capability bridge stub and reached its read-only daemon projection before failing; no Docker mutation occurred, and the owned fixtures were corrected. Later real source findings included multiline scalar acceptance, raw Compose documentation routing, invalid daemon identity/OS evidence and missing native service/mirror effect boundaries. Earlier artifacts retain their own source identity and verdict; they are not current acceptance evidence.

Final fresh independent specification and standards reports are included with their source/plan bindings. The standards report separately assesses the proposed bounded public-image observation contract. That contract has no runtime execution authority and no host acceptance result. Its final source commit, public snapshot, built image identity and exact owned CID must be bound before an explicitly coordinated runtime role.

Replay current development checks:

```sh
/usr/bin/python3.14 -u -m unittest discover -s lab -p 'test_host*.py' -v
/usr/bin/python3.14 -u lab/host_caller_checks.py
sh -n deploy/host-preflight.sh deploy/compose.sh
bash -n deploy/server-acceptance.sh
```

SB-01 capability registry rebinding is an integration obligation owned by the coordinating task. This source delta does not promote the profile, replace the broader PROJECT_GOAL or portability roadmap, accept SB-03, or prove native Supabase feature/recovery behavior.
