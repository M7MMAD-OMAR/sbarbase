# Local REST SQL execution defaults

The durable runtime now configures each environment's REST login, scoped to its own database:

| Setting | Local default | Purpose |
|---|---:|---|
| statement_timeout | 8 seconds | Baseline for roles such as service_role without a shorter role setting |
| transaction_timeout | 12 seconds | End an overlong API transaction before the gateway's 15-second upstream fetch deadline |
| search_path | `pg_catalog` | Added with the Data API settings: the query PostgREST loads its configuration with resolves its names through it, so a function planted in `public` cannot take the place of a built-in ([Data API schemas](DATA-API-SCHEMAS.md)) |

Existing Supabase anon (3 seconds) and authenticated (8 seconds) statement settings remain unchanged. Auth, Storage, management logins and cluster-wide defaults are not modified. These are experimental REST defaults, not a production performance commitment.

## Live evidence

Command: `bun lab/gateway-overload-check.ts --sql-deadline`, after starting the owned durable runtime. [Fourteen live checks](../evidence/sql-deadline-checks.json) passed:

- An RPC using the service role reported effective statement/transaction settings of 8s/12s.
- An ordinary 20-second sleep RPC failed after 8008.80 ms with HTTP 500 and PostgreSQL code 57014.
- A second RPC explicitly hoisted statement_timeout=0. It failed after 12011.95 ms with HTTP 503/PGRST001, before the 15-second upstream deadline.
- No matching target SQL remained active after transaction expiry. The neighbor returned its distinct correct result, and the target reconnected and served a subsequent request.
- Temporary functions were removed, keys revoked and owned runtime stopped.

Thirty Python tests passed at the time, including warm-pool reconciliation checks. This probe observes sleep RPCs, not every CPU, lock, I/O or transaction rollback scenario.

## Reconciliation and compatibility

Role defaults apply to new logins. Provisioning writes them, and while the environment's REST container runs, `Runtime.rest_deadlines` without `rewrite` still refuses to change them. A start used to refuse the same way when they differed; it no longer does. Every start, and every Data API settings write, runs `rest_deadlines` with `rewrite`: it leaves the REST login exactly the three settings above in its own database, removes every other role setting of that login, in any database or globally, and writes no role setting when they already match (a start still writes the login's generated password back). Tenant code can act as the REST login and change its own defaults ([Data API schemas](DATA-API-SCHEMAS.md), finding 3), so a refusal let one environment stop the start of every other. New connections then take the settings; connections already open keep what they had. This does not audit manual out-of-band changes to already-open sessions.

Transaction expiry terminates the backend session and the client sees an upstream error. Do not automatically replay writes after arbitrary network errors; application idempotency remains necessary. Calls legitimately requiring more than these defaults need an explicit future operator policy; no UI for deadline overrides exists yet.

These defaults are not a non-bypassable boundary against trusted SQL authors or the host operator. Role/function settings can override statement_timeout; functions that change transaction_timeout itself are not covered. Environments still share PostgreSQL CPU, memory and I/O. These defaults complement request admission and retained cancellation slots, not replace them.

Sources: [PostgREST 14 transaction settings](https://docs.postgrest.org/en/v14/references/transactions.html), [PostgreSQL 17 timeout semantics](https://www.postgresql.org/docs/17/runtime-config-client.html), [ALTER ROLE session defaults](https://www.postgresql.org/docs/17/sql-alterrole.html).
