[العربية](README.ar.md)

# Sbarbase

**Many Supabase projects on one server that you can back up, restore and upgrade without fear.**

Sbarbase is an open source, self-hosted administration layer that runs original, unmodified Supabase services (PostgreSQL, Auth, PostgREST, Storage) for several projects on one server. You organize the server into clients, each client into projects, and each project into environments such as production and staging. The environment is the unit that is isolated, moved and restored.

**In development. Current source release: [0.1.0](CHANGELOG.md).** It is not production ready, and it does not promise a fixed number of projects per server.

## Who it is for

A developer or a small agency that runs Supabase apps for several clients and wants them on one server they control, without running a full, separate Supabase stack for every app and without giving up the upstream Supabase SDKs and SQL.

## What works today, and what does not

Verified on a development workstation, not on a production server:

- A platform console and management API for clients, projects, environments, connection details, scoped publishable keys and provisioning status.
- Original Supabase services per environment: a separate database and scoped logins on a shared PostgreSQL, original Auth and PostgREST per environment, one shared tenant-aware Storage, behind an API-key gateway with bounded admission.
- Crash-oriented provisioning that refuses to replay work whose outcome is unknown.
- Encrypted export of one environment and its restore to a separate database engine on the same host.

Not built yet: per-environment Supabase Studio (specified, not served), Realtime, Edge Functions, the connection pooler, cron, scheduled or off-host backups, automatic upgrades, multi-server placement and measured capacity. The full list, with every number and the evidence behind it, is in [status](docs/reference/status.md).

## Try it

An install from an empty server has passed in a local virtual machine (Fedora 44, 4 cores, 6 GB; plan on 4 cores and 8 GB for a real one): one command installs the service, creates a first project and proves supabase-js works through it, and the service survives a reboot. It **has not been run on a real server yet**.

- [Quickstart](docs/guides/quickstart.md): from an empty server to a supabase-js call, the steps the VM ran.
- [Choosing a server](docs/guides/choosing-a-server.md): what to buy, what the free options really give you.
- [Local lab](docs/guides/local-lab.md): run the stack on your own machine for evaluation. Read its safety notes first.
- [Server deployment](docs/guides/server-deployment.md): the runbook as it stands, with its known gaps.
- [Operator setup](docs/guides/operator-setup.md): create the first operator account and client.

## Documentation

- [Docs map](docs/README.md): where everything is.
- [Explanations](docs/README.md#explain-why-it-works-this-way): why Sbarbase exists and how it is built.
- [Guides](docs/README.md#guides-how-to-do-something): step by step tasks.
- [Reference](docs/README.md#reference-facts-to-look-up): glossary, status, configuration and API.
- [Decisions](docs/decisions/README.md): what was chosen, what was rejected and when to reconsider.
- [Security policy](SECURITY.md): supported versions, how to report a vulnerability privately and an operator hardening checklist.
- [Changelog](CHANGELOG.md) and [website](https://base.sbarah.com).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and the [roadmap](docs/engineering/plans/2026-09-23-roadmap.md) for what comes next. Reproducible isolation, recovery and upgrade tests are the most useful contributions. Read [status](docs/reference/status.md) and the [engineering notes](docs/engineering/README.md) before making claims. Keep credentials, runtime data, private backups and local state out of contributions. A reported experiment is not production certification.

## License

Sbarbase source is licensed under Apache-2.0. Supabase, PostgreSQL and other dependencies keep their own licenses. Bundled website fonts include their SIL Open Font License notices.
