[العربية](cli.ar.md)

# The sbarbase command

`sbarbase` is one command for the tasks an operator does every week: check the server, take a backup, restore one, add an environment, rotate a key, upgrade, open Studio and read logs. It adds nothing new. Each subcommand runs a tool that already exists or calls the management API the console uses, with the same checks and the same refusals.

## Run it

The command is `deploy/sbarbase` in the installation directory. It runs `lab/sbarbase.ts` with Bun from that directory, wherever you call it from. It finds Bun on the PATH, in the running account's `~/.bun/bin`, or on the PATH the installed unit gives the service; `SBARBASE_BUN` names it outright. Run it as the service account, which owns the state:

```bash
sudo -u sbarbase /opt/sbarbase/deploy/sbarbase status
```

To type only `sbarbase`, link it onto the PATH once:

```bash
sudo ln -s /opt/sbarbase/deploy/sbarbase /usr/local/bin/sbarbase
```

With Docker, run it inside the control-plane container, from the checkout:

```bash
docker compose exec sbarbase deploy/sbarbase status
```

`sbarbase help` lists every subcommand, and `sbarbase help <command>` explains one.

## Subcommands

| Task | Command | What it runs |
|---|---|---|
| See how the server is doing | `sbarbase status` | reads local state, no sign-in |
| List environments | `sbarbase environments` | reads the control catalog, no sign-in |
| Back up now | `sbarbase backup now [<environment>]` | `lab/backup.py create` |
| List backups | `sbarbase backups list [<environment>]` | `lab/backup.py list` |
| Restore an environment | `sbarbase restore <environment> <backup>` | `lab/backup.py restore`, after you confirm |
| Add an environment | `sbarbase add-environment <project> <name>` | the management API, as the console |
| Rotate a publishable key | `sbarbase rotate-key <environment>` | the key API, as the console |
| Gateway share | `sbarbase share <environment> [<n>]` | the management API, as the console |
| Upgrade | `sbarbase upgrade [check\|start\|status\|rollback]` | `lab/upgrade.py` |
| Studio | `sbarbase studio start\|stop <environment>` | the management API; the supervisor runs `lab/studio.py` |
| Logs | `sbarbase logs [supervisor\|auth\|rest\|storage\|database]` | `journalctl` or `docker logs` |

`<environment>` is the environment id from the console, its runtime id (`e_` and 24 hex characters), or `<project>/<name>`, such as `shop/production`. When two clients have a project with the same name, write `<client>/<project>/<name>`. `<backup>` is the time `backups list` shows, such as `20260924T030000Z`.

### status

`status` answers without signing in, so it still works when the console or the management login is down. It reads the files the runtime already writes and shows:

- the supervisor: `sbarbase.service` and whether systemd reports it active, or the Docker container;
- the console and its loopback address;
- each environment with its state, its routing (serving or in maintenance, on its first engine or moved), whether its Auth and REST answer, and its last complete backup;
- notifications not yet delivered, with their kind, severity and delivery state. Their details and delivery errors are never printed.

It exits 0 when the console runs and every published environment answers, and 1 otherwise. `status --json` prints the same facts as one JSON object for a monitor.

### backup now, backups list, restore

These run `lab/backup.py`, described in [backup and restore](backup-and-restore.md). Without an environment, `backup now` backs up every environment. A manual backup keeps as many backups as the daily one: it reads `SBARBASE_BACKUP_KEEP` from your shell or, on a systemd install, from the unit, and `--keep N` overrides it. When neither sets it, `lab/backup.py` keeps 7.

`restore` asks you to type the backup time before it replaces anything, and says what is lost. At no terminal, as in a script, it refuses unless you pass `--yes`. What the restore sets aside is removed with `lab/backup.py discard-previous`.

### add-environment, rotate-key, share, studio

These sign in to the management API with your own operator account, exactly as the console does, so the same membership checks, capacity limit and audit record apply. The email comes from `--email`, `SBARBASE_EMAIL` or a prompt. The password comes from a prompt that does not echo, from `--password-stdin`, or from `--operator-file` with the private 0600 file `lab/operator_file.py` writes. A password is never accepted as an argument. The session is signed out at the end and its token is never stored or printed.

- `add-environment` asks for the environment and returns at once; the worker provisions it. Follow it with `sbarbase environments`.
- `rotate-key` issues a new publishable key, prints it once on standard output, then revokes the old key. Save the new key before you close the terminal: it is shown only once. When the environment has more than one active key, the command refuses rather than guess; name the key to replace with `--revoke <key id>`. If the revoke fails, the new key is active and the old one is too; revoke it from the console.
- `share` shows the environment's guaranteed share of the application gateway, and the allocation across the installation when you are an installation operator. `share <environment> <n>` sets it; only installation operators may, and the API refuses a share that would take the total past the gateway.
- `studio start` and `studio stop` record the request; the supervisor starts or stops Studio within a few seconds. Open it from the console, as [the Studio guide](studio.md) describes.

### upgrade

`sbarbase upgrade` runs `lab/upgrade.py check`, which changes nothing. `upgrade start`, `upgrade status` and `upgrade rollback` run the same subcommands of `lab/upgrade.py`, and `--to REF` picks the version. [Upgrades](upgrades.md) explains each step.

### logs

`sbarbase logs` shows the supervisor's log through `journalctl`; `--follow` keeps it open and `--lines N` sets how much history to show. The service account needs permission to read the journal, for example membership of `systemd-journal`. With Docker, the supervisor's log is the container's output: run `docker compose logs sbarbase` on the host instead.

`sbarbase logs auth <environment>` and `sbarbase logs rest <environment>` show one environment's Auth or REST container. `sbarbase logs storage` and `sbarbase logs database` show the shared Storage and PostgreSQL containers.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Done, or everything `status` checked is healthy |
| `1` | The operation failed. A delegated tool's own exit code is passed through unchanged |
| `2` | Usage error: an unknown command or option, or a missing argument |
| `3` | Refused or not confirmed: nothing was changed |

## Limits

- The command covers the common tasks only. Recovery export, moving an environment, adopting retained containers and the other runbook steps stay with their own tools, described in [server deployment](server-deployment.md).
- `status` and `environments` read the control catalog directly and read only. The management API has no route for routing records, so this is how the command shows them.
- Rotating a key is two API calls, not one: between them both keys are active for a moment.
- This is in development and not production ready. The command was tested with unit tests that stub every tool and the API; it has not yet been run on the rehearsal VM.
