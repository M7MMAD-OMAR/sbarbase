# Security policy

Sbarbase runs many Supabase projects on one server that you can back up, restore and upgrade without fear. It is in development and not production ready. This page says which versions get security fixes, how to report a problem privately, what counts as a vulnerability here and how an operator should harden a server today. The reasoning behind it is in the [threat model](docs/explain/threat-model.md).

## Supported versions

| Version | Status | Security fixes |
| --- | --- | --- |
| 0.1.x | In development | Fixed on `main` on a best effort basis; no backports, no production support |
| Earlier | Not released | None |

There is no production support at any version. A fix lands on `main` and in the next 0.1.x release entry in the [changelog](CHANGELOG.md).

## Reporting a vulnerability

Report privately through GitHub: open the [security advisories page of github.com/M7MMAD-OMAR/sbarbase](https://github.com/M7MMAD-OMAR/sbarbase/security/advisories/new) and use **Report a vulnerability**. The report stays visible only to you and the maintainers until an advisory is published.

If that button is not offered, open a public issue that says only that you have a security report and asks for a private channel. Do not put details, proof of concept code, logs or credentials in a public issue, pull request or discussion.

A useful report names:

- the commit or release you tested;
- the boundary you crossed (see scope below) and what an attacker needs to start;
- the steps to reproduce, ideally against the local lab ([local lab guide](docs/guides/local-lab.md));
- what you expected and what happened.

Never include real application data, real credentials or another person's data. Test only against an installation you own.

## What to expect

Sbarbase is maintained by a small team, so these are intentions, not a service level:

- an acknowledgement, usually within a week;
- an assessment of whether the report is in scope and how severe it is;
- a fix on `main` when one is feasible, with credit in the advisory unless you ask otherwise;
- coordinated disclosure: please allow up to 90 days before publishing, or tell us if you need a different timeline.

## Scope

Sbarbase trusts the people who run the server and treats the visitors of the hosted apps as untrusted. A report is in scope when it lets someone cross a boundary that the design says should hold.

**In scope**

- **Cross-environment access.** Anything that lets a token, key, login or request for one environment read or change data, files or configuration of another environment.
- **Key leakage.** Raw publishable keys, service passwords, Auth signing secrets or Storage signing keys disclosed through the management API, the gateway, key listings, error messages or files that are not private.
- **Gateway bypass.** Reaching Auth, PostgREST or Storage for an environment without a valid key for that environment, choosing the upstream host or Storage tenant from client input, or getting past the body, deadline or admission limits in a way that affects other environments.
- **Privilege escalation from an environment's service login.** An environment's Auth, REST or Storage login connecting to another database, assuming a cluster administrator role, or changing the connection rules.
- **Management identity confusion.** An application user's token being accepted by the management API, or a management actor being taken from a header or body instead of from authentication.
- **Secret exposure in logs or evidence.** Credentials, tokens or private paths written to logs, command output, notifications or the versioned files under `docs/evidence/`.
- **The reference TLS proxy** (`deploy/console-tls-proxy.ts`): open redirects, host header injection, or logging of bodies, query strings, cookies or credentials.

**Out of scope**

- **A malicious host operator.** Anyone with root, Docker access, the PostgreSQL administrator login or the files under `.secrets/` and `.lab/` can read and change everything by design. Operators can also run arbitrary SQL in any environment by design.
- Components that are not built yet: Realtime, Edge Functions, the connection pooler, cron and per-environment Studio.
- Vulnerabilities in upstream Supabase services, PostgreSQL or other dependencies, unless Sbarbase's configuration of them creates the problem. Report those upstream.
- Resource exhaustion that needs trusted operator access, or that only affects the environment the attacker already controls.
- Findings that depend on ignoring the hardening checklist below, such as a database port published to the internet.
- Automated scanner output without a demonstrated impact.

The [known gaps](docs/explain/threat-model.md#known-gaps) in the threat model are already public. A report that shows one of them is worse than described, or offers a fix, is still welcome.

## Hardening checklist for operators

These steps reduce risk; they do not make an installation fully secure. The runbook is the [server deployment guide](docs/guides/server-deployment.md).

- **Firewall.** Allow inbound HTTPS on 443 (and 80 only if you keep the redirect to HTTPS), plus your administration SSH path. Block everything else. The installer publishes no Docker ports; keep it that way. The reference proxy listens on 8443 and 8080 by default, so either pass `--https-port 443 --http-port 80` (binding ports below 1024 needs root or `CAP_NET_BIND_SERVICE`) or map those ports in the firewall.
- **Console behind TLS.** The console, management API and gateway bind loopback. Reach them only through a TLS terminating proxy, either `deploy/console-tls-proxy.ts` or your own with the same behaviour. Never expose the management Auth endpoint or the provisioning API directly.
- **Private secrets.** Keep `.secrets/`, `.lab/` and the certificate key readable only by the service account (mode `0600` files in a private directory). The reference proxy refuses a group or world readable key. Never paste secrets into issues, logs or evidence files.
- **Treat the service account as root.** `deploy/sbarbase.service` runs as a dedicated `sbarbase` user with Docker access, which is root equivalent on the host. Do not log in as it or share it with other software.
- **Encrypted, off host backups.** Sbarbase does not copy backups off the host. Arrange encrypted copies on another machine yourself, keep the decryption material somewhere that survives the loss of the server, and rehearse a restore. See [backup and restore](docs/guides/backup-and-restore.md).
- **Pinned images.** Run only the image digests in `lab/images.lock.json`, `lab/storage-image.lock.json` and `lab/distro-image.lock.json`. The startup preflight refuses a missing or mismatched pin; do not work around it.
- **Updates.** Change one upstream component at a time as described in [upgrades](docs/guides/upgrades.md) and the [upstream update policy](docs/engineering/UPSTREAM-UPDATE-POLICY.md). Record the rollback pin first, read the upstream changelog for security fixes, and run the full test suites afterwards. Apply operating system and Docker security updates on their own schedule.
