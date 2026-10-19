# Edge Functions network isolation

Status: 2026-10-15. Source change with unit tests (`lab/test_functions.py`, `tests/functions-storage.test.ts`). The live check below is planned and has not run, so nothing here is established on a running installation yet. Not a production readiness claim.

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
4. **Functions reach Storage only through a per-environment proxy (chosen, for Storage only).** Closes the Storage row; see below. Auth and REST stay direct on the environment network: they are that environment's own, so a proxy would add a dependency on the loopback API and a hop for every call and close nothing.

## Design (built)

- **Environment network.** `functions_network(e)` is `sbarbase-durable-<runtime>-fn`, created `--internal` with labels `io.sbarbase.owner=durable-upstream` and `io.sbarbase.functions=<runtime>`. Members: that environment's Functions, Auth and REST. Storage is not a member (below). An existing network of that name that is not internal or carries another environment's label is refused.
- **Functions join no other internal network.** `functions_start` launches the container with `network=functions_network(e)` instead of the runtime network, and the gateway reaches it at its address on that network (`endpoint(name, port, network)`). The main service's `SB_AUTH_URL` and `SB_REST_URL` keep their container names, which resolve on the environment network. Other environments' names resolve nowhere.
- **Outbound network without traffic between containers.** `EGRESS` is now `sbarbase-durable-outbound`, created with `com.docker.network.bridge.enable_icc=false`, so a Functions container reaches the internet but not another Functions container. `ensure_egress()` refuses an outbound network that allows inter-container traffic or is internal. Network options cannot change after creation, so a new name replaces `sbarbase-durable-egress` rather than recreating it under running containers; the earlier network is removed once no container, running or stopped, names it. `lab/import_project.py` uses the same helper for its source tool.
- **Membership survives recreation.** `Runtime.launch` calls `join(name, functions_links(name, component))` after it creates or restarts a container: an environment's Auth and REST rejoin their environment network when it exists. That covers each path that recreates them: `activate_services`, mail reconcile, sign-in settings reconcile (including its rollback, which keeps the old container's attachments), signing rotation and image drift replacement.
- **Storage through a host listener.** The shared Storage picks its tenant from `X-Forwarded-Host`, and a function's worker shares the main service's network namespace, so a tenant set inside the Functions container could be set to anything by function code. Storage therefore joins no environment network, and `functions_links` gives it none. `functions_start` reads the environment network's IPv4 gateway and subnet (`functions_address`), sets `SB_STORAGE_URL=http://<gateway>:25433` (`SBARBASE_FUNCTIONS_STORAGE_PORT`), and publishes `{host, port, subnet}` in the environment's `functions` entry of `endpoints.json`. The loopback API (`lab/upstream-server.ts`) keeps one listener per published entry, opened and closed every two seconds as entries change (`functionsStorageListeners` in `src/control/functions-storage.ts`). Each listener:
  - binds only that gateway address, so it exists only on the host's side of that bridge;
  - closes a connection whose source address is outside that environment's subnet before reading anything (`serveLocal`'s `admit`). Linux accepts a packet for any local address on any interface, so the listening address alone would admit any container that can route to it. A function in B that connects to A's gateway address has no route on B's environment network for it, so the packet leaves through the outbound network with B's outbound address as its source, outside A's subnet, and is closed;
  - forwards every path to Storage at its address on the runtime network, drops the caller's `Forwarded` and `X-Forwarded-*` headers, sets `X-Forwarded-Host` to that environment's tenant, and refuses a published tenant that does not name the environment;
  - checks no key: Storage checks every token against the tenant's own secret, as before;
  - answers 503 while the environment is not ready, has Functions off, or an upgrade hold is on;
  - allows 150 seconds per request, the function worker's own limit, instead of the loopback API's 30.

  The main service's `/_sb/storage/v1` forwards to that listener and no longer sets a tenant itself; `SB_STORAGE_HOST` is gone. A retained Storage that an earlier version attached to an environment network is disconnected by `functions_start`. The listener opens only while the loopback API runs; so does the gateway, the only way requests reach Functions, so a function that runs has its Storage listener within two seconds of being published.
- **Why not a forwarder container.** A small proxy container per environment on the environment network and the runtime network would do the same without the host's weak host model to reason about, but it costs a third pinned image with its own drift checks, a memory row in `lab/resource_policy.py` and a container per environment in admission, restart and recovery. The listener costs one socket in a process that already runs, and the source check is a few lines with unit tests.
- **Turning Functions off.** `functions_network_remove` disconnects every member, naming the environment's Auth and REST explicitly because `network inspect` lists only running containers and a stopped member would fail to start once its network is gone, and Storage too in case an earlier version attached it, and then removes the network. The listener closes at the next reconcile, once the `functions` entry is gone.
- **Existing installations.** A retained Functions container is always recreated by `functions_start`, which runs for every environment with Functions on at each runtime start, so the next start moves them onto the new networks.

Realtime stays on the runtime network. Functions cannot reach any Realtime now, their own included; `/_sb` has no Realtime route, so nothing that worked before stops. If one is added later, attaching the environment's own Realtime to its environment network is safe, since Realtime is per environment.

## What stays open

- **Host firewall, observed 2026-10-19 on the owner's server.** ufw with its default incoming deny dropped Studio's connections from the runtime network to its gateway on 25432 (the kernel log shows `UFW BLOCK` from Studio's address); the same chain drops 25433 from an environment bridge. Studio's Storage, users and API docs pages failed with it. The two allow rules are in `docs/guides/server-deployment.md`, and `lab/studio.py` now checks the route from inside Studio instead of from the host, which always reached its own bridge address.
- **Storage tenant header: closed in source, not yet live.** Storage is on no environment network, and the listener sets the tenant and refuses sources outside the environment's subnet. Until the live check runs, this rests on unit tests and on two things not yet observed on this host: that the host firewall accepts connections from an environment bridge to its gateway address (Studio's internal route on the runtime network relies on the same for that bridge), and that the source address of a connection from another environment is its outbound address, as routing predicts.
- **The listener admits the host itself.** The gateway address is inside the subnet, so a connection from the host to that address passes the source check. Operators are trusted, so this is accepted.
- **Host addresses.** A container on any Docker network can reach every address of the host, including the runtime network's gateway, where the Studio internal route listens, and other environments' gateway addresses, where their Storage listeners close it for its source. The Studio route needs the environment's service role token. Any host service listening on all addresses is reachable the same way; the live check records what answers.
- **Bridge network count.** Each environment with Functions on adds one bridge. Docker's default address pools give a daemon roughly thirty bridge networks; beyond that `network create` fails and turning Functions on fails with Docker's error. The remedy is `default-address-pools` with small subnets in the daemon configuration, or explicit `--subnet` allocation from an owned range later. This is a limit to measure, not a stated capacity.
- **Database-originated requests.** `pg_net` has no worker here (`lab/import_inspect.py`). If it is ever enabled, the database sits on the runtime network with every service, which is a separate design.

## Unit tests

`lab/test_functions.py`:

- Functions launch on their environment network, never the runtime network; the gateway endpoint is read on that network; the network is created internal with the environment label; exactly that environment's Auth and REST and Storage are connected; nothing names another environment.
- Storage is never joined to the environment network, and a retained Storage attached to it is disconnected; `functions_links` gives Storage no network.
- `SB_STORAGE_URL` is the environment network's gateway at the listener port, with no tenant setting; the published entry carries the gateway, the port and the subnet; the IPv4 gateway is chosen over an IPv6 one, and a network without one is refused.
- The outbound network is created with inter-container traffic off and is not internal; an existing one that allows that traffic is refused.
- The earlier shared outbound network is removed only once no container names it.
- `functions_links`: Auth and REST get their own environment network only when it exists, another environment's services and Realtime and the management Auth get none, Storage gets none.
- A recreated Auth is created on the runtime network and then rejoins its environment network.
- Turning Functions off disconnects every member, stopped ones included, before removing the network.

`tests/functions-storage.test.ts`:

- The subnet check admits addresses inside the subnet, in plain and IPv4-mapped form, and refuses another environment's subnet, the outbound and runtime networks, loopback, IPv6 and malformed addresses or subnets.
- The listener sets the tenant whatever `X-Forwarded-Host` or `Forwarded` the caller sends, drops `X-Forwarded-For`, passes the caller's tokens through, and refuses an inactive environment or a published tenant for another environment without forwarding anything.
- On a real loopback listener, a connection whose source is refused is closed before the handler runs, and an admitted one is served; a source check with WebSocket upgrades is refused at start.
- Listeners open for well formed published entries only, reopen when the gateway changes, close when Functions turn off, and a failed bind is retried at the next reconcile.

## Live check (planned, not run)

Read [lab/README.md](../../lab/README.md) first. Container names are installation-wide, so only one placement may run per Docker daemon.

CI already runs `lab/functions-check.ts` in its Docker job on every change. On the new topology that is the first live evidence for the positive path: a function using supabase-js from npm against its own Auth, REST and Storage through `/_sb`. It reaches the API through the running `lab/upstream-server.ts` (`.lab/upstream/server.json`), which is the only entry point that opens the Storage listeners; the other live checks compose the application in process and do not call Functions. It says nothing about isolation; the steps below do.

1. Preconditions. Confirm no other checkout holds a placement on this daemon, in particular the worktree working on the upstream currency plan: `docker ps -a --filter label=io.sbarbase.owner=durable-upstream` must show only this checkout's retained runtime, or nothing. Confirm the host headroom `lab/README.md` asks for. Before starting from this checkout, with a Studio session open for one environment, send an unauthenticated `select 1` to its postgres-meta query route from the host, to confirm the severity of the Studio row above; after the change a function can only show that the route is unreachable.
2. Start the runtime from this checkout so retained Functions containers move to the new networks. With `docker network inspect`, record the members of each `sbarbase-durable-<runtime>-fn` (no Storage among them) and of `sbarbase-durable-outbound`, its `enable_icc` option, and that no Functions container is on `sbarbase-durable-net`. Confirm `sbarbase-durable-egress` is gone. With `ss -ltn`, confirm one Storage listener per environment with Functions on, each on its gateway address only.
3. Two published environments A and B, Functions on in both, a Studio session open for B, Realtime on for B, and a public bucket with one object in B.
4. Deploy in A a probe function that tries each target with a two second timeout, by name and by the address `docker inspect` reports, and returns what happened. Expected to fail to connect or resolve: B's REST, Auth, Realtime, Studio, postgres-meta and Functions, the database, and the management Auth. Expected to work: A's own `/_sb/rest/v1`, `/_sb/auth/v1/health` and `/_sb/storage/v1/bucket` with A's service role key, and an `npm:` import and one outbound HTTPS call. Expected to fail too: Storage by name and by address, on `:5000` and on `:5001`. Through `/_sb/storage/v1`, A's public object must be readable and a request carrying `X-Forwarded-Host` for B's tenant must still return A's objects, never B's. A connection from A to B's gateway address at the listener port must be closed without an answer, and to A's own gateway address must be served. Record the source address B's listener saw refused.
5. Recreate A's Auth through a sign-in settings change, then repeat A's `/_sb/auth/v1/health`: it must still answer.
6. Turn Functions off in A: the environment network must be gone, and A's Auth and REST must restart cleanly afterwards, and the Storage listener on its gateway address must close within a few seconds.
7. Write the result as `docs/evidence/functions-isolation-checks.json`, with its own scope, from a probe script still to be written alongside `lab/functions-check.ts`.
