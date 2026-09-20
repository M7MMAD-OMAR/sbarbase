# Database operation fencing: design gate

Status: reviewed design constraints, not implemented. Recorded 2026-09-20.

## Problem

Host worker/effect/operation locks can be released after process death while a Docker daemon-side SQL execution remains queued or active. Observing a closed database or absent PID is not proof that an old command cannot run later. Recovery must retire old mutation authority before resuming or compensating partial SQL.

## Candidate to prove

An admin-only operation registry and a per-runtime session advisory lock could protect SQL batches executed inside the same database. A batch must acquire the lock, then read the exact active token with a fresh statement snapshot, then perform mutations on that same backend. Revocation acquires the same lock and commits a durable revoked record. Registration must preserve revocation tombstones and never reactivate a token or replace a newer operation.

CREATE DATABASE must remain outside a transaction block. The lock acquisition, token check and DDL can be separate psql commands on one session with ON_ERROR_STOP. Reconnecting requires a fresh lock and token check. A second connection that merely holds a lock is insufficient: its death can release the lock while another backend continues mutating.

## Rejected shortcut

One lock in the postgres database is not a cluster-wide mutation barrier. [PostgreSQL documents advisory locks as database-local](https://www.postgresql.org/docs/17/view-pg-locks.html). An identical numeric key in an environment database does not conflict with the control database lock. A registry stored in postgres is also not directly queryable from target-database SQL without another mechanism.

Therefore proving a control-database barrier covers only roles, database creation and grants executed in that database. Target schema and migration statements require their own explicit protocol. Do not enable whole-operation recovery based on the narrower proof.

## Required evidence before integration

- A delayed batch observes a revoked token and makes no application mutation.
- Revocation waits for a currently guarded batch and only reports completion after commit.
- Registration after revocation cannot resurrect authority.
- Different tokens, newer claims and reconnects cannot borrow prior authority.
- CREATE DATABASE works on the guarded session without an implicit transaction error.
- Crash and timeout paths release backend ownership without certifying an uncommitted revocation.
- Cross-database tests explicitly demonstrate the boundary, then validate a separate target-database protocol before broadening the claim.

Independent adversarial review identified the database-local lock constraint and registration resurrection risk. Current production-like code has no registry or SQL revocation barrier yet; unknown later stages remain blocked. Trusted host/SQL administrators remain outside the visitor isolation model.
