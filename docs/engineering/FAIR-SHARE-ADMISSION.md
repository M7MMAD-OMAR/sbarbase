# Fair share admission: a guaranteed share plus borrowed room

Status: design record, 2026-09-24. The gateway step, the saturation notice and the Telegram
channel are implemented; memory, CPU and connection borrowing are the later steps listed at the
end.

## The problem with fixed ceilings

Every environment today has a fixed ceiling at each layer:

- the gateway admits at most 8 requests in flight per environment and 32 in total
  (`src/gateway/concurrency.ts`, [GATEWAY-OVERLOAD](GATEWAY-OVERLOAD.md));
- each environment's database logins have a fixed connection count
  ([CONNECTION-BUDGET](CONNECTION-BUDGET.md));
- each environment's Auth and REST containers run with a hard `--cpus` and `--memory`
  ([RESOURCE-POLICY](RESOURCE-POLICY.md), `lab/resource_policy.py` `TIERS`).

A fixed ceiling protects the neighbours, but it wastes the server. Five environments on one
host, four of them small sites with a few requests a week: when the fifth gets real traffic it is
refused at its ceiling while the host sits idle. The operator paid for the whole server and can
use a fraction of it.

## The rule

Each layer gives an environment two numbers instead of one:

1. **A guaranteed share.** Reserved for it while it is active.
2. **A ceiling.** The most it may take, and only out of room nobody else needs.

Between the two, the environment **borrows**. This is the same idea as Linux CPU weights and
as Kubernetes requests and limits: work conserving when the host is idle, fair when it is
contended.

## Step 1: the gateway (implemented)

`ConcurrencyGate` takes an optional borrowing policy, `{ceiling, headroom, recentMs}`:

- **Within the share** (`perEnvironment`, 8): admitted while the whole gateway has a free slot
  (`maximum`, 32). Otherwise 503.
- **Borrowing** (above the share, up to `ceiling`, 24): admitted only while the free slots
  exceed the larger of two reserves. Otherwise 429 with `Retry-After`.
  - the **unused share of every other environment that asked for a slot in the last
    `recentMs`** (60 s): an active neighbour always has its whole share waiting;
  - the **headroom** (8): room for environments waking up from idle.

So with four projects, one busy and three that were active in the last minute, the busy one
borrows only what is left after all three shares. With three that were quiet for a minute, it
may take up to 24. When a quiet one wakes, its first requests land in the headroom at once and
from then on its whole share is reserved.

What this does not guarantee: several environments waking up **in the same moment** share the
headroom of 8 until the busy one's borrowed slots return. Borrowed slots are not taken away
mid request (no queue, no pre-emption); they return as requests finish, each bounded by the
30 s response deadline. A neighbour turned away this way is counted against the borrower (see
below).

The managed application gateway uses share 8, ceiling 24, total 32, headroom 8, recent 60 s. The
constructor without a policy keeps the fixed ceiling, so every existing caller and probe behaves
as before. The per-service budgets (REST's cap of 3 from its pool) still bind, and a heavy
query inside the one PostgreSQL engine still shares that engine's CPU, memory and IO.

Evidence: [unit tests](../../tests/concurrency.test.ts) and a
[real loopback HTTP probe](../evidence/fair-share-checks.json) (`bun lab/fair-share-check.ts`,
seven checks, no containers). Not measured against Supabase services or under sustained load.

## The saturation notice (implemented)

Borrowing an idle server is the intended behaviour and is never reported. A problem is an
environment that keeps needing more than it can get. The gateway records, per environment:

- **refused:** 429s at its share or ceiling (not the per-service caps, which a bigger share
  would not fix);
- **squeezed:** neighbours within their share that got 503 while it was borrowing;
- **peak:** the most requests it had in flight.

A monitor samples the gate once a minute. A minute is **saturated** for an environment when
it was refused or squeezed a neighbour. After `15` saturated minutes in a row the monitor writes
one `environment.saturated` notification through the existing outbox (`Catalog.environmentSaturated`,
[OPERATOR-NOTIFICATIONS](OPERATOR-NOTIFICATIONS.md)), with the minutes, the count and the peak.
The outbox window keeps it to one message per condition window, and the drain delivers it by
email, webhook or Telegram, whichever the operator configured.

The message says what to do: raise this environment's share, move it to its own database
engine, or grow the server. The platform does not do any of these by itself, because each one
takes room from someone else and is the operator's decision.

## Per environment shares (implemented)

The share is no longer one number for all. `gateway_shares` in the catalog holds a share per
ready environment (default 8). The console's environment page shows it with the installation's
allocation, and owners and admins change it (`GET`/`PUT /management/v1/environments/{id}/share`,
`src/control/share.ts`). The gate asks the catalog on each request (`ConcurrencyGate.useShares`),
so a change applies to the next request without a restart; a lookup that fails falls back to the
default. A raised share is refused when the shares of all ready environments would exceed the
gateway's 32, so every guarantee can hold at once. Creating an environment later is not blocked by
this; the page shows the allocation, and the operator lowers a share if it has run over. The
ceiling stays 24, or the share itself when that is higher. Every change is an audit event.

## Telegram channel (implemented)

A third channel beside email and webhook. `lab/notify.py` sends the same text as the email
with the Bot API `sendMessage` call. The bot token is read from a 0600 file
named in the notifier configuration, never from an argument, and the chat id is configuration.
The same redaction gate runs on the rendered text before any byte leaves.

## Later steps

- **Memory:** `--memory-reservation` as the guarantee and a higher `--memory` as the ceiling
  for each environment's Auth and REST. Needs a measured soak first: a ceiling above the
  reservation changes the placement arithmetic in `resource_policy.start_placement`.
- **CPU:** the containers already carry `--cpu-shares` weights, which are work conserving. The
  hard `--cpus` becomes the ceiling and can rise above today's 0.25 once measured.
- **Database connections:** a guaranteed connection count per environment plus a shared
  borrowable pool, which needs a pooler; the connection pooler is itself unbuilt.

