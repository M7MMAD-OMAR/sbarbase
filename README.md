# Sbarbase

An open source, self-hosted administration project built on original Supabase services. Organize an installation into organizations, projects and environments, with a separate database and scoped service credentials for each environment.

**In development. Current source release: [0.1.0](CHANGELOG.md).** Production readiness, complete automatic recovery, multi-host coordination and fixed capacity guarantees are not established.

## What exists today

Everything below is verified on a development workstation, not on a production server.

- **Platform console** for organizations, projects, environments, connection details, scoped keys and provisioning status. Each environment is meant to be administered through the original upstream Supabase Studio, one per environment; that is specified and not served yet ([integration specification](docs/STUDIO-INTEGRATION.md)).
- **Original Supabase services**: shared PostgreSQL with a separate database and scoped logins per environment, original Auth and PostgREST per environment, one shared tenant-aware Storage process, behind an API-key gateway with bounded admission.
- **Resource separation**: every container runs under a policy tier with CPU weight and per-device block IO limits; admission checks memory, disk, cgroup pressure and connection budgets before allocating ([resource policy](docs/RESOURCE-POLICY.md)). The tiers are not yet calibrated under load.
- **Per-environment mail** through original Auth with an operator-supplied SMTP relay, and **operator notifications** by email or signed webhook ([mail](docs/ENVIRONMENT-EMAIL.md), [notifications](docs/OPERATOR-NOTIFICATIONS.md)).
- **Crash-oriented provisioning**: durable effect receipts, fenced SQL and database access rules, and a container generation migration crash-tested at five points on disposable fixtures ([migration](docs/CONTAINER-GENERATION-MIGRATION.md)).
- **Recovery experiments**: encrypted export and restore of one environment to an independent engine on the same host ([independent restore](docs/INDEPENDENT-RESTORE.md)).
- **Server installation path**: preflight, installer and systemd supervision, rehearsed on a workstation and not yet on a real server ([runbook](docs/SERVER-DEPLOYMENT.md), [readiness](docs/DEPLOYMENT-READINESS.md)).

- [Website](https://base.sbarah.com) with an interactive architecture walkthrough, Arabic and English.
- [Start here](docs/START-HERE.md): one page on the decision, evidence and current state.
- [Project overview](PROJECT.md): model, implementation and limits.
- [Changelog](CHANGELOG.md): what each release adds and what it does not.
- [Current handoff](docs/HANDOFF.md): decisions, verified experiments and remaining work.
- [Decision register](docs/DECISIONS.md): why this design, alternatives and reconsideration gates.
- [Local laboratory](lab/README.md): prerequisites and bounded experiments. Read before running anything.
- [Website development and deployment](website/README.md).

## Why

Repeated full stacks are the baseline for self-hosting multiple small projects. Sbarbase explores a shared foundation while retaining environment boundaries and original service compatibility. Shared PostgreSQL and Storage remain shared failure boundaries. Operators are trusted. Actual resource savings require measurement.

## Contributing

Reproducible isolation, recovery and upgrade tests are especially useful. Read the existing evidence and limitations before making claims. Keep credentials, runtime data, private backups and local state out of contributions. A reported experiment is not production certification.

## License

Sbarbase source is licensed under Apache-2.0. Supabase, PostgreSQL and other dependencies retain their own licenses. Bundled website fonts include their SIL Open Font License notices.
