[العربية](why.ar.md)

# Why Sbarbase

## What it is

Sbarbase lets one server host many Supabase projects, for several clients, while every environment keeps its own database, its own logins and the original Supabase services. The goal is that backing up, restoring, moving and upgrading one environment is routine and does not put the others at risk.

## Why

**The problem.** Official self-hosted Supabase is a single-project deployment: one stack, one Studio, one project. A developer or agency with ten small client apps has two obvious choices today, and both hurt:

- Run a full Supabase stack per app. Simple and well isolated, but every stack repeats PostgreSQL, Auth, REST, Storage and their supporting containers, and every stack is backed up and upgraded on its own.
- Put several apps in one Supabase project, separated by schema. Cheap, but the apps then share one Auth, one set of API roles and one lifecycle: you cannot restore or move one app without touching the rest.

**Evidence people want this.** Several independent open source projects already try to run many Supabase projects on one host, which shows the need is real. The ones reviewed in [the alternatives review](../engineering/reviews/alternatives-product.md):

| Project | Approach | Observation from the review |
|---|---|---|
| GustavoMartins123/supabase-multitenant | Shared PostgreSQL and some shared services; per-project database, Auth, REST and Storage | Closest in topology; describes itself as unofficial and in development; modified Realtime and shared Functions need audit |
| arunrajiah/supafleet | Shared PostgreSQL; per-project database, Auth, REST and Storage | Its CLI provisioner reuses one global database password in every project's services, so a compromised project service is not contained by its credentials |
| Coolify, Dokploy | Deployment managers with a Supabase template | Deploy one ordinary stack per project; they do not share a database engine between projects |
| smartpiai/multibase | Separate Compose deployment per project with a dashboard | Lifecycle and monitoring patterns, no shared engine |

None of these was installed or benchmarked; the review read their documentation, licenses and one provisioning script.

**What Sbarbase does differently.**

1. **Original services only.** Auth, PostgREST and Storage are the upstream images, pinned by digest. Nothing rewrites Auth or switches databases at request time, so existing SDKs and SQL keep working.
2. **Credentials per environment, not per server.** Each environment gets its own database and its own scoped service logins, with connection rules that name exactly which login may reach which database. A leaked service credential from one environment should not open another.
3. **Recovery designed in.** One environment can be fenced, exported encrypted and restored into a separate engine, then verified before traffic is switched.
4. **Crash-oriented operations.** Provisioning and migrations write durable records before acting and refuse to guess after a crash.

**Rejected alternative.** Replacing Supabase with a different backend (for example Nhost) was ruled out: it changes the SDK and API contract the apps depend on. See the [decision register](../decisions/README.md).

## How we built it

![Left, three full Supabase stacks that each repeat every component; right, Sbarbase with one gateway, Auth and REST per environment, one PostgreSQL engine with a database per environment, and one shared Storage; the shared parts are a shared failure boundary](../diagrams/full-stack-vs-shared.svg)


A small control plane in TypeScript (Bun) keeps the catalog of clients, projects and environments and serves the console and the gateway. A Python runtime starts and supervises the pinned upstream containers. The full picture is in [architecture](architecture.md). Code: [src/control/catalog.ts](../../src/control/catalog.ts), [src/gateway/handler.ts](../../src/gateway/handler.ts), [lab/durable_runtime.py](../../lab/durable_runtime.py).

## Limits

- Resource savings compared with one full stack per app have not been measured. The design keeps independent PostgreSQL per environment as the fallback if sharing does not pay off.
- Shared PostgreSQL and shared Storage are shared failure boundaries. See [isolation and trust](isolation-and-trust.md).
- There is no measured capacity figure; no "10 projects" or "100 projects" promise is made.
- Server operators are trusted. This is not hosting for mutually hostile customers.

## Go deeper

- [Alternatives review](../engineering/reviews/alternatives-product.md) and [architecture research](../engineering/ARCHITECTURE-REVIEW.md).
- [Supabase feasibility review](../engineering/reviews/supabase-feasibility.md) and [security and operations review](../engineering/reviews/security-operations.md).
- [Capacity method](../engineering/reviews/capacity-method.md): how savings would be measured.
