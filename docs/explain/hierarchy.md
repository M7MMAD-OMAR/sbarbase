[العربية](hierarchy.ar.md)

# Hierarchy

## What it is

Your server holds clients, each client owns projects, and each project has environments such as production and staging. Who owns an environment (the client) is recorded separately from where it runs (the placement), so an environment can move without changing its owner.

## Why

**Choice: ownership separate from placement.** The catalog stores the ownership chain (client > project > environment) in one place and the runtime placement of each environment in a separate routing record. Moving an environment to a restored database engine changes only its routing record; its identifiers, keys and owner stay the same. Transferring a project to another client changes only ownership metadata; nothing is moved.

**Rejected alternative: tie a client to a server.** If a client's projects had to live where the client lives, balancing load or restoring one environment elsewhere would mean re-creating ownership, keys and memberships. Supabase's managed cloud and its self-hosted package also scope these things differently, so Sbarbase does not copy either.

**Why the environment, not the project, is the unit.** Production and staging of the same project have separate data, credentials and keys. Isolation, backup, move and restore therefore operate on one environment at a time.

## How we built it

![Client A owns the Shop project with production and staging environments, and Client B owns the Blog project with a production environment; on your server one shared PostgreSQL engine holds a separate database for each environment, linked by its routing record](../diagrams/hierarchy.svg)

*Ownership lives in the catalog; where each environment runs is a routing record on one ordinary server.*


Operators are members of a client with one of three roles:

| Role | Read projects | Create projects and environments | Manage keys | Manage members |
|---|---|---|---|---|
| Owner | yes | yes | yes | yes, keeping at least one owner |
| Admin | yes | yes | yes | invite, but not as owner |
| Viewer | yes | no | no | no |

Every mutation checks authority and writes the change in one database transaction, with an audit row. Placement changes use a revision number, so a stale caller cannot overwrite a newer decision.

Code: [src/control/catalog.ts](../../src/control/catalog.ts) (clients, projects, environments, memberships, jobs), [src/control/placement.ts](../../src/control/placement.ts) (routing with placement), [src/control/keys.ts](../../src/control/keys.ts) (hashed key metadata). In the code a client is an `organization` and your server is the `installation`.

## Limits

- Moving a project to another client (an owner of both, from the console or the [API](../reference/api.md)) revokes every API key of its environments and cancels its queued provisioning. It does not rotate an environment's JWT signing key or its direct database password, so tokens its Auth already issued keep working; rotate them when people of the old client must lose every kind of access ([signing key](../guides/signing-keys.md)).
- The first owner is created by a local setup command; everyone else joins by an invitation link that lasts 7 days.
- Placement is on one server. A second server is future work, and there is no multi-server coordination.
- Audit rows are local records, not a tamper-proof log.

## Go deeper

- [Control plane](../engineering/CONTROL-PLANE.md): roles, transactions and transfer limits.
- [Persistent routing](../engineering/PERSISTENT-ROUTING.md): maintenance flag, revisions and placement.
- [Decision register](../decisions/README.md): the hierarchy decision and its alternatives.
