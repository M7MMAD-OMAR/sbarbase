[العربية](operator-setup.ar.md)

# Operator setup

Create the first operator account and the first client (organization) on your
server. Verified 2026-09-20 on the development workstation. This is local
experimental onboarding. On a server, follow the [quickstart](quickstart.md)
or [Install with Docker](docker.md) (`deploy/compose.sh bootstrap`) instead; the
runbook is in [SERVER-DEPLOYMENT](server-deployment.md). The installer is
`lab/install_server.py` (`check`, `plan`, `install`, `images`, `smoke`,
`supervise`, `wait-console`).

## Current compatibility gate

Fresh installations and retained installations whose source and current recovery
target carry a matching HBA generation pin are supported. Retained legacy
databases without that pin require explicit adoption
(`lab/adopt-retained.py source|target`), and their startup refuses before
credential writes or service startup. Both retained databases in this checkout
are adopted as of 2026-09-20. Do not recreate their containers or remove state to
bypass the gate. See [source HBA integration](../engineering/SOURCE-HBA-INTEGRATION.md) and
[recovery-target writers](../engineering/TARGET-HBA-WRITERS.md).

## Run

1. Start the owned upstream runtime: `/usr/bin/python3 lab/durable_runtime.py up`.
2. In your terminal run `/usr/bin/python3 lab/bootstrap.py`. Enter an email,
   organization name and a password of at least 12 characters. Password entry
   uses no echo and asks for confirmation. Do not put credentials in arguments.
3. Run `bun lab/upstream-server.ts` to start the loopback API. The management
   Supabase SDK endpoint is its printed base URL plus `/management`; the public
   routing key is `sb_publishable_sbarbase_local_management`. Open the printed base URL for the login UI. Run `bun run build:ui` before starting the server.
4. After login, `GET /management/v1/organizations` lists the authenticated user's
   current memberships, including organization ID, name and role. Existing
   project/environment APIs then operate within that organization.

For automation, `bootstrap.py --stdin` reads at most 8192 bytes of JSON containing
exactly `email`, `password` and `organization`. Supply it through a private stdin
channel, not an inline shell command containing credentials. The command outputs
only completion state and the resulting identity/organization IDs.

The setup command is for the trusted host operator who already has access to
installation secrets. It is not an HTTP endpoint. Auth public signup remains
disabled, and POST to organization discovery is not a bootstrap operation.
Creating the initial organization owner does not confer cross-organization
access or expose the private Auth administrator API.

## Interruption and retries

A private, password-free intent journal is atomically written and fsynced before
creating an Auth user. It records a random operation ID, email and organization,
then the confirmed identity ID. An admin-controlled `app_metadata` operation
marker locates the same Auth user if setup stopped before its ID was journaled.
User-editable metadata is never used to claim this identity.

Retry with the original email, organization and password. The existing password
is verified against management Auth; it is never silently reset. Setup uses a
single catalog transaction for organization, owner membership, bootstrap record
and audit entry. Repeating a completed operation returns the same IDs. It cannot
create duplicate organizations or restore ownership that was later revoked.

The wrapper holds an OS file lock throughout the child operation. Concurrent
setup attempts fail rather than race. The catalog also enforces a single initial
setup record. If the journal is missing after initialization, setup refuses a
new operation. If a recorded Auth identity disappears, setup refuses replacement.
Manual repair for those cases is a separate operator recovery procedure, pending.

No Auth password or JWT is stored in this journal. Its file mode is 0600 under
ignored `.secrets/upstream`. The Auth service stores the password using its own
normal password handling. The setup command does not email an invitation; its
private admin creation explicitly confirms the operator-supplied email.

## Evidence and limits

[18 live checks](../evidence/bootstrap-checks.json) verify real management Auth user
creation, interruption after Auth creation, interruption after catalog commit,
wrong-password refusal, identity/organization reuse, journal privacy and SDK
login. They also prove organization discovery, first project creation, denial of
unauthenticated discovery and denial of access to an unrelated organization.

The live probe uses the same bootstrap core, Auth adapter and file journal as
the CLI, with an isolated temporary catalog. The wrapper itself was checked for
malformed input, weak/invalid input, oversized input and concurrent-lock refusal.
Interactive password entry was not automated. No actual owner account was
retained or delivered; the probe removed its generated identity and private files.

Unit tests cover four durable interruption boundaries, password verification
before authority, revoked ownership and membership-scoped discovery. Current suite
totals are recorded in [status](../reference/status.md); they do not establish
production readiness.

Since this page was first written, these have shipped: invitations,
sign-in factors (MFA) and login rate limits for the console, HTTPS through the
reference TLS proxy ([server deployment](server-deployment.md)), encrypted off-site
backups ([backup and restore](backup-and-restore.md)) and a password reset from the
server's shell (below). Still missing: a supported repair for the two refusals
above (a missing journal, a vanished identity), guided first-run onboarding in the
console, pagination for very large organization lists and a complete distributable
installer. Bootstrap identity lookup
paginates the private Auth API and stops with an error after 10,000 users rather
than silently assuming no match. Store the original password securely yourself.

## A forgotten password

The console has no "forgot password" link: the management sign-in sends no mail by itself. Whoever has a shell on the server sets a new one:

```bash
sudo -u sbarbase /opt/sbarbase/deploy/sbarbase reset-password you@example.com
```

It asks for the new password twice without echo (at least 12 characters), replaces it through the management sign-in service, and ends every console and Studio session the account had, so a session opened with the old password stops at once. An authenticator the account uses stays as it is. The change is in the audit log as `management.password_reset`. It reads the management realm's secret from `.secrets/upstream/runtime.json`, which is why it runs only on the server, as the service account.

Sources: [Supabase admin createUser](https://supabase.com/docs/reference/javascript/auth-admin-createuser),
[admin listUsers](https://supabase.com/docs/reference/javascript/auth-admin-listusers),
and the original pinned Auth service exercised locally.

## Recover the only owner's authenticator from the host

`lab/management-sole-owner-recovery.ts` is a separate root-only B5 host command for an installation whose bootstrap organization currently has exactly one owner. The ordinary `management-factor-recovery.ts` command still requires a different owner with verified MFA. Host root is trusted and can change installed code; this command is not a sandbox against root.

Deploy the controller with `durable-latch-v1` support first. Recovery reads the installed `sbarbase.service` working directory and original Auth process binding, and asks the running controller's authenticated policy endpoint to advertise that capability. A newer checkout beside an older running controller is refused.

Run as root from the installed checkout, with private JSON on stdin, never command-line credentials:

```sh
bun lab/management-sole-owner-recovery.ts < /root/sole-owner-request.json
```

The root-private input has exactly `target`, `factor`, `targetPassword` and `reason` (`lost_factor` or `compromised_factor`). `target` is the current sole owner's Auth UUID; `factor` is the exact lost verified TOTP factor UUID. Add `resumeReceipt` to resume a failed or interrupted attempt. There are no realm, uid, authorization or output-path arguments.

Recovery checks the native password against the exact target, durably blocks both password and MFA management grants, revokes personal tokens and native sessions, removes the admitted old factor, then enrolls and verifies a new native TOTP factor. Global optional-MFA policy stays unchanged. The command rereads the native identity and factors, then arms the receipt for that replacement only. The existing proxy completes the receipt and publishes its grant in one transaction. Final management access must pass the existing proxy's native MFA verification, using a later TOTP timestep to avoid replay.

The command prints only receipt and factor IDs. Root-owned files under `/var/lib/sbarbase/sole-owner-recovery` use mode `0600` inside a `0700` directory: `<receipt>.request.json` records the admitted request without the password, `<receipt>.setup.json` preserves the new native setup secret and URI, and `<receipt>.session.json` holds the genuinely proxy-verified session for a private host task. Import the new URI into the owner's authenticator through a private channel. Keep setup material away from the service account. Consume the session privately, sign it out and delete its file after the authorized host task. Never put these files in logs, tickets or terminal output.

A failure or interruption leaves the durable denial active; use the same receipt and unchanged request fields to resume. A receipt with a published session cannot be replayed. If native enrollment succeeded but its response was lost before the setup file reached disk, Auth cannot return that seed again: recovery preserves the factor and denial, and refuses automatic reenrollment or deletion. Inspect that case as a host recovery incident. Source tests model root admission and native calls; they do not establish real-root native integration on an installed host.
