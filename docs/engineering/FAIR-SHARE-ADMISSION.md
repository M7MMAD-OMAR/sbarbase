# Fair share admission: a guaranteed share plus borrowed room

Status: design record, 2026-09-24. Step 1 (the gateway) and the saturation notice are
implemented in this change; memory, CPU and connection borrowing are the later steps listed at
the end.

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

1. **A guaranteed share.** Always available to the environment, whatever the others do.
2. **A ceiling.** The most it may take, and only out of room nobody else is using.

Between the two, the environment **borrows**. Borrowing stops, and borrowed room returns, as
soon as the neighbours need their guaranteed share. This is the same idea as Linux CPU weights
and as Kubernetes requests and limits: work conserving when the host is idle, fair when it is
contended.

## Step 1: the gateway (implemented)

`ConcurrencyGate` takes an optional borrowing policy, `{ceiling, headroom}`:

- **Within the guarantee** (`perEnvironment`, 8): admitted while the whole gateway has a free
  slot (`maximum`, 32). Otherwise 503, the installation is full.
- **Borrowing** (above the guarantee, up to `ceiling`): admitted only while at least
  `headroom` slots stay free in the whole gateway. Otherwise 429 with `Retry-After`.

The headroom is the part of the gateway that only requests within their guarantee may use.
When a quiet neighbour wakes up, its requests land in that headroom at once. The busy
environment's borrowed slots are not taken away mid request: no queue, no pre-emption. They
return as its requests finish (every slot is bounded by the 30 s response deadline), and it
cannot borrow new ones until the headroom is free again. A neighbour therefore always finds room
immediately for up to `headroom` requests, and the busy environment gets the rest of the idle
gateway.

The managed application gateway uses guarantee 8, ceiling 24, total 32, headroom 8. The
constructor without a policy keeps the fixed ceiling, so every existing caller and probe behaves
as before.

What this does not change: the per-service budgets (REST's cap of 3 from its pool) still bind,
and a heavy query inside the one PostgreSQL engine still shares that engine's CPU, memory and
IO. Borrowing at the gateway admits more requests; it does not make the engine bigger.

## The saturation notice (implemented)

A borrowing environment is fine. A problem is an environment that keeps needing more than it
can get, or keeps borrowing for a long time. The gateway records, per environment:

- how many requests were refused with 429 since the last sample;
- the most requests it had in flight since the last sample.

A monitor samples the gate once a minute. An environment is **saturated** in a minute when it
had any 429, or when it was at or above its guarantee for the whole minute's peak. After
`15` saturated minutes in a row the monitor writes one `environment.saturated` notification
through the existing outbox (`Catalog.notify`, [OPERATOR-NOTIFICATIONS](OPERATOR-NOTIFICATIONS.md)),
with the minutes, the refused count and the peak. The outbox window keeps it to one message
per condition window, and the drain delivers it by email, webhook or Telegram, whichever the
operator configured.

The message says what to do: raise this environment's share, move it to its own database
engine, or grow the server. The platform does not do any of these by itself, because each one
takes room from someone else and is the operator's decision.

## Telegram channel (implemented)

A third channel beside email and webhook. `lab/notify.py` sends the rendered envelope's
summary and action with the Bot API `sendMessage` call. The bot token is read from a 0600 file
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
- **Per environment shares in the catalog,** so the operator can raise one environment's
  guarantee from the console when a saturation notice arrives.
