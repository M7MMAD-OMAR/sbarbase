# Decision register

Recorded 2026-09-20. These are concise research conclusions, not production certification. Source details are in the linked reviews.

| Topic | Current position and reason | Alternative and why not selected now |
|---|---|---|
| Foundation | Keep Supabase: explicit user requirement and existing application compatibility | Nhost or custom Auth would change the required API/SDK contract |
| Hierarchy | Installation > organization > project > environment; ownership independent of placement | Coupling organization to server makes transfers and capacity balancing harder |
| Database boundary | Experiment with database per environment on shared PostgreSQL | Schema per project provides a weaker separation for the desired independent lifecycle; full stacks remain a cost baseline |
| API roles | Test canonical NOLOGIN roles plus unique service logins, exact login/database HBA and CONNECT grants | Namespaced roles may break existing SQL policies; independent PostgreSQL is the fallback, not rejected |
| Auth and REST | Original service processes per environment | Request-time database switching is not an established supported mode; rewriting Auth adds security and compatibility risk |
| Other services | Sharing must pass pinned-version integration and isolation tests | Do not infer that multitenant configuration proves safe integration |
| Existing projects | Evaluate supabase-multitenant for adaptation, Pigsty for operations | No adoption approved. Supafleet CLI credential sharing needs correction. Coolify/Dokploy manage deployments but do not establish this shared topology |
| Capacity | Measure peak workloads and reserve recovery headroom before admission | Neither 500 daily visitors nor container count predicts hardware needs; no fixed 10/100 guarantee |
| Recovery | Restore into an isolated target, validate, then switch; include objects and configuration | Replication is not backup. Physical PITR targets the cluster; per-environment recovery needs temporary-cluster extraction |
| Local control store | SQLite key-hash adapter for the experiment | Not a final multi-host store decision; application databases remain PostgreSQL |
| Environment | Local, isolated, bounded experiments without rented infrastructure | VPS purchase and remote deployment are unnecessary before feasibility gates |

## Reconsider the candidate if

- Full upstream SQL/bootstrap or upgrades cannot preserve compatible canonical roles safely.
- Compromised service credentials can cross environment boundaries.
- One environment can exhaust shared resources without effective admission and containment.
- Backup/restore or migration cannot meet an explicitly chosen recovery objective.
- Measured savings do not justify operational complexity compared with independent instances.

## Research map

[Component feasibility and pinned Auth scan](reviews/supabase-feasibility.md), [adversarial security and operations](reviews/security-operations.md), [alternative products and licenses](reviews/alternatives-product.md), [benchmark method](reviews/capacity-method.md).

Three independent reviews informed the direction. They are design reviews, not security certification. There is no evidence that Supabase failed to invent a hierarchy; its self-hosted package and its managed cloud have different scopes.
