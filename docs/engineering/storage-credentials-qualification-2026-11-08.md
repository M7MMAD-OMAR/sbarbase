# Storage credential qualification working notes

Preserved on 2026-11-13 from local documentation work. The runtime issuer and the attended minting prototype have different publication states. In particular, `lab/storage_migration_credentials.py` is not included in the current public release. These notes do not establish deployment, restore acceptance or a supported command in a released checkout. Use the current [backup guide](../guides/backup-and-restore.md) and [published credential guide](../guides/fanio-storage-credentials.md) for operator-facing instructions.

## Installed-version qualification, 2026-11-08

A passive running-container inventory confirmed PostgreSQL 17.6 with image
`sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86`.
Newer-version local experiments do not prove this installation's recovery.
No database image upgrade or broader restore admission is part of this change.
The disposable native restore fixture now uses the actual pinned Auth/REST
images instead of invalid Storage-image placeholders, preserving production
restore guards. Its installed-version qualification remains a separate gate.
See [private Storage credentials](#private-storage-credentials) for credential custody.

## Private Storage Credentials

Implementation qualification, 2026-11-08. Not a declaration of production
activation or complete host recovery. This capability adds no Storage service
or upstream image and does not make a private bucket public.

### Runtime issuer

The existing Sbarbase gateway accepts server-only POST requests to
`/<environment>/storage-credentials/v1/originals` or `/<environment>/storage-credentials/v1/evidence`.
Use the actual `e_` plus 24 hexadecimal environment identifier in both routes.
There is no request body. Browser Origin/Sec-Fetch headers, query strings,
incorrect Host and unsupported methods are refused.

The Authorization header contains a separate `sb_storage_refresh_` credential
with 43 base64url characters. Signing material stays in existing environment
custody on the server. The response contains `accessToken`, `expiresAt`,
`refreshAfter`, `environment`, `purpose` and `generation`, never signing or
bootstrap material. JWT lifetime is 1 hour, with a refresh hint after 30 minutes.

Only two exact environment roles can be configured:

- `fanio_<environment-hex>_original_reader` for `originals`.
- `fanio_<environment-hex>_evidence_runtime` for `evidence`.

The JWT audience is `sbarbase:storage`. Gateway admission requires the existing
environment publishable API key separately from the Bearer JWT and refuses
this audience on other HTTP services. Storage still verifies the signature and
RLS; gateway parsing alone is not authentication. Realtime websocket token
messages are outside this capability. Review their upstream audience handling
before enabling Realtime for an environment using these credentials.

### Policy custody and rotation

The policy is `.secrets/upstream/storage-runtime-credentials.json`, version 1,
with a `credentials` array. Each record contains `environment`, `purpose`,
`role`, `generation`, `secretSha256`, `enabled`, `notBefore` and `expiresAt`.
Times are integer Unix seconds. Only the bootstrap SHA-256 is stored here.
A bootstrap hash cannot be reused across purposes or environments; rotation uses
a distinct credential. Actual bootstrap material belongs in the consuming application's server secret
store. Never put it in source, command arguments, logs or browser builds.

The file must be a regular, single-link file owned by the service's effective
UID with exactly mode 0600. The bounded single-descriptor reader refuses
symlinks, unsafe mode, replacement during read and malformed policy. The issuer
rereads policy, environment epoch/readiness and signing custody immediately
before publication; it has no stale policy TTL cache.

Rotate by creating a distinct generation/credential, atomically replacing the
0600 policy with both records, updating the consuming secret through its
existing versioned deployment flow, verifying the new generation, then removing
the old record. Do not delete the working generation first. Bootstrap validity
and its next rotation are operator responsibilities. Short-lived JWT refresh is
automatic in the Fanio server adapter; cache identity includes the deployment
environment, purpose, issuer and bootstrap generation material. Concurrent cold
reads share one bounded refresh. Revoked or expired bootstrap requests cannot
mint, while already issued JWTs retain their bounded lifetime unless underlying
role permissions or environment identity are revoked.

Authenticated admission is bounded to 600 requests per minute per environment,
purpose and generation, and 1200 per minute globally. Invalid public requests
do not spend authenticated refresh buckets. These are issuer limits, not a
claim of Storage download throughput or total user capacity.

### Attended migration credentials

`lab/storage_migration_credentials.py` is an operator command, not a public API.
It uses the existing environment signing custody and registration. Supply an
exact environment, purpose, approved manifest SHA-256, expiry and private output
path. The lifetime must exceed 60 seconds and cannot exceed 1 hour. Its
purpose-specific role is `e_<hex>_fanio_migration_originals` or
`e_<hex>_fanio_migration_evidence`, separate from runtime roles.

The command creates a new 0600 file inside an existing owned private directory.
It never overwrites an existing file or prints a JWT. Its stdout contains only
nonsecret scope/expiry metadata. Provision the reviewed scoped role separately;
minting does not grant permissions. Revoke migration-role membership after
reconciliation and keep the application on its runtime credentials. No
service-role token is a substitute for this capability.

Run `python3 lab/storage_migration_credentials.py --help` for arguments. Keep
secret inputs in existing custody and the generated file out of Git.

### Qualification and recovery boundaries

Local source-bound qualification covered the pinned native Storage image,
actual custom-role JWT acceptance, PUBLIC-wide policy negatives, separate
migration roles, private mint output and 100 concurrent HTTP issuer requests.
It did not test production website traffic or complete host restoration.

The running server remains on PostgreSQL 17.6. Restore qualification must use
that exact image and the pinned Storage image; a successful newer local fixture
cannot substitute for it. Existing worker, preload, session and extension-default
checks remain unchanged. The fixture-only Auth/REST image correction does not
weaken production admission or upgrade a service. Full-host/offsite recovery,
hourly RPO and complete application recovery remain separate acceptance gates.

See [backup and restore](../guides/backup-and-restore.md) for those broader requirements.
