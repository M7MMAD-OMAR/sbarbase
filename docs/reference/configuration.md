[العربية](configuration.ar.md)

# Configuration

What an operator can set, and where Sbarbase keeps its state. Paths are relative to the checkout. Sbarbase has no single configuration file: settings live in the files and flags below, and everything else is decided by code and pinned lock files.

## Host requirements

| Setting | Where | Notes |
|---|---|---|
| Docker endpoint | `DOCKER_HOST`, or `--docker-host` on `deploy/server-acceptance.sh` | Must reach a native Linux daemon. A system service does not inherit your shell, so a host whose Docker context points at a desktop socket must forward it in the unit |
| Bun | on `PATH`; `--bun-dir` for the service unit | Used for the console build and the loopback server |
| Python | `/usr/bin/python3`, 3.14 or newer | `--python` on `deploy/server-acceptance.sh` overrides it |

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
| `--max-body` | 1 MiB | Larger bodies get `413` |

## Operator bootstrap file

`/usr/bin/python3 lab/operator_file.py PATH` prompts (no echo) and writes a 0600 JSON file with exactly `email`, `password` and `organization`. `--stdin` reads the same object from stdin (at most 8192 bytes) for automation. Pass it to `install --bootstrap-file`, or run `lab/bootstrap.py` interactively instead. See [operator setup](../guides/operator-setup.md).

## Per-environment mail

Application mail (confirmation, recovery) for one environment is switched on by one file, `.secrets/upstream/<runtime>-mail.json`, mode 0600. Write, show or remove it with `lab/mail_config.py write|show|remove PATH`; the password is never printed. Keys: `host`, `port`, `user`, `pass`, `admin_email`, `sender_name`, `reply_to`, `max_frequency`, `otp_exp`, `otp_length`, `secure_email_change`, `autoconfirm`. A change is applied by reconciling that environment's Auth container. Full schema: [environment email](../engineering/ENVIRONMENT-EMAIL.md).

## Operator notifications

If `.lab/upstream/notifications.json` exists, the worker delivers operator events by email, by signed webhook or both:

```json
{
  "schema": 1,
  "email": {"enabled": true, "host": "smtp.example.invalid", "port": 587,
            "from": "sbarbase@example.invalid", "to": "operator@example.invalid", "tls": "starttls"},
  "webhook": {"enabled": true, "url": "https://hooks.example.invalid/sbarbase",
              "secretFile": ".secrets/upstream/notifier.json"}
}
```

`tls` is `none`, `starttls` or `tls`. The webhook signing secret lives in `.secrets/upstream/notifier.json` as `{"schema": 1, "webhookSecret": "<64 hex characters>"}`. Full design: [operator notifications](../engineering/OPERATOR-NOTIFICATIONS.md).

## State directories

Both are ignored by Git. Never print `.secrets/`, and never delete either to get past a refusal.

| Path | Holds |
|---|---|
| `.lab/upstream/control.sqlite` | The catalog: clients, projects, environments, memberships, jobs, routing |
| `.lab/upstream/server.json` | URL and PID of the running loopback server |
| `.lab/upstream/*.json` | Operation journals, recovery descriptors and probe outputs |
| `.lab/ui/` | The built console |
| `.secrets/upstream/runtime.json` | Generated credentials of the owned runtime |
| `.secrets/upstream/managed-keys.sqlite` | Hashed publishable key metadata |
| `.secrets/upstream/bootstrap.json` | Operator setup journal (no password) |

## Internal environment variables

Variables named `SBARBASE_*` (for example `SBARBASE_WORKER_FD`, `SBARBASE_EFFECT_TOKEN`) pass lock descriptors and tokens between Sbarbase's own processes. They are not operator settings; do not set them by hand.

## Not configurable yet

Resource tiers and admission thresholds are fixed in code ([lab/resource_policy.py](../../lab/resource_policy.py), [lab/pressure_admission.py](../../lab/pressure_admission.py)). Gateway concurrency limits and deadlines are constructor defaults in [src/gateway/concurrency.ts](../../src/gateway/concurrency.ts).
