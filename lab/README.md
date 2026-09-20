# Local component laboratory

This is an isolated compatibility experiment, not the Sbarbase product or a production deployment. It initially compares three environment databases on one PostgreSQL 17 instance with original Supabase Auth and PostgREST services. Storage, Realtime, functions, UI, recovery and upgrade gates remain separate work.

All containers use the `sbarbase-lab` ownership label, an internal bridge with no public port publishing, dedicated networking and a dedicated volume. Secrets stay in ignored `.secrets/`. Run with `/usr/bin/python3 lab/run.py up`, `status`, or `stop`. Stop preserves data. No existing services are managed.

Live probes: `/usr/bin/python3 lab/verify.py` and `bun lab/sdk-check.ts`. The SDK probe starts the initial environment-key gateway on loopback with new lab keys backed by persistent hashed metadata, revoked at probe completion. It is not a complete production gateway. Test fixture `auth.uid()` is a minimal JSON-claim helper on stock PostgreSQL; full Supabase database bootstrap is a separate gate. Stop the lab between test sessions to preserve workstation headroom.

Recovery probes: `/usr/bin/python3 lab/retry-check.py` injects three phase failures into disposable databases. `/usr/bin/python3 lab/restore-check.py` streams a logical backup in memory to a temporary database and verifies source/neighbor preservation. Neither probe is a complete platform restore.

Management probe: `bun lab/management-check.ts` uses a_stage as a temporary management Auth realm and a_prod as the application realm. It exercises the actual HTTP management handler and Supabase SDK getUser. This is test-only realm substitution, not a production management deployment.

Dynamic provisioning: `/usr/bin/python3 lab/worker.py` drains `.lab/control.sqlite` operations with an exclusive worker lock. `bun lab/provision-check.ts` tests lost-completion recovery. See [scope and limitations](../docs/PROVISIONING.md). Run `lab/verify.py` before the SDK probe to install test fixtures in all enrolled environments.
