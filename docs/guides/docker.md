[العربية](docker.ar.md)

# Install with Docker

The container installation targets a local rootful Linux Docker Engine with Compose 2.15 or later. Python, Bun and the Docker CLI come inside the Sbarbase image, so the host's own Python version does not matter. The CI workflow describes a clean-machine installation rehearsal; its existence does not establish a passing runtime result for the current checkout. Independent-host trials and measured resource enforcement remain required before publishing a supported-host matrix.

Size the server first with [choosing a server](choosing-a-server.md).

The default Docker data root is `/var/lib/docker` and socket is `/var/run/docker.sock`. If the daemon uses different absolute paths, set `SBARBASE_DOCKER_DATA_ROOT` to its reported `DockerRootDir` and `SBARBASE_DOCKER_SOCKET` to its local Unix socket before Compose creates the controller. These settings select existing paths; they do not reconfigure or migrate Docker. Bind mounts refuse missing sources rather than creating empty directories. The controller checks the daemon root, its own full container identity, project/service labels and both bind mounts before recovery or provisioning.

`DOCKER_CONTEXT`, `DOCKER_TLS` and `DOCKER_TLS_VERIFY` are cleared inside the controller, and its CLI uses the mounted socket. The Compose invocation itself must target the intended local daemon. Remote daemons, Docker Desktop and rootless profiles have not passed the required tests and the explicit `local-v1` profile refuses them. A custom data root also requires a runtime declaring `local-v1` compatibility. Rollback to a runtime without that declaration stays stopped rather than silently using its historical default root.

## 1. Get the code and start

```bash
git clone https://github.com/M7MMAD-OMAR/sbarbase /opt/sbarbase
cd /opt/sbarbase
docker compose up -d --build
```

The first start pulls the pinned Supabase images (about 2.4 GB) and takes a few minutes. Follow it with `docker compose logs -f`; it is ready when the log says `Local Sbarbase API: http://127.0.0.1:8790`.

## 2. Create the first operator

```bash
docker compose exec sbarbase python3 lab/bootstrap.py
```

It asks for an email, a client (organization) name and a password, without echoing the password. Then open the console at `http://127.0.0.1:8790` on the server (for example through `ssh -L 8790:127.0.0.1:8790 your-server`), sign in, create a project and an environment, and copy its connection details and key.

## 3. Put HTTPS in front

The console and the API listen on loopback only. Publish them through the TLS proxy described in [server deployment](server-deployment.md), or any reverse proxy you already run, pointed at `127.0.0.1:8790`.

## Everyday commands

| Task | Command |
|---|---|
| Status and logs | `docker compose ps`, `docker compose logs -f` |
| Health check | `docker compose exec sbarbase python3 lab/install_server.py smoke` |
| Stop everything cleanly | `docker compose down` |
| Start again | `docker compose up -d` |
| Update Sbarbase | the console's Updates page, or see [updates](#updates) below |
| Back up / restore | see [backup and restore](backup-and-restore.md); daily backups run on their own |

`restart: unless-stopped` brings Sbarbase back after a reboot once Docker itself starts at boot (`systemctl enable docker`). Data lives in Docker volumes and in the checkout's `.lab/` and `.secrets/` folders; `docker compose down` keeps all of it.

## Updates

The console's Updates page shows a newer signed release, installs a safe one with one click, and installs one that changes Auth, Storage or Realtime after you confirm a warning ([upgrades](upgrades.md)). The supervisor lets running work finish, backs up every environment, moves the checkout, stops cleanly and exits with code 42. `restart: unless-stopped` starts the container again on any exit, with the same image. The container's baked profile validator runs before the upgrade guard and checks again afterward, before dependencies or image pulls. The new version then holds application traffic until its health checks pass. If they do not pass, it moves back by itself and the container restarts once more on the previous version; the guard also moves back after 3 failed starts or a start that died halfway.

Two settings in `compose.yaml` concern updates. `TZ` sets the container's time zone, which the maintenance window of automatic updates is read in (UTC when unset; the image carries the time zone database, so a name such as `Asia/Dubai` works). `SBARBASE_RELEASE_SOURCE` points the update check at a mirror; leave it empty for the canonical repository.

A plain restart reuses the image and the container `compose.yaml` created. A release whose class is "needs a rebuild" changes one of them, so it is installed on the server, and the container is rebuilt:

```bash
docker compose exec sbarbase python3 lab/upgrade.py start --release vX.Y.Z --allow-class rebuild
docker compose up -d --build
```

Do not update with `git pull`: that skips the backup, the control snapshot and the way back. The first move onto the version with the update channel is a rebuild too; the [upgrades guide](upgrades.md) has the steps. The update channel has unit and CI coverage and a local rehearsal-VM run: [vm-channel-checks.json](../evidence/vm-channel-checks.json) records 114 checks on 2026-09-26 using releases signed with a throwaway key. It has not been accepted on an independent public server, and no upstream release adoption is established by that rehearsal.

## How it fits together

The container holds the control plane: the supervisor, the provisioning worker, the console and the gateway. It starts the pinned Supabase services as sibling containers through the host's Docker socket. The following settings in `compose.yaml` make that work, and tests keep them in place:

- the checkout is mounted at the same path as on the host, and the host network is used, so paths and loopback addresses mean the same inside and outside;
- the selected Docker data root is mounted read only at the identical path, so block IO device discovery uses the selected daemon's persistent data;
- `cgroup: host` exposes the controller's own full container identity for exact inspection;
- `init: true`, because the supervisor refuses to run as process 1.

Access to the Docker socket is equivalent to root on the host, as it is for the systemd install; the [threat model](../explain/threat-model.md) explains why that is accepted for now.
