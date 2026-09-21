# Existing alternatives for sbarbase

Research date: 2026-09-20. Scope: downloadable open-source software that lets an operator run multiple projects on their own server. PostgreSQL and the Supabase foundation are required. Replacement backends are excluded from selection. This is a focused evidence review, not a runtime security certification or a complete market census.

## Recommendation

Evaluate existing multi-project implementations before building a new control plane. Two repositories already implement much of the proposed topology: `GustavoMartins123/supabase-multitenant` and `arunrajiah/supafleet`. Neither should be adopted solely on its README. The latter has a concrete credential-sharing concern in its CLI provisioner. Retain ordinary Supabase stacks as the compatibility and isolation baseline. Evaluate Pigsty separately as an operational foundation for PostgreSQL. Coolify and Dokploy solve deployment management, but their standard Supabase templates do not establish the proposed shared database architecture.

These are three separate decisions: reuse a deployment manager, reuse a PostgreSQL operating layer, and reuse or implement a multi-project Supabase control plane. Installing all three by default would create overlapping responsibilities.

## Baseline and compatibility definition

Official self-hosted Supabase is a single-project deployment. Its Studio does not provide multiple organizations or projects. This is the reference baseline, not evidence that multiple deployments require separate physical servers. Because each environment's administration is now that same single-project Studio, the constraint is used rather than worked around: every environment gets its own Studio instance. [Official self-hosting documentation](https://supabase.com/docs/guides/self-hosting)

Compatibility means passing a versioned application test suite for the actual features used: SQL/RPC, Auth sessions and refresh, Storage, Realtime and Functions. Running upstream services makes compatibility plausible, but does not prove correct routing, authentication, migration behavior or support for every SDK operation. A replacement with its own SDK is not a drop-in Supabase implementation.

## Focused shortlist

| Candidate | PostgreSQL and project model | Compatibility category | Fit for sbarbase |
| --- | --- | --- | --- |
| Supabase through Coolify | Compose service per deployment, containing its database and supporting containers | Upstream stack deployment, test selected template versions | Reuse deployment and server management; does not itself remove repeated project stacks |
| Supabase through Dokploy | Supabase template with its own db container and generated secrets | Upstream stack deployment; official template explicitly documents supabase-js | Same role as Coolify; compare one of these managers rather than require both |
| Pigsty Supabase integration | Supabase application services with PostgreSQL operations supplied by Pigsty | Community integration of upstream Supabase components | Candidate database operational foundation, not established multi-project product UI |
| GustavoMartins123/supabase-multitenant | Shared PostgreSQL and selected services; separate project databases and Auth/REST/Storage containers | Modified Supabase composition; conformance testing required | Closest broad adaptation candidate |
| arunrajiah/supafleet | Shared PostgreSQL, project database and Auth/REST/Storage containers | Claims Supabase-compatible REST/Auth/Storage/GraphQL | Smaller implementation reference; credential isolation blocks unchanged adoption |
| smartpiai/multibase | Separate generated Compose deployments managed through CLI/dashboard | Upstream stack management | Alternative orchestration reference; no demonstrated shared-cluster savings |

## Candidate evidence and tradeoffs

### Coolify

Coolify supports self-hosted management APIs for projects, environments, servers and services. A service is a stored Docker Compose stack. Its Supabase guide describes a database component, and its service documentation says template updates do not rewrite existing deployed Compose definitions. Operational reuse is valuable, but template ownership and upgrades remain explicit work. No shared multi-project PostgreSQL topology was established by these sources. [API overview](https://coolify.io/docs/api/overview), [service behavior](https://coolify.io/docs/services), [Supabase template guide](https://coolify.io/docs/services/supabase)

Core repository license: Apache-2.0. Verify the exact selected revision and dependency notices when distributing an integration. [License](https://raw.githubusercontent.com/coollabsio/coolify/main/LICENSE)

### Dokploy

The official Supabase template generates project credentials, launches the stack and documents a supabase-js connection. It describes a `db` container. This supports using Dokploy to manage ordinary Supabase deployments, not an inference that its template shares one PostgreSQL cluster across projects. [Template](https://dokploy.com/templates/supabase)

The current repository license applies Apache-2.0 outside proprietary directories; proprietary directories have a separate license. The official January 2026 announcement confirms the change from its previous adapted license. Enterprise features require a license. Do not describe the entire distribution as uniformly Apache-2.0 or assume all administrative features can be reused in an OSS fork. [Current license](https://github.com/Dokploy/dokploy/blob/canary/LICENSE.MD), [license change](https://dokploy.com/blog/we-are-updating-dokploys-open-source-license), [enterprise license requirements](https://docs.dokploy.com/docs/core/enterprise/license-keys)

### Pigsty

Pigsty documents a Supabase integration with monitoring, backup/PITR and PostgreSQL operational tooling. It identifies this as an independent community integration. Its guide supplies a deployment template, but does not establish an out-of-the-box multi-project Supabase control plane. Treat it as an operations component or alternative packaging route, not a drop-in replacement for sbarbase project management. Operational complexity and minimum resource use still need measurement on the target VPS. [Integration documentation](https://pigsty.io/docs/app/supabase/)

The current core license is Apache-2.0. Release notes show versioned releases and explicitly record a historical move from AGPL-3.0 to Apache-2.0. Pin the chosen version; included components retain their own licenses. [Current license](https://raw.githubusercontent.com/pgsty/pigsty/main/LICENSE), [release history](https://github.com/pgsty/pigsty/releases)

### GustavoMartins123/supabase-multitenant

This implementation describes shared PostgreSQL, Studio, Supavisor, modified Realtime, Functions and management services. Each project has a database, JWT secret and dedicated Auth, REST, Storage, ImgProxy and Nginx. The README explicitly calls it unofficial and under development. It also says lifecycle execution moved to a host agent, while its overview diagram still depicts API access to Docker. Verify implementation against documentation before adopting. Modified Realtime, dynamic Studio routing and globally shared Functions are priority audit targets. Dynamic Studio routing is now the reference point to read rather than a side note: that project already serves per-project administration, which sbarbase answers with one Studio per environment instead of a shared instance. [Repository and architecture summary](https://github.com/GustavoMartins123/supabase-multitenant), [detailed architecture](https://github.com/GustavoMartins123/supabase-multitenant/blob/main/docs/00-arquitetura.md)

The core license is Apache-2.0. This is the first candidate to evaluate for adaptation because its advertised topology overlaps the requirement. That ranking expresses architectural fit, not a maturity or safety claim. [License](https://raw.githubusercontent.com/GustavoMartins123/supabase-multitenant/main/LICENSE)

### arunrajiah/supafleet

Supafleet documents a shared PostgreSQL instance and per-project database, JWT secret, Auth, REST and Storage containers. It includes a web UI and uses supabase-js in its connection example. Its documented API surface lists REST, Auth, Storage and GraphQL; this does not establish Realtime or Functions coverage. Its capacity numbers are author claims, not accepted benchmarks. The core license is MIT, with attribution for Apache-licensed Supabase-derived files. [Repository](https://github.com/arunrajiah/supafleet), [license](https://raw.githubusercontent.com/arunrajiah/supafleet/main/LICENSE)

Concrete audit finding: the CLI provisioner assigns `PG_PASS` from the global `POSTGRES_PASSWORD`, then embeds that same password with the same database role names into every project's Auth, REST and Storage container. See source lines 60, 121, 154 and 182. This is not a unique database credential boundary per project. A compromised project service is therefore a different and broader threat than token reuse. No exploit was executed; database/HBA restrictions and the web provisioner's path require further inspection. Do not adapt this provisioning path unchanged. [CLI provisioning source](https://raw.githubusercontent.com/arunrajiah/supafleet/main/scripts/add-tenant.sh)

### smartpiai/multibase

Multibase describes CLI and dashboard management of separate project directories, each containing Compose configuration and volumes. Its dashboard monitors per-instance components including PostgreSQL. It is relevant for lifecycle and observability patterns, but the documented model does not establish the desired shared PostgreSQL engine. The repository license is MIT. [Repository](https://github.com/smartpiai/multibase), [license](https://raw.githubusercontent.com/smartpiai/multibase/main/LICENSE)

### Excluded replacement: Nhost

Nhost was checked but is outside the clarified scope: its application SDK and API contract replace the required Supabase foundation. Its core repository is MIT. It is not a selection candidate. [Repository and self-hosting entry](https://github.com/nhost/nhost), [license](https://raw.githubusercontent.com/nhost/nhost/main/LICENSE)

## Proposed decision experiment

Use the same three environments on the same test host: A production, A staging and B production. Compare an ordinary upstream Supabase baseline with the closest existing multi-project implementation. Record idle memory, loaded memory, connection count, WAL growth, storage IO and upgrade/recovery work. Do not publish a number of supported projects without the workload and resource limits.

Acceptance requires denied cross-environment access through application tokens, direct runtime database credentials, Storage, Realtime, Functions, jobs and administrative project selection. Test stale routes during deletion and recreation, two Studio tabs on different projects, password/key rotation, failed upgrades and recovery to a different target. Two Studio tabs on two different environments is now a named acceptance test for the administration surface itself. Verify project-local schema changes cannot alter another project's authorization. Treat environment as the isolation unit even if an upstream tool calls that object a project.

For a downloadable product, the operator controls their own machine. That changes the business model, but does not eliminate hostile application input, compromised dependencies, unsafe SQL or accidental production access. Avoid adding public-hosting billing and reseller machinery to the minimum product.

## Evidence limits

Primary documentation, repository pages, license files and one provisioning source file were inspected. No candidate was installed or benchmarked, and no full SDK conformance suite or security audit was run. GitHub API metadata requests for maintenance statistics failed with rate-limit responses. Consequently this report does not assert response-time SLAs, maintainer availability, current exact commit dates, or production maturity. All moving-branch links should be replaced with pinned commit references during implementation selection. A guessed architecture path returned 404; the repository's actual linked architecture document was then opened successfully.
