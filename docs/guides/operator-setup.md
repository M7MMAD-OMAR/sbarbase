[العربية](operator-setup.ar.md)

# Operator setup

Create the first operator account and the first client (organization) on your
server. Verified 2026-09-20 on the development workstation. This is local
experimental onboarding. A server deployment
runbook is in [SERVER-DEPLOYMENT](server-deployment.md). The installer is
`lab/install_server.py` (`check`, `plan`, `install`, `smoke`).

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

Missing: supported repair/reset flows, invitations, guided first-run onboarding, MFA, login
rate controls, HTTPS deployment, pagination for very large organization lists,
remote backups and a complete distributable installer. Bootstrap identity lookup
paginates the private Auth API and stops with an error after 10,000 users rather
than silently assuming no match. Store the original password securely yourself.

Sources: [Supabase admin createUser](https://supabase.com/docs/reference/javascript/auth-admin-createuser),
[admin listUsers](https://supabase.com/docs/reference/javascript/auth-admin-listusers),
and the original pinned Auth service exercised locally.
