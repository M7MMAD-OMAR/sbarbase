# Local component laboratory

This is an isolated compatibility experiment, not the Sbarbase product or a production deployment. It initially compares three environment databases on one PostgreSQL 17 instance with original Supabase Auth and PostgREST services. Storage, Realtime, functions, UI, recovery and upgrade gates remain separate work.

All containers use the `sbarbase-lab` ownership label, an internal bridge with no public port publishing, dedicated networking and a dedicated volume. Secrets stay in ignored `.secrets/`. Run with `/usr/bin/python3 lab/run.py up`, `status`, or `stop`. Stop preserves data. No existing services are managed.

Live probes: `/usr/bin/python3 lab/verify.py` and `bun lab/sdk-check.ts`. The SDK probe starts a transient loopback test router, not a production gateway. Test fixture `auth.uid()` is a minimal JSON-claim helper on stock PostgreSQL; full Supabase database bootstrap is a separate gate. Stop the lab between test sessions to preserve workstation headroom.

Recovery probes: `/usr/bin/python3 lab/retry-check.py` injects three phase failures into disposable databases. `/usr/bin/python3 lab/restore-check.py` streams a logical backup in memory to a temporary database and verifies source/neighbor preservation. Neither probe is a complete platform restore.
