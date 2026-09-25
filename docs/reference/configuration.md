[العربية](configuration.ar.md)

# Configuration

What an operator can set, and where Sbarbase keeps its state. Paths are relative to the checkout. Sbarbase has no single configuration file: settings live in the files and flags below, and everything else is decided by code and pinned lock files.

## Host requirements

| Setting | Where | Notes |
|---|---|---|
| Docker endpoint | `DOCKER_HOST`, or `--docker-host` on `deploy/server-acceptance.sh` | Must reach a native Linux daemon. A system service does not inherit your shell, so a host whose Docker context points at a desktop socket must forward it in the unit |
| Bun | on `PATH`; `--bun-dir` for the service unit | Used for the console build and the loopback server |
| Python | `/usr/bin/python3`, 3.12 or newer, with `cryptography` | `--python` on `deploy/server-acceptance.sh` overrides it |

## Pinned images

`lab/distro-image.lock.json`, `lab/images.lock.json` and `lab/storage-image.lock.json` pin every upstream image by tag and digest. Change them only through `lab/pin_update.py`; see [upgrades](../guides/upgrades.md).

## Installer and service

`/usr/bin/python3 lab/install_server.py <command>`:

| Command or flag | Meaning |
|---|---|
| `check` | Read-only preflight; non-zero exit on any blocker |
| `plan` | Print the exact install steps |
| `install --bootstrap-file PATH` | Install, using a 0600 operator file (below) |
| `smoke` | Check the console and management endpoints answer |
| `supervise` | Render the systemd unit for this checkout and verify it; `--apply` installs it (root only) |
| `--service-user`, `--home`, `--bun-dir` | Account, home directory and Bun directory written into the unit. Default account `sbarbase` |

`deploy/server-acceptance.sh` accepts `--rehearse`, `--skip-install`, `--install-unit`, `--bootstrap-file`, `--service-user`, `--home`, `--bun-dir`, `--docker-host` and `--python`. The shipped unit is `deploy/sbarbase.service`; do not hand-edit an installed copy, render it with `supervise`.

## TLS proxy

`bun deploy/console-tls-proxy.ts` terminates HTTPS in front of the loopback server.

| Flag | Default | Meaning |
|---|---|---|
| `--cert`, `--key` | required | Certificate and key files |
| `--public-host` | required | Public host name (optional port); used for redirects instead of the client's `Host` |
| `--https-port` | `8443` | HTTPS listener |
| `--http-port` | `8080` | Plain HTTP listener that redirects with `308` |
| `--upstream` | the running loopback server | Where to forward |
| `--max-body` | the upload limit plus 1 MiB (51 MiB) | Larger bodies get `413`. Bodies up to 1 MiB are read whole; larger ones (file uploads) are passed on as they arrive |

## Operator bootstrap file

`/usr/bin/python3 lab/operator_file.py PATH` prompts (no echo) and writes a 0600 JSON file with exactly `email`, `password` and `organization`. `--stdin` reads the same object from stdin (at most 8192 bytes) for automation. Pass it to `install --bootstrap-file`, or run `lab/bootstrap.py` interactively instead. See [operator setup](../guides/operator-setup.md).

## Per-environment mail

Application mail (confirmation, recovery) for one environment is switched on by one file, `.secrets/upstream/<runtime>-mail.json`, mode 0600. Write, show or remove it with `lab/mail_config.py write|show|remove PATH`; the password is never printed. Keys: `host`, `port`, `user`, `pass`, `admin_email`, `sender_name`, `reply_to`, `max_frequency`, `otp_exp`, `otp_length`, `secure_email_change`, `autoconfirm`. A change is applied by reconciling that environment's Auth container. Full schema: [environment email](../engineering/ENVIRONMENT-EMAIL.md).

## Operator notifications

If `.lab/upstream/notifications.json` exists, the worker delivers operator events by email, by signed webhook, by Telegram, or any mix:

```json
{
  "schema": 1,
  "email": {"enabled": true, "host": "smtp.example.invalid", "port": 587,
            "from": "sbarbase@example.invalid", "to": "operator@example.invalid", "tls": "starttls"},
  "webhook": {"enabled": true, "url": "https://hooks.example.invalid/sbarbase",
              "secretFile": ".secrets/upstream/notifier.json"},
  "telegram": {"enabled": true, "chatId": "-1001234567890",
               "tokenFile": ".secrets/upstream/telegram.json"}
}
```

`tls` is `none`, `starttls` or `tls`. The webhook signing secret lives in `.secrets/upstream/notifier.json` as `{"schema": 1, "webhookSecret": "<64 hex characters>"}`. For Telegram, create a bot with @BotFather, add it to your chat, and put its token in a private file (mode 0600) as `{"schema": 1, "botToken": "<token>"}`. `chatId` is the chat's number, or `@channelname` for a public channel. The token is read from that file only and never appears in a message. Full design: [operator notifications](../engineering/OPERATOR-NOTIFICATIONS.md).

Among the events: **an environment kept needing more than its share.** The gateway guarantees each environment 8 requests in flight and lets a busy one borrow up to 24 of the 32, leaving free the unused share of every project active in the last minute (and at least 8). Borrowing alone is never reported. After 15 minutes in a row of being refused at its limit, or of crowding out a neighbour, you get one notice for that environment, with what to do: raise its share (the environment page in the console, **Gateway share**; installation operators only), move it to its own database engine, or grow the server. A share can be raised only while the shares of all ready environments fit in the gateway's 32; lower another first if they do not. Design: [fair share admission](../engineering/FAIR-SHARE-ADMISSION.md).

## Off-host backup copies

If `.lab/upstream/backup-offsite.json` exists, each daily backup run is encrypted and copied to one S3-compatible bucket:

```json
{
  "schema": 1,
  "s3": {"endpoint": "https://s3.eu-central-1.amazonaws.com", "region": "eu-central-1",
         "bucket": "example-backups", "prefix": "sbarbase/",
         "credentialsFile": ".secrets/upstream/offsite-s3.json"},
  "keyFile": ".secrets/upstream/offsite-key.json"
}
```

`endpoint` must be `https` (plain `http` only on loopback). `prefix` may hold letters, digits, `.`, `_`, `-` and `/`. `credentialsFile` is a 0600 file `{"schema": 1, "accessKeyId": "...", "secretAccessKey": "..."}`. `keyFile` is a 0600 file `{"schema": 1, "key": "<64 hex characters>"}`, created only on request with `python3 lab/backup.py offsite-key PATH`. Both files are refused when they are symlinks or readable by group or others. Retention follows `SBARBASE_BACKUP_KEEP`. Setup and limits: [backup and restore](../guides/backup-and-restore.md).

## Update channel

The update settings, requests and the upgrade record live in `.lab/upgrades/`, private to the service account: the directory is 0700, and the JSON files are 0600 and each replaced atomically. The console writes only `settings.json` and `request.json`; the supervisor and `lab/upgrade.py` write the rest ([upgrades](../guides/upgrades.md)).

| Path | Holds |
|---|---|
| `.lab/upgrades/settings.json` | The operator's settings: `check` (default on), `automatic` (default off) and the maintenance `window` in server local time (default 3:00 AM to 5:00 AM, stored as `"03:00"` and `"05:00"`). A missing or damaged file means the defaults |
| `.lab/upgrades/request.json` | The one request under way (`apply`, `rollback` or `check`, with `acknowledged` for an attended release), created only when none exists; the supervisor judges it again before acting |
| `.lab/upgrades/last-request.json` | The last finished request, for the console's progress view |
| `.lab/upgrades/current.json` | The supervisor's word to the console: the running version, the `rollback` verdict, the `apply` verdict for the release on offer (can it be installed now, the reason, whether it needs the acknowledgement), `pending` while an upgrade or rollback waits, and the `timezone` the window is read in |
| `.lab/upgrades/available.json` | The last check's result: the running version, the release on offer with its class, notes and refusals, and the newer releases passed over with the reason |
| `.lab/upgrades/available.previous.json` | A check result made on another version, moved aside so the console never offers the release just installed |
| `.lab/upgrades/check.json` | When the last check ran, its error and the count of failures in a row (for the backoff) |
| `.lab/upgrades/outcome.json` | How the last update child the supervisor ran ended (passed its point of no return, changed anything, refusals, error), for the request's detail and the automatic tries |
| `.lab/upgrades/ledger.json` | Per version: announced, automatic tries, whether a try was spent past its point of no return, and rolled back |
| `.lab/upgrades/state.json` | The last upgrade: from, to, phase (`applied`, `confirmed`, `rolling_back`, `rolled_back`, `rollback_failed`, `failed`), who started it, its snapshot, the guard's attempt and failed move counts, why it went back, a failed rollback of a confirmed version, whether it is stuck, and notices the next start sends |
| `.lab/upgrades/snapshots/` | Control state snapshots (catalog and key store) with a manifest; the newest three are kept, plus the one the last upgrade names |
| `.lab/upgrades/guard.py` | The start guard of the version the last upgrade left, which every start runs first (see [the start guard](../guides/upgrades.md#the-start-guard)) |
| `.lab/upgrades/hold` | Present while a new version waits for its health checks; the gateway holds application traffic only while `state.json` also says a start is pending |
| `.lab/upgrades/probe-token` | A random token for this start's health checks through the gateway, 0600; it exists only while the hold does |
| `.lab/upgrades/evidence-<time>/` | Evidence written on this server, copied aside before the checkout moved |
| `.lab/upgrades/aside-<time>/` | Local changes to tracked files, and untracked files the previous version would overwrite, copied here by a way back before it forced the checkout; one folder per way back. Nothing prunes them: remove one yourself once you no longer need it |
| `.lab/upgrades/upgrade.lock`, `channel.lock`, `check.log`, `apply.log`, `rollback.log` | The lock one upgrade or rollback holds, the lock of one release check or fetch, and the output of the last child of each kind |
| `.lab/upstream/worker-drain` | Present while the supervisor drains before an update: the worker claims no new job |
| `.lab/upstream/upgrade-intent.json` | Which pinned images the next start may replace, written by an upgrade or a way back |

`SBARBASE_RELEASE_SOURCE` names the Git repository the release check reads (a URL or a path); by default the canonical repository over HTTPS. Signatures are checked against `deploy/release-signers` of the running checkout whatever the source. `TZ` sets the zone the maintenance window is read in; `compose.yaml` passes both into the container (see [operator settings](#operator-settings)).

## State directories

Both are ignored by Git. Never print `.secrets/`, and never delete either to get past a refusal.

| Path | Holds |
|---|---|
| `.lab/upstream/control.sqlite` | The catalog: clients, projects, environments, memberships, jobs, routing |
| `.lab/upstream/server.json` | URL and PID of the running loopback server |
| `.lab/upstream/*.json` | Operation journals, recovery descriptors and probe outputs |
| `.lab/ui/` | The built console |
| `.lab/backups/` | Daily backups per environment, and the installation manifest of each run under `installation/` |
| `.lab/backups/upgrade-moved.json` | The backup runs of upgrades that moved the checkout. The last 3 of them are kept out of pruning; an upgrade try that stopped before the move keeps ordinary backups |
| `.secrets/upstream/runtime.json` | Generated credentials of the owned runtime |
| `.secrets/upstream/managed-keys.sqlite` | Hashed publishable key metadata |
| `.secrets/upstream/bootstrap.json` | Operator setup journal (no password) |
| `.secrets/offsite.json` | Off-site copy settings, the storage key and the passphrase ([backup and restore](../guides/backup-and-restore.md#copies-off-the-server)) |
| `.secrets/upstream/<runtime>-auth.json` | An environment's sign-in settings, including provider secrets, written by the console ([sign-in](../guides/sign-in.md)) |

## Operator settings

Set these in `compose.yaml` (Docker) or as `Environment=` lines of the systemd service.

| Variable | Default | Meaning |
|---|---|---|
| `SBARBASE_PUBLIC_URL` | `http://localhost` | The address people and OAuth providers reach this server at, such as `https://api.example.com`. Auth builds email links and OAuth callbacks from it. A change applies at the next start |
| `SBARBASE_CONSOLE_PORT` | chosen at start | The loopback port of the console and API, for a TLS proxy |
| `SBARBASE_BACKUP_HOUR` | `3` | Hour (UTC) of the daily backup; `off` stops it |
| `SBARBASE_BACKUP_KEEP` | `7` | Backups kept per environment. Backups taken for an upgrade are not counted: those of the last 3 upgrades that moved the checkout are always kept |
| `TZ` | UTC in the container | The zone the maintenance window of automatic updates is read in, for example `Asia/Dubai`. Outside Docker the server's own zone applies |
| `SBARBASE_RELEASE_SOURCE` | the canonical repository | Where the update check reads signed releases: a mirror's Git URL or path |
| `SBARBASE_DATABASE_PORT` | `6543` | Port of the direct database access listener ([database access](../guides/database-access.md)); `off` turns it off |
| `SBARBASE_DATABASE_BIND` | `127.0.0.1` | Address that listener binds. Keep loopback and use an SSH tunnel; another address sends database traffic unencrypted |
| `SBARBASE_UPLOAD_LIMIT_MB` | `50` | The largest file an application may upload to Storage, in MiB (1 to 5120), as Supabase's global file size limit. Larger uploads get `413`. A change applies at the next start, which recreates the shared Storage container; a bucket's own limit can be lower ([evidence](../evidence/docker-upload-checks.json)) |

## Internal environment variables

Other variables named `SBARBASE_*` (for example `SBARBASE_WORKER_FD`, `SBARBASE_EFFECT_TOKEN`) pass lock descriptors and tokens between Sbarbase's own processes. They are not operator settings; do not set them by hand.

## Not configurable yet

Resource tiers and admission thresholds are fixed in code ([lab/resource_policy.py](../../lab/resource_policy.py), [lab/pressure_admission.py](../../lab/pressure_admission.py)). Gateway concurrency limits and deadlines are constructor defaults in [src/gateway/concurrency.ts](../../src/gateway/concurrency.ts).
