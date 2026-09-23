# Separate-cluster restore gate

Read-only review of `lab/storage_restore_probe.py`, `lab/restore-check.py` and the existing recovery report. No separate-cluster restore has run yet. The existing payload contains a database dump, object snapshot and environment credentials; it restores on the source cluster and registers a new Storage tenant alias.

## Missing from the present proof

The source cluster already supplies roles, memberships and defaults. The current envelope does not independently specify all scoped role/database settings and CONNECT restrictions. It also does not establish restored Storage metadata encryption/signing state, Vault secrets or function artifacts. Preserving downloads with an Auth JWT is not proof that pre-backup signed URLs survive.

## Concrete next rehearsal

1. Capture only the allowlisted environment roles, memberships, database ACL/settings, required Auth/REST settings and Storage tenant/signing state. Encrypt secrets in the private artifact; never put them in evidence or the handoff ZIP.
2. Snapshot source and neighbor rows and object bytes/xattrs. Quiesce and stop owned source services before target startup; retain source volumes unchanged.
3. Start the same pinned Supabase PostgreSQL image on a separate owned network and fresh volume. Bootstrap system roles from the image, recreate scoped logins/memberships, then create an empty environment database. Do not precreate Auth schemas before restoring a complete dump.
4. Restore original ownership and ACLs. Apply and independently verify CONNECT, HBA, search_path, connection limits and SQL deadlines. Preserve the original environment identity on the isolated target.
5. Restore files before exposing target Auth/REST/Storage. Verify original identity, password login/refresh, RLS and private object bytes plus MIME/cache attributes. Reject neighbor tokens and cross-database service credentials.
6. Stop target before restarting source; recheck original source and neighbor snapshots. Check resource admission before either start.

Candidate target ceilings: PostgreSQL 1 GiB/1 CPU, Auth and REST 256 MiB/0.25 CPU each, Storage 512 MiB/0.5 CPU, totaling 2 GiB/2 CPU. These are a proposed test budget, not measured restore requirements. Staging avoids simultaneous source/target stacks within the earlier 4 GiB/4 CPU envelope.

Signed-URL continuity must be a separately verified gate. If signing state cannot be restored, record failure or defer it explicitly. This rehearsal would prove independent-cluster recovery on one workstation, not off-site availability, PITR or a production disaster-recovery service.
