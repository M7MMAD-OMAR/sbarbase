# Edge Functions network isolation

Status: 2026-10-15. Source change with unit tests (`lab/test_functions.py`). The live check below is planned and has not run, so nothing here is established on a running installation yet. Not a production readiness claim.

## Finding

Read from the code, without a live run, except where a row says it rests on upstream behaviour.

- Every environment's Auth and REST, every Realtime, every Studio and postgres-meta, the shared Storage, the management Auth and the database join one internal network, `NETWORK = PREFIX + '-net'` (`lab/durable_runtime.py`, created `--internal`; `Runtime.launch` puts every container on it; `lab/studio.py` `launch` does the same for Studio and postgres-meta).
- Before this change, `functions_start` also put each environment's Functions container on that network, and connected it to one shared outbound network, `sbarbase-durable-egress`, which every Functions container joined.
- The main service (`lab/functions/main/index.ts`) runs each function with `EdgeRuntime.userWorkers.create({..., netAccessDisabled: false})`. A worker shares the container's network namespace, so function code can open a connection to any address the main service can. Docker's embedded DNS resolves every container name on a shared network, so names such as `sbarbase-durable-<runtime>-rest` work as well as addresses.

So code deployed as a function in environment A, or a visitor who finds a request forgery bug in such code, could reach, without the gateway:

| Target | What A gets |
|---|---|
| B's REST `:3000` | Requests as `anon` with no key at all: whatever B's row level security lets `anon` read or write. The gateway's publishable key check, key revocation, concurrency admission, body caps, deadlines and drain or pause leases are all skipped. |
| B's Auth `:9999` | Sign up users, start password recovery and other mail through B's mail settings, outside the gateway's controls. Admin routes still need B's service role token. |
| B's Studio `:3000` and postgres-meta `:8080`, while an operator has Studio open for B | The most serious path, if upstream behaves as expected. The code here shows that `lab/studio.py` gives postgres-meta no authentication setting and that it connects as `<B>_studio`, which is `BYPASSRLS`. That postgres-meta checks nothing itself (in Supabase's own compose file it is reached only through Kong), and that Studio's self-hosted API routes forward to it without a session check, is upstream behaviour not yet confirmed here; live check step 1 confirms it. |
| B's Functions `:9000`, over the shared outbound network | B's functions without the gateway, and B's own `/_sb/<auth|rest|storage>/v1` proxy, which forwards to B's services with B's Storage tenant pinned. |
| Shared Storage `:5000` | Storage takes the tenant from `X-Forwarded-Host`, which the caller sets. Every request is checked against the named tenant's JWT secret, so only what needs no token is open: B's public bucket objects. |
| B's Realtime `:4000` | Reachable, but joining a channel needs a token signed with B's secret. |
| The database `:5432` | Reachable; HBA allows each login only to its own database, with SCRAM passwords that Functions never hold. A password guessing surface, nothing more. |
| The management Auth | Reachable; protected only by its own settings. |

The intended boundary is that application visitors, and code that handles their input, stay inside their own environment. The paths above break it, the Studio one most of all.

## Options

1. **A network per environment for every service.** Each environment's Auth, REST, Realtime, Studio and Functions on its own network, with the database and Storage joining all of them. It multiplies bridge networks by the number of environments for every environment, not just the ones running Functions, and it needs a migration for every retained container. Against Functions it closes nothing that option 3 does not.
2. **Worker network restrictions.** `netAccessDisabled: true` stops `npm:` and `jsr:` imports and every outbound API call, which is most of what functions are for. A per-worker allow list is not something this design relies on: [EDGE-FUNCTIONS](EDGE-FUNCTIONS.md) already rejects resting isolation between clients on the runtime's worker sandbox alone, and it would sit inside the namespace it is meant to restrict. At most it is defence in depth later.
3. **Take Functions off the shared network (chosen).** Functions are the one service that runs operator-deployed code handling visitor input. Auth, REST, Realtime and Studio run no tenant code. Giving each environment's Functions their own internal network, holding only that environment's services, closes every row above except the Storage tenant header, with one extra bridge per environment that has Functions on.
4. **Functions egress only through a per-environment proxy.** Needed for Storage (below), and possible for everything, but it adds a host listener or a container per environment. Kept for Storage only, as follow-up.

## Design (built)

- **Environment network.** `functions_network(e)` is `sbarbase-durable-<runtime>-fn`, created `--internal` with labels `io.sbarbase.owner=durable-upstream` and `io.sbarbase.functions=<runtime>`. Members: that environment's Functions, Auth and REST, and the shared Storage. An existing network of that name that is not internal or carries another environment's label is refused.
- **Functions join no other internal network.** `functions_start` launches the container with `network=functions_network(e)` instead of the runtime network, and the gateway reaches it at its address on that network (`endpoint(name, port, network)`). The main service's `SB_AUTH_URL`, `SB_REST_URL` and `SB_STORAGE_URL` keep their container names, which resolve on the environment network. Other environments' names resolve nowhere.
- **Outbound network without traffic between containers.** `EGRESS` is now `sbarbase-durable-outbound`, created with `com.docker.network.bridge.enable_icc=false`, so a Functions container reaches the internet but not another Functions container. `ensure_egress()` refuses an outbound network that allows inter-container traffic or is internal. Network options cannot change after creation, so a new name replaces `sbarbase-durable-egress` rather than recreating it under running containers; the earlier network is removed once no container, running or stopped, names it. `lab/import_project.py` uses the same helper for its source tool.
- **Membership survives recreation.** `Runtime.launch` calls `join(name, functions_links(name, component))` after it creates or restarts a container: an environment's Auth and REST rejoin their environment network when it exists, and Storage joins every one. That covers each path that recreates them: `activate_services`, mail reconcile, sign-in settings reconcile (including its rollback, which keeps the old container's attachments), signing rotation, image drift replacement and Storage at runtime start.
- **Turning Functions off.** `functions_network_remove` disconnects every member, naming the environment's Auth and REST and Storage explicitly because `network inspect` lists only running containers and a stopped member would fail to start once its network is gone, and then removes the network.
- **Existing installations.** A retained Functions container is always recreated by `functions_start`, which runs for every environment with Functions on at each runtime start, so the next start moves them onto the new networks.

Realtime stays on the runtime network. Functions cannot reach any Realtime now, their own included; `/_sb` has no Realtime route, so nothing that worked before stops. If one is added later, attaching the environment's own Realtime to its environment network is safe, since Realtime is per environment.

## What stays open

- **Storage tenant header.** Storage is shared and joins every environment network, so a function in A can still send `X-Forwarded-Host: <B>.storage.internal` and read B's public objects without B's publishable key and outside the gateway's controls. Signed URLs, uploads and private objects still need B's tokens. The pin has to live outside the Functions container, because the worker and the main service share one network namespace. Follow-up design: a host listener per environment network, modelled on `studioUpstream` in `src/control/studio.ts`, that serves only that environment's `auth`, `rest` and `storage` and sets the tenant itself. It must also check that the source address is inside that environment's subnet: Linux accepts a packet for any local address on any interface, so the listening address alone does not identify the environment. The alternative is a small forwarder container per environment, which costs a pinned image and its memory row.
- **Storage admin port.** Storage's `:5001` admin API is now reachable from every environment network as it was from the runtime network. It needs the installation's Storage admin key, which no function holds.
- **Host addresses.** For the same reason, a container on any Docker network can reach every address of the host, including the runtime network's gateway, where the Studio internal route listens. That route needs the environment's service role token. Any host service listening on all addresses is reachable the same way; the live check records what answers.
- **Bridge network count.** Each environment with Functions on adds one bridge. Docker's default address pools give a daemon roughly thirty bridge networks; beyond that `network create` fails and turning Functions on fails with Docker's error. The remedy is `default-address-pools` with small subnets in the daemon configuration, or explicit `--subnet` allocation from an owned range later. This is a limit to measure, not a stated capacity.
- **Database-originated requests.** `pg_net` has no worker here (`lab/import_inspect.py`). If it is ever enabled, the database sits on the runtime network with every service, which is a separate design.

## Unit tests

`lab/test_functions.py`:

- Functions launch on their environment network, never the runtime network; the gateway endpoint is read on that network; the network is created internal with the environment label; exactly that environment's Auth and REST and Storage are connected; nothing names another environment.
- The outbound network is created with inter-container traffic off and is not internal; an existing one that allows that traffic is refused.
- The earlier shared outbound network is removed only once no container names it.
- `functions_links`: Auth and REST get their own environment network only when it exists, another environment's services and Realtime and the management Auth get none, Storage gets every environment network.
- A recreated Auth is created on the runtime network and then rejoins its environment network.
- Turning Functions off disconnects every member, stopped ones included, before removing the network.

## Live check (planned, not run)

Read [lab/README.md](../../lab/README.md) first. Container names are installation-wide, so only one placement may run per Docker daemon.

CI already runs `lab/functions-check.ts` in its Docker job on every change. On the new topology that is the first live evidence for the positive path: a function using supabase-js from npm against its own Auth, REST and Storage through `/_sb`. It says nothing about isolation; the steps below do.

1. Preconditions. Confirm no other checkout holds a placement on this daemon, in particular the worktree working on the upstream currency plan: `docker ps -a --filter label=io.sbarbase.owner=durable-upstream` must show only this checkout's retained runtime, or nothing. Confirm the host headroom `lab/README.md` asks for. Before starting from this checkout, with a Studio session open for one environment, send an unauthenticated `select 1` to its postgres-meta query route from the host, to confirm the severity of the Studio row above; after the change a function can only show that the route is unreachable.
2. Start the runtime from this checkout so retained Functions containers move to the new networks. With `docker network inspect`, record the members of each `sbarbase-durable-<runtime>-fn` and of `sbarbase-durable-outbound`, its `enable_icc` option, and that no Functions container is on `sbarbase-durable-net`. Confirm `sbarbase-durable-egress` is gone.
3. Two published environments A and B, Functions on in both, a Studio session open for B, Realtime on for B, and a public bucket with one object in B.
4. Deploy in A a probe function that tries each target with a two second timeout, by name and by the address `docker inspect` reports, and returns what happened. Expected to fail to connect or resolve: B's REST, Auth, Realtime, Studio, postgres-meta and Functions, the database, and the management Auth. Expected to work: A's own `/_sb/rest/v1`, `/_sb/auth/v1/health` and `/_sb/storage/v1/bucket` with A's service role key, and an `npm:` import and one outbound HTTPS call. Expected to remain open, recorded as the known residuals: Storage with `X-Forwarded-Host` set to B's tenant reading B's public object, and Storage's admin port answering 401 without the admin key.
5. Recreate A's Auth through a sign-in settings change, then repeat A's `/_sb/auth/v1/health`: it must still answer.
6. Turn Functions off in A: the environment network must be gone, and A's Auth and REST must restart cleanly afterwards.
7. Write the result as `docs/evidence/functions-isolation-checks.json`, with its own scope, from a probe script still to be written alongside `lab/functions-check.ts`.
