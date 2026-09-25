[العربية](docker.ar.md)

# Install with Docker

The shortest path: any Linux server with Docker Engine and the Compose plugin. Python, Bun and the Docker CLI come inside the Sbarbase image, so the host's own Python version does not matter. CI runs this exact path on a clean machine on every change: build, start, first operator, a first project used through supabase-js, a restart, and a clean stop.

Size the server first with [choosing a server](choosing-a-server.md).

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

The console's Updates page shows a newer signed release, and installs a safe one with one click ([upgrades](upgrades.md)). The supervisor backs up every environment, moves the checkout, stops cleanly and exits with code 42. `restart: unless-stopped` starts the container again on any exit, with the same image, and the new version then holds application traffic until its health checks pass. If they do not pass, it moves back by itself and the container restarts once more on the previous version.

A plain restart reuses the image and the container `compose.yaml` created. A release whose class is "needs a rebuild" changes one of them, so it is installed on the server, and the container is rebuilt:

```bash
docker compose exec sbarbase python3 lab/upgrade.py start --release vX.Y.Z --allow-class rebuild
docker compose up -d --build
```

Do not update with `git pull`: that skips the backup, the control snapshot and the way back. The first move onto the version with the update channel is a rebuild too; the [upgrades guide](upgrades.md) has the steps. The update channel has unit tests only so far: its live CI cases and VM rehearsal have not run yet.

## How it fits together

The container holds the control plane: the supervisor, the provisioning worker, the console and the gateway. It starts the pinned Supabase services as sibling containers through the host's Docker socket. Three settings in `compose.yaml` make that work, and a test keeps them in place:

- the checkout is mounted at the same path as on the host, and the host network is used, so paths and loopback addresses mean the same inside and outside;
- `/var/lib/docker` is mounted read only, so block IO limits target the disk behind Docker's data;
- `init: true`, because the supervisor refuses to run as process 1.

Access to the Docker socket is equivalent to root on the host, as it is for the systemd install; the [threat model](../explain/threat-model.md) explains why that is accepted for now.
