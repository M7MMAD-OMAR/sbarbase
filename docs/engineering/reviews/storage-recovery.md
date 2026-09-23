# Database and object recovery rehearsal

Recorded 2026-09-20. Eleven additional live checks passed inside the upstream Storage probe; the combined run now totals 122, including previous checks. [Evidence](../../evidence/storage-recovery-checks.json). This is a same-cluster, quiescent-fixture rehearsal, not a production backup service or off-host recovery guarantee.

## Backup contents and integrity

The probe captures a PostgreSQL custom-format logical dump, Storage file bytes and supported user.supabase filesystem attributes, plus the source environment's service credentials/JWT secret. It encrypts the payload using AES-256-GCM with a random 256-bit key and 96-bit nonce. The authenticated manifest records format version, payload checksums and pinned image identities. Corrupting the ciphertext is rejected before any restore operation.

Plaintext dumps, object snapshots and credentials stay in process memory. The ignored local ciphertext artifact is `.lab/backup-probe.encrypted.json`; its key is separately written mode 0600 under ignored `.secrets/backup-probe.key`. Both remain on the same machine. They are excluded from the handoff archive and do not provide off-site protection. The probe uses the workstation's Python cryptography 50.0.0 installation; packaging this dependency remains installer work.

## Restore and verification

The dump is restored into a fresh temporary database. The original environment's role ownership is retained because this is a recovery copy of the same identity, not a new independent project. Its Storage role gets an explicit HBA/CONNECT allowance to the recovery database. Files are restored under a separate internal tenant namespace. Original user identity can download the recovered private object; neighbor identity cannot.

Hashes compare all table rows in auth, storage and public before API use, and compare file bytes plus supported extended attributes. Source and neighbor databases and files are checked for unchanged content afterward. The checksum scope does not independently verify every sequence, grant, function or extension state. The logical dump carries those objects, but complete schema/ACL recovery certification remains work.

File-backend MIME type and cache-control live in filesystem extended attributes. The first restore attempt correctly rejected a copied security.selinux attribute. Snapshot collection was restricted to the known Storage user attributes: cache-control, content-type and etag. OS security labels are assigned by the destination, not imported from the source. Restore rejects absolute/traversal paths, unexpected attributes and an already existing destination. File modification times are not preserved by this probe.

## Remaining production gates

- Enforce and test a write fence, drain uploads and coordinate database/object snapshots under real concurrent workloads. This test relies on a quiescent fixture.
- Include the required tenant configuration and URL-signing JWK state from the shared Storage metadata database. The current test registers a fresh recovery alias; it does not prove preservation of old signed URLs.
- Restore on another host/cluster with role reconciliation, Vault keys, functions, jobs and complete settings. Test recovery from loss of the entire original installation.
- Add durable recovery phases, retry/rollback, write-disabled staging, route cutover, key recovery/rotation, retention, scheduled verification and explicit RPO/RTO targets.
- Upload encrypted artifacts to independent storage and rehearse fetching them without local originals. No paid or external storage was used here.

Database-only backups cannot restore object bytes. This aligns with [Supabase backup scope](https://supabase.com/docs/guides/platform/backups), but all success claims above come from this local rehearsal.
