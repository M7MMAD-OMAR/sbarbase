# Sbarbase

An open source, self-hosted administration project built on original Supabase services. Organize an installation into organizations, projects and environments, with a separate database and scoped service credentials for each environment.

**In development.** A local console, original Auth/REST/Storage integration and independent restore experiments exist. Production readiness, complete automatic recovery, multi-host coordination and fixed capacity guarantees are not established.

- [Website](https://base.sbarah.com) with an interactive architecture walkthrough, Arabic and English.
- [Project overview](PROJECT.md): model, implementation and limits.
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
