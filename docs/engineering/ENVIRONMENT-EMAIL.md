# Per environment application email: Supabase Auth against a real SMTP server

Status: design record, 2026-09-21. Nothing here is implemented yet. Written by a
delegated design pass and reviewed by the parent the same day: the load bearing claims were
re-read against the code and the counts were reproduced (audit_events 224 and 8 in the two
catalogs, the environment Auth at 256m and 0.25 CPU, the database at 1024m and 1 CPU on one
volume, no blkio or cpu-shares anywhere in the tree, the pin count assertion at
lab/test_pinned_images.py:38). Unverified items are labelled unconfirmed in place.

## Parent review notes, 2026-09-21

Two facts were established after this design was written, and both change an assumption in it.

1. **A 0600 environment file does not hide a value from the Docker daemon.** Verified live on
   this host: a container created with `--env-file` pointing at a 0600 file, then
   `docker inspect --format '{{json .Config.Env}}'`, printed the value in cleartext. This is
   already true of every credential the runtime writes today, including each environment's
   database password and JWT secret, so it is the existing posture rather than a new hole. The
   consequence for this design: the file mode protects the value from other host users, and
   anyone who can reach the Docker daemon can read it. A mail credential must therefore stay
   scoped to one environment and revocable on its own, and no sentence may claim the file
   protects it from an operator with daemon access.

2. **The mailer is a test fixture, not a product component, so it does not enter the pin
   table.** `lab/install_server.py` walks every entry of the three lock files (`LOCKS` at line
   36, `pinned_images()` at lines 47 to 61) and requires each image to be present, so adding a
   test-only mailer to `lab/images.lock.json` would make every production install depend on a
   test image. The probes therefore carry the image reference as a constant holding both the
   tag and the expected local image id, and refuse to run when the local id differs, which
   keeps the anti-drift property without the install dependency. The pin table in
   `docs/engineering/UPSTREAM-UPDATE-POLICY.md` stays as it is.

3. **What an environment with no mail configuration does today is kept, deliberately.** With no
   mail file, the Auth environment stays exactly as it was: `GOTRUE_EXTERNAL_EMAIL_ENABLED` true
   and `GOTRUE_MAILER_AUTOCONFIRM` true, so a signup succeeds and is confirmed without any mail
   ever being sent (`lab/run.py`). The independent review treats automatic confirmation as a
   fatal criterion, and that disagreement is recorded here rather than smoothed over. The reason
   to keep it: flipping it would break every environment provisioned before this change on the
   day it lands, and an environment that cannot send mail could not confirm anyone either. An
   operator who wants the stricter posture sets `autoconfirm` false in that environment's mail
   configuration, which the configuration allows and the probe covers.

4. **The mail state reaches the console.** The runtime writes the non secret summary file
   (`lab/mail_state.py`), which is the single source: `GET /management/v1/environments/<uuid>/mail`
   (`src/control/http.ts`) serves that environment's entry to an owner, admin or viewer, and the
   environment surface renders it (`ui/Connection.tsx`). The `environment_mail` table this
   document planned was never written and never read, so it was removed from
   `src/control/catalog.ts` rather than gaining a writer.


Design document for sbarbase. Executable: every section that changes behaviour names
the file, the exact variable and the command that proves it. Written 2026-09-21.

Rules applied to this document: no em dashes or en dashes anywhere, Western digits,
English, `bun` only and never `npm`. No credential value appears here. A variable name
that could not be confirmed for the pinned version is written as **unconfirmed** with
the place that was searched.

## 0. Pinned versions this design is written against

| Component | Pin | Source |
|---|---|---|
| Auth (GoTrue) | `public.ecr.aws/supabase/gotrue:v2.196.0`, digest `sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232` | `lab/images.lock.json` key `auth`, mirrored in the pin table at `docs/engineering/UPSTREAM-UPDATE-POLICY.md:52` |
| Supabase PostgreSQL (distribution) | `public.ecr.aws/supabase/postgres:17.6.1.166` | `docs/engineering/UPSTREAM-UPDATE-POLICY.md:50` |
| Mailpit (proposed new pin, for local verification only) | `public.ecr.aws/supabase/mailpit:v1.30.2`, digest `sha256:37a38e48e9338cd7e89dfeb487f37b02ebfcd9cb23111bed2d345e79d37d6dd6` | present on this host, confirmed with `docker image inspect`; see section 6.1 |

Upstream source used for every variable name below: the tag matching the pin,
`github.com/supabase/auth` tag `v2.196.0`, commit
`0204331ca41a5b49f076b6fa3dc6c0d20b996590`. Files cited by path and line:

- `example.env` at tag `v2.196.0` (the repository's own environment template, 10314 bytes).
- `internal/conf/configuration.go`.
- `internal/conf/rate.go`.
- `internal/api/apilimiter/apilimiter.go`.
- `internal/api/mail.go` and `internal/api/signup.go`.
- `internal/mailer/templatemailer/template.go` and `.../templatemailer.go`.
- `internal/mailer/mailmeclient/mailmeclient.go`.
- `go.mod` line 33.

The SMTP client behind that tag is `gopkg.in/gomail.v2 v2.0.0-20160411212932-81ebce5c23df`
(`go.mod:33`), resolved to commit `81ebce5c23df`. Its dial behaviour is quoted in section 5.1
because it constrains the port and the certificate an operator may supply.

## 1. What exists today and what is missing

### 1.1 The Auth environment the runtime writes today

The single shared builder for every Auth process is `lab/run.py:121-130`:

```python
def auth_configuration(e, v, database_host):
    return {
        'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999',
        'API_EXTERNAL_URL': f'http://localhost/{e}/auth/v1',
        'GOTRUE_SITE_URL': 'http://localhost', 'GOTRUE_DB_DRIVER': 'postgres',
        'GOTRUE_DB_DATABASE_URL': f'postgres://{e}_auth:{v["auth"]}@{database_host}:5432/{e}',
        'GOTRUE_JWT_SECRET': v['jwt'], 'GOTRUE_JWT_AUD': 'authenticated',
        'GOTRUE_JWT_DEFAULT_GROUP_NAME': 'authenticated', 'GOTRUE_JWT_ADMIN_ROLES': 'service_role',
        'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'true',
        'GOTRUE_DB_MAX_POOL_SIZE': '3', 'GOTRUE_DB_NAMESPACE': 'auth'}
```

Exact current values that matter for email, quoted as they are written today:

- `GOTRUE_EXTERNAL_EMAIL_ENABLED`: `'true'`. The email provider is on, so `/signup`,
  `/recover`, `/magiclink` and `/otp` are reachable (upstream `internal/api/api.go:229-265`).
- `GOTRUE_MAILER_AUTOCONFIRM`: `'true'`. Email confirmation is off, so signup returns a
  session immediately and no confirmation message is ever produced.
- `GOTRUE_SITE_URL`: `'http://localhost'`.
- `API_EXTERNAL_URL`: `f'http://localhost/{e}/auth/v1'`.

Absent from the dict, and therefore absent from every container environment today:
`GOTRUE_SMTP_HOST`, `GOTRUE_SMTP_PORT`, `GOTRUE_SMTP_USER`, `GOTRUE_SMTP_PASS`,
`GOTRUE_SMTP_ADMIN_EMAIL`, `GOTRUE_SMTP_SENDER_NAME`, `GOTRUE_SMTP_HEADERS`,
`GOTRUE_SMTP_MAX_FREQUENCY`, `GOTRUE_SMTP_LOGGING_ENABLED`, `GOTRUE_MAILER_OTP_EXP`,
`GOTRUE_MAILER_OTP_LENGTH`, `GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED`,
`GOTRUE_MAILER_SUBJECTS_*`, `GOTRUE_MAILER_TEMPLATES_*`, `GOTRUE_MAILER_URLPATHS_*`,
`GOTRUE_MAILER_NOTIFICATIONS_*`, `GOTRUE_RATE_LIMIT_EMAIL_SENT`, `GOTRUE_RATE_LIMIT_OTP`,
`GOTRUE_RATE_LIMIT_VERIFY`, `GOTRUE_RATE_LIMIT_TOKEN_REFRESH`, `GOTRUE_RATE_LIMIT_HEADER`.

Absent is exactly one variable type and it is the decisive one: no `GOTRUE_SMTP_*` key is
ever set, so upstream selects the noop mail client. `internal/mailer/templatemailer/template.go:41-46`:

```go
func FromConfig(globalConfig *conf.GlobalConfiguration, tc *Cache) *Mailer {
	var mc mailer.Client
	if globalConfig.SMTP.Host == "" {
		logrus.Infof("Noop mail client being used for %v", globalConfig.SiteURL)
		mc = noopclient.New()
	} else {
		mc = mailmeclient.New(globalConfig)
	}
```

and `internal/mailer/noopclient/noopclient.go` returns `nil` from `Mail(...)` when the
recipient is non-empty. That is why the platform has never needed mail: mail is silently
discarded, with an info log line, and every endpoint that would send mail still succeeds.
That is the behaviour the disabled posture must preserve byte for byte.

Two consumers of the builder exist:

- The durable runtime, `lab/durable_runtime.py:309`, for the two services of an environment:
  `for service, builder, port, suffix in [('auth', lab.auth_configuration, 9999, '/health'), ('rest', lab.rest_configuration, 3000, '/')]:`.
- The dedicated management realm, `lab/durable_runtime.py:352-354`, which deliberately
  overrides the same dict:

```python
config = lab.auth_configuration('management', values, DB)
config.update({'GOTRUE_DISABLE_SIGNUP': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'false',
               'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'false'})
```

The management realm sets `GOTRUE_MAILER_AUTOCONFIRM` to `'false'` and still sends nothing,
because its `GOTRUE_SMTP_HOST` is also empty. The management realm must stay that way: it
is the operator's own identity realm, not an application.

The component lab's four fixtures use the same builder (`lab/run.py:143`), and
`lab/distro-check.py:71-76` builds its own inline dict. Neither is an application
environment reached through the console, and neither is in scope for this change.

### 1.2 The runtime key layout, from `lab/durable_runtime.py`

`.secrets/upstream/runtime.json` is the generated secret file. Its keys are created in
`lab/durable_runtime.py:104-109` and `:292-294` and are read as `self.values`:

- Top level: `admin`, `storage_control`, `storage_admin`, `encryption`. Each is
  `secrets.token_hex(32)` (`:105`).
- `management`: `{'auth': token_hex(32), 'jwt': token_hex(32)}` (`:108`).
- `environments`: a dict keyed by the environment runtime identifier, which the runtime
  requires to match `e_[a-f0-9]{24}` (`:163`, `:229`, `:244`, `:271`). Each value is
  `{k: secrets.token_hex(32) for k in ('auth', 'rest', 'storage', 'jwt')}` (`:293`).

Value `v` of one environment is what the builder receives: `v['auth']`, `v['rest']`,
`v['storage']`, `v['jwt']`. Nothing in this file is operator supplied, and nothing in it is
printed: the module's own comment at `lab/durable_runtime.py:397` reads
`# Secrets, SQL and HTTP response bodies must never reach console output.` and the top level
handler re-raises as `SystemExit('Durable runtime operation failed; retained state is available for reconciliation.')`.

Generated values are not the right place for an operator supplied SMTP password. Section 2.2
puts it beside this file instead.

### 1.3 How secrets are stored today

Generated runtime state, in the ignored `.secrets/` tree (`lab/run.py:13-15`:
`ROOT = Path(__file__).resolve().parents[1]`, `STATE = ROOT / '.lab'`, `PRIVATE = ROOT / '.secrets'`),
with `.secrets/` and `.lab/` both git-ignored (`.gitignore:1-2`).

Every launched container gets its environment written to a mode 0600 file under
`.secrets/upstream/` (`lab/durable_runtime.py:131-135`):

```python
path = PRIVATE/(name+'.env')
lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
args = ['run', '-d', '--name', name, '--label', 'io.sbarbase.owner='+OWNER, '--network', NETWORK,
        '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', '128',
        '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(path)]
```

`lab.secure_file` opens with mode 0600 (`lab/run.py:39-42`). With `PREFIX = 'sbarbase-durable'`
(`lab/durable_runtime.py:29`), the per environment service env files that exist today are,
names only and never contents:

```
.secrets/upstream/sbarbase-durable-e_<24 hex>-auth.env
.secrets/upstream/sbarbase-durable-e_<24 hex>-rest.env
.secrets/upstream/sbarbase-durable-management-auth.env
.secrets/upstream/sbarbase-durable-db.env
.secrets/upstream/sbarbase-durable-storage.env
.secrets/upstream/runtime.json
.secrets/upstream/bootstrap.json, bootstrap.lock
.secrets/upstream/bootstrap-check-<6 chars>/ (one directory per probe run)
.secrets/upstream/recovery-<16 hex>.key, managed-keys.sqlite
```

The durable runtime refuses to persist anything before proving the ignore rule holds
(`lab/durable_runtime.py:90-93`), and `lab/bootstrap.py:19-20` does the same for its own
journal with `git check-ignore`. Any new operator supplied secret file must repeat that check.

Also relevant: the runtime refuses configuration drift. `lab/durable_runtime.py:119-123`:

```python
if actual:
    expected = json.loads(lab.docker('image', 'inspect', image).stdout)[0]['Id']
    configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
    if actual['Image'] != expected or any(configured.get(k) != v for k, v in env.items()):
        raise RuntimeError('Runtime drift requires explicit reconciliation')
```

This comparison is one directional: it iterates the desired keys only. Adding a key trips it,
removing a key does not. Section 2.6 turns that into a stated hazard rather than a surprise.

### 1.4 What the operator bootstrap does today, and why it avoids email

`lab/bootstrap.py` is the only onboarding path. It takes `email`, `password` and
`organization` by no-echo prompt or bounded stdin (`lab/bootstrap.py:12-13`, `:28-41`), holds
an exclusive lock (`:21-26`), and hands the payload to `bun lab/bootstrap.ts` over stdin with
the lock file descriptor passed as an inherited fd (`:44-46`). It prints nothing but the exit
code.

`lab/bootstrap-auth.ts` then creates the operator through the management realm's private
admin API with the email already confirmed:
`admin.auth.admin.createUser({email,password,email_confirm:true,app_metadata:{sbarbase_bootstrap:operation}})`.
No invite mail is sent and none is needed, and `docs/guides/operator-setup.md:64-65` states the
position explicitly: "The setup command does not email an invitation; its private admin
creation explicitly confirms the operator-supplied email."

The reason is structural, not accidental. The management realm sets signup disabled and
autoconfirm false but has no SMTP host, so it can never mail anything. Bootstrap therefore
cannot depend on a working relay, and the first operator exists on a host that has no mail
configuration. Any design that made bootstrap depend on SMTP would break the only way an
installation gets its first identity. This design keeps that property: the management realm
never gains an SMTP host.

### 1.5 The gap

1. No variable of the `GOTRUE_SMTP_*` family exists anywhere in the repository. Confirmed
   with a case-insensitive search for `smtp`, `mailer`, `mailpit`, `MAIL_` across `*.py`,
   `*.ts`, `*.json`, `*.md` excluding `node_modules`: the only hits are the two
   `GOTRUE_MAILER_AUTOCONFIRM` values, `GOTRUE_EXTERNAL_EMAIL_ENABLED`, and `email_confirm`
   in the bootstrap probes.
2. No operator input path for a mail provider exists. `lab/operator_file.py` is the only
   precedent for an operator supplied secret file, and it writes the bootstrap identity only.
3. No per environment switch exists. Every environment shares one builder and one
   `runtime.json` shape, so there is no place to record "this environment sends mail".
4. No console surface reports mail state.
5. No verification harness proves a message was actually delivered, and none proves that
   enabling mail for one environment leaves the others unchanged.
6. The rate limits that bound mail are absent, so upstream defaults apply silently and
   differently from the example configuration (`GOTRUE_RATE_LIMIT_EMAIL_SENT` defaults to 30
   per hour, while `example.env` suggests 100).

## 2. The per environment configuration to add

### 2.1 Design rules

1. **One environment at a time.** The switch is a per environment file plus an explicit
   reconcile of that environment's Auth container. No shared, installation wide setting for
   application mail exists, and none may be introduced later, because it would make one
   environment's abuse spend another environment's provider quota. This is about application
   mail, which Auth sends from inside each environment's own process; the separate,
   installation scoped sender that document `03-notifications.md` designs for operator alerts
   is a different sender with a different purpose, and section 11 fixes the boundary.
2. **Absent means identical.** When no mail file exists for an environment, the builder must
   return a dict equal to today's dict. This is asserted by unit test, not by inspection.
3. **Operator supplied secrets never enter `runtime.json`.** That file is generated state.
   The SMTP password goes in its own 0600 file beside the environment's generated env file.
4. **Startup only.** Auth reads SMTP configuration once at process start
   (`internal/mailer/templatemailer/template.go:40-58` is called once from
   `internal/api/api.go:128-130`). There is no supported runtime reload of the mailer for
   this pin, so a mail change is a container reconcile, not a live patch.
5. **The password never crosses the management API.** No HTTP path accepts or returns it.

### 2.2 The operator supplied file

New file, one per environment, in the ignored secrets tree. Names only:

```
.secrets/upstream/<environment runtime id>-mail.json
```

Example, for environment `e_1f0624c545789214eef426c9`:

```
.secrets/upstream/e_1f0624c545789214eef426c9-mail.json
```

Mode 0600, written by a new tool that mirrors `lab/operator_file.py` in shape: absolute path
required, parent must exist, symlink refused, `O_CREAT|O_EXCL` unless `--force`, at most 8192
bytes on stdin, no password echoed, values never printed. New file:

```
lab/mail_config.py
```

Content schema, exactly these keys, no others:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `host` | string, non-empty, no whitespace | yes | SMTP host. Must be a name with a certificate valid for it if the relay offers STARTTLS or uses port 465, see section 5.1 |
| `port` | integer, 1 to 65535 | yes | SMTP port |
| `user` | string | yes | SMTP user (may be an empty string only when the relay allows anonymous submission) |
| `pass` | string | yes | SMTP password or provider API key |
| `admin_email` | single address, no whitespace | yes | becomes the envelope From and the `From` header |
| `sender_name` | string | yes, may be empty | display name in `From` |
| `reply_to` | single address or empty | yes | becomes an SMTP header through `GOTRUE_SMTP_HEADERS` |
| `max_frequency` | Go duration string, for example `60s` | yes | per user minimum interval between two mails of the same kind |
| `otp_exp` | integer seconds | yes | lifetime of a confirmation or recovery token |
| `otp_length` | integer 6 to 10 | yes | length of the numeric OTP in the mail body |
| `secure_email_change` | boolean | yes | require confirmation from both the old and the new address |
| `autoconfirm` | boolean | yes | false means confirmation mail is required before a session |
| `rate_limit_email_sent` | string or integer, see section 3 | yes | emails per hour for the process |
| `rate_limit_otp` | integer | yes | per 5 minute bucket for signup, recover, resend, magiclink, otp, user |
| `rate_limit_verify` | integer | yes | per 5 minute bucket for token verification |
| `rate_limit_header` | string, may be empty | yes | header carrying the trusted client address |

Command surface of `lab/mail_config.py`:

```
/usr/bin/python3 lab/mail_config.py write .secrets/upstream/e_1f0624c545789214eef426c9-mail.json
/usr/bin/python3 lab/mail_config.py write <path> --stdin --force
/usr/bin/python3 lab/mail_config.py show <path>
/usr/bin/python3 lab/mail_config.py remove <path>
```

`write` validates the schema and refuses: a missing or extra key, a bad type, a bad address
shape, a port out of range, an `otp_length` outside 6 to 10, a `host` containing whitespace,
a directory or symlink target, an unwritable parent, and an existing file without `--force`.
It never prints a value, only the path and the resulting mode, and it refuses to proceed
unless `git check-ignore -q <path>` succeeds, the same guard the runtime uses at
`lab/durable_runtime.py:92`.

`show` prints the non-secret fields and the literal string `pass set` or `pass empty`, never
the value. That is the only sanctioned way to display the configuration.

### 2.3 The builder change

`lab/run.py:121` becomes `def auth_configuration(e, v, database_host, mail=None):` and gains a
block that runs only when `mail` is not None. `lab/rest_configuration` keeps its 3 argument
signature so `lab/durable_runtime.py:309` and `lab/run.py:144` continue to work.

```
mail = None -> dict unchanged, byte for byte
mail set    -> the dict plus exactly these keys:

GOTRUE_SMTP_HOST                     mail['host']
GOTRUE_SMTP_PORT                     str(mail['port'])
GOTRUE_SMTP_USER                     mail['user']
GOTRUE_SMTP_PASS                     mail['pass']
GOTRUE_SMTP_ADMIN_EMAIL              mail['admin_email']
GOTRUE_SMTP_SENDER_NAME              mail['sender_name']
GOTRUE_SMTP_HEADERS                  json object, see below
GOTRUE_SMTP_MAX_FREQUENCY            mail['max_frequency']
GOTRUE_SMTP_LOGGING_ENABLED          'false'
GOTRUE_MAILER_AUTOCONFIRM            'true' or 'false' from mail['autoconfirm']
GOTRUE_MAILER_OTP_EXP                str(mail['otp_exp'])
GOTRUE_MAILER_OTP_LENGTH             str(mail['otp_length'])
GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED  'true' or 'false'
GOTRUE_RATE_LIMIT_EMAIL_SENT         mail['rate_limit_email_sent']
GOTRUE_RATE_LIMIT_OTP                str(mail['rate_limit_otp'])
GOTRUE_RATE_LIMIT_VERIFY             str(mail['rate_limit_verify'])
GOTRUE_RATE_LIMIT_HEADER             mail['rate_limit_header']
```

`GOTRUE_SMTP_HEADERS` is JSON because upstream parses it that way.
`internal/conf/configuration.go:580` declares `Headers string`, and
`buildNormalizedHeaders` does `json.Unmarshal([]byte(c.Headers), &val)` into
`map[string][]string` at `:595-613`, ignoring invalid JSON with a warning. `Reply-To` is not a
first class variable in this pin, so it is carried here:

```
GOTRUE_SMTP_HEADERS = {"Reply-To":["<mail['reply_to']>"]}
```

Omit the key entirely when `reply_to` is empty, so the header is not sent as an empty value.

Where the value comes from at each call site:

- `lab/durable_runtime.py:309`: `lab.auth_configuration(e, v, DB, mail_config.load(e))`.
- `lab/durable_runtime.py:352`: unchanged, three arguments. Written deliberately with a
  comment that the management realm never gains a mail configuration.
- `lab/run.py:143` (component lab fixtures): unchanged, three arguments.
- `lab/distro-check.py:71-76`: unchanged, its own inline dict with autoconfirm true.

`mail_config.load(runtime_id)` returns the parsed dict when
`.secrets/upstream/<runtime_id>-mail.json` exists and passes schema validation, and `None`
when it does not. It must raise, never fall back to a default, when the file exists but is
invalid: a half configured relay that silently becomes the noop client is worse than a
refused start.

### 2.4 Variable table with upstream defaults and semantics

Every name below was confirmed against tag `v2.196.0`, either in `example.env` of that tag or
in the struct/field declaration cited. Defaults are the `default:"..."` struct tags or the
`ApplyDefaults` assignments, so they are what a container without the variable actually gets.

| Variable | Confirmed where | Upstream default | Semantics for this pin |
|---|---|---|---|
| `GOTRUE_SMTP_HOST` | `example.env`, `configuration.go:575` | empty string | Empty selects the noop client at `templatemailer/template.go:42`. Non-empty selects the gomail client. |
| `GOTRUE_SMTP_PORT` | `example.env`, `configuration.go:576` | `587` | Port passed to `gomail.NewDialer`. Port 465 switches that dialer to implicit TLS (`gomail` `smtp.go:46`). |
| `GOTRUE_SMTP_USER` | `example.env`, `configuration.go:577` | empty string | With an empty user, gomail attempts no AUTH at all (`gomail` `smtp.go:90`). |
| `GOTRUE_SMTP_PASS` | `example.env`, `configuration.go:578` | empty string | Password or API key. |
| `GOTRUE_SMTP_ADMIN_EMAIL` | `example.env`, `configuration.go:579` | empty string | Combined with `sender_name` into the `From` address by `FormatAddress` at `configuration.go:588-591`. |
| `GOTRUE_SMTP_SENDER_NAME` | `example.env`, `configuration.go:580` | empty string | Display name only. |
| `GOTRUE_SMTP_HEADERS` | `configuration.go:581` (`Headers`), `example.env` omits it | empty string | JSON object of headers. Used for `Reply-To`. |
| `GOTRUE_SMTP_MAX_FREQUENCY` | `example.env`, `configuration.go:574`, enforced at `internal/api/mail.go:325,401,443,486,527` | `1` minute, set at `configuration.go:1180-1182` | Minimum interval before a second mail of the same kind to the same user. Enforced by `validateSentWithinFrequencyLimit`, which uses the stored `confirmation_sent_at`, `recovery_sent_at` and `email_change_sent_at` columns. Over the limit returns 429 `over_email_send_rate_limit` (`mail.go:341`). |
| `GOTRUE_SMTP_LOGGING_ENABLED` | `configuration.go:582`, `example.env` omits it | `false` | When true, `mailmeclient` logs one `mail.send` record per message including `mail_from` and `mail_to` (`mailmeclient.go:72-85`). |
| `GOTRUE_MAILER_AUTOCONFIRM` | `example.env`, `configuration.go:623` | `false` (zero value) | True suppresses the confirmation mail entirely and returns a session from signup. |
| `GOTRUE_MAILER_ALLOW_UNVERIFIED_EMAIL_SIGN_INS` | `example.env`, `configuration.go:624` | `false` | Setting both this and autoconfirm is a startup error (`configuration.go:1151-1153`). Leave unset. |
| `GOTRUE_MAILER_OTP_EXP` | `configuration.go:633`, default applied at `:1171-1173` | `86400` seconds | Token lifetime. Not in `example.env`. |
| `GOTRUE_MAILER_OTP_LENGTH` | `example.env` has it under SMS only, `configuration.go:634` | `6`, clamped to 6 to 10 at `:1175-1178` | Digits in the mailed OTP. |
| `GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED` | `example.env`, `configuration.go:631` | `true` | Requires confirmation from both the current and the new address on email change. |
| `GOTRUE_MAILER_URLPATHS_CONFIRMATION` | `example.env` | `/verify` (`configuration.go:1159-1161`) | Path the action link points at. Leave at the default unless the gateway serves another path. |
| `GOTRUE_MAILER_URLPATHS_INVITE` | `example.env` | `/verify` | as above |
| `GOTRUE_MAILER_URLPATHS_RECOVERY` | `example.env` | `/verify` | as above |
| `GOTRUE_MAILER_URLPATHS_EMAIL_CHANGE` | `example.env` | `/verify` | as above |
| `GOTRUE_MAILER_SUBJECTS_*` | `example.env`, `configuration.go:626` | the strings in `templatemailer.go:127-141` | Inline subject templates, one key per mail type. |
| `GOTRUE_MAILER_TEMPLATES_*` | `example.env`, `configuration.go:627` | empty string | URL of the body template, not a folder. Section 4. |
| `GOTRUE_MAILER_NOTIFICATIONS_*_ENABLED` | `example.env` | `false` for all seven | Account change notifications. Leave off in the first increment. |
| `GOTRUE_MAILER_TEMPLATE_MAX_SIZE` | `configuration.go:646` | `1000000` bytes | Read limit when fetching a body template. |
| `GOTRUE_MAILER_TEMPLATE_MAX_AGE` | `configuration.go:649` | `10m` | Cache lifetime of a fetched body template. |
| `GOTRUE_MAILER_TEMPLATE_RETRY_INTERVAL` | `configuration.go:652` | `10s` | Retry interval after a failed fetch. |
| `GOTRUE_MAILER_TEMPLATE_RELOADING_ENABLED` | `configuration.go:656` | `false` | Background reloading. |
| `GOTRUE_MAILER_EMAIL_BACKGROUND_SENDING` | `configuration.go:639` | `false` | True makes the mailer asynchronous, see section 7. |
| `GOTRUE_RATE_LIMIT_EMAIL_SENT` | `example.env`, `configuration.go:435` | `30` | See section 3. Accepts `N` or `N/over-time` (`internal/conf/rate.go:32-60`). |
| `GOTRUE_RATE_LIMIT_OTP` | `example.env` does not list it, `configuration.go:441` | `30` | Governs signup, recover, resend, magiclink, otp and user endpoints, see section 3. |
| `GOTRUE_RATE_LIMIT_VERIFY` | `configuration.go:437` | `30` | Token verification. |
| `GOTRUE_RATE_LIMIT_TOKEN_REFRESH` | `configuration.go:438` | `150` | Token refresh. |
| `GOTRUE_RATE_LIMIT_HEADER` | `example.env`, `configuration.go:434` | empty string | Header trusted as the client address. |
| `GOTRUE_EXTERNAL_EMAIL_ENABLED` | `example.env`, `configuration.go:547` (`Email EmailProviderConfiguration` inside `ProviderConfiguration` at `:423`, struct at `:103`) | the value in `example.env` is `true` | The email provider. Already `'true'` in sbarbase today. |

**Verified against the pinned tag, 2026-09-21.** Both items this section left
unconfirmed were re-read from the tag's own source files and are settled:

- **No variable sets a templates directory, and a template value is an HTTP URL,
  not a file.** The tag's `example.env` lists twelve template variables
  (`GOTRUE_MAILER_TEMPLATES_INVITE`, `_CONFIRMATION`, `_RECOVERY`, `_MAGIC_LINK`,
  `_EMAIL_CHANGE`, and seven `*_NOTIFICATION` kinds) and twelve matching
  `GOTRUE_MAILER_SUBJECTS_*` variables. What a value does is decided in
  `internal/mailer/templatemailer/template.go`: an empty value returns the body
  compiled into the binary (`:450-453`), a value that does not start with `http` is
  prefixed with `SiteURL` and fetched anyway (`:455-457`), and the body is then
  retrieved by HTTP GET under a ten second timeout with a cache in front of it
  (`:459-471`, `:473-490`). A relative value is therefore a URL relative to the
  site URL, never a path on disk.
- **The reauthentication variables exist in the code and not in the tag's
  `example.env`.** `EmailContentConfiguration` declares `Reauthentication`
  (`configuration.go:499`) and the template machinery handles
  `ReauthenticationTemplate` and reads that field (`template.go:82`, `:532-533`), so
  `GOTRUE_MAILER_TEMPLATES_REAUTHENTICATION` and
  `GOTRUE_MAILER_SUBJECTS_REAUTHENTICATION` are accepted by name. The tag ships no
  example value for either, so there is nothing to copy and the compiled default
  body and subject apply. Both stay unset here.

The consequence, and it is why this design ships no template override: the runtime
network is `--internal`, so a template URL is fetched from inside that network and
an external one fails the send with `template_body_http_error`. The compiled
defaults keep the send path free of an outbound fetch, and an operator who wants
custom bodies has to serve them from inside the runtime network.

### 2.5 Disabled by default, proven

The posture has three parts.

1. No file, no keys. `mail_config.load(e)` returns `None`, the builder adds nothing, and the
   container environment is identical to today. Proven by a unit test in a new file
   `lab/test_mail_config.py`:

```
the builder without a mail argument and the builder with None produce equal dicts
the same equality holds for the management realm's three argument call
no key of the returned dict starts with GOTRUE_SMTP_
GOTRUE_MAILER_AUTOCONFIRM is still 'true' in the unconfigured dict
an invalid mail file raises instead of returning None
an absent mail file returns None
```

2. Existing environments are untouched by the change. The builder change adds an optional
   parameter; every existing call site keeps its current behaviour, and the runtime's drift
   guard (`lab/durable_runtime.py:119-123`) continues to accept retained containers.
   Proven by running the existing suites in section 8 steps 3 and 4 and by the reconcile
   test in section 6.4.

3. The switch is per environment, not installation wide. There is no installation level mail
   key anywhere in the design: no `runtime.json` field, no catalog column on `organizations`
   or `projects`, and no environment variable read by the API or the worker. The only switch
   is the presence of one per environment file.

### 2.6 The reconcile path, and the drift hole it closes

A per environment mail change is applied by a new operation in `lab/durable_runtime.py`:

```
Runtime.reconcile_mail(e)
```

Contract:

1. Require the owned runtime running and the environment published, reusing the checks in
   `resume` (`lab/durable_runtime.py:225-240`).
2. Refuse when `source_fence.is_fenced(self.sql, e)` is true, for the reason given at `:234-235`.
3. Read the desired mail configuration with `mail_config.load(e)`.
4. Inspect the existing Auth container and compare the mail relevant keys in both directions,
   not only the desired direction: the set of keys starting with `GOTRUE_SMTP_`,
   `GOTRUE_MAILER_` or `GOTRUE_RATE_LIMIT_` that the container has must equal the desired set,
   including removals. This closes the hole described in section 1.3, where the existing one
   directional comparison at `:122` would leave old SMTP values in a recreated container if a
   key were ever dropped from the desired dict.
5. When nothing differs, return without touching the container and report "unchanged". This
   makes the command idempotent, which the supervisor and the operator both need.
6. When something differs: `docker rm -f` the owned Auth container only, after an ownership
   check through `inspect('container', name)` (`:49-64` rejects a foreign owner), then call
   `self.launch(name, 'auth', builder(e, v, DB, mail), '256m', .25)` and `self.wait(endpoint+'/health')`.
   The environment database holds all Auth state, so recreating the container process loses
   nothing; the durable lifecycle already retains Auth data across container recreation.
7. Record the outcome as an audit event through the existing catalog audit table, and write
   the non-secret summary for the console (section 5.3).
8. Never print a value. On any failure re-raise into the module's existing single message.

Exposed as a subcommand beside the existing ones (`lab/durable_runtime.py:370`
currently allows `up`, `stop`, `provision`):

```
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id>
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id> --off
```

`--off` applies the empty configuration: the container is recreated without any `GOTRUE_SMTP_*`
key, which returns that environment to the noop client. This is the only way to stop mail for
an environment, and it is why step 4 above must compare removals.

Sequencing note that must be respected: the operation holds
`.lab/upstream/operation.lock` the way `provision` does (`:380-381`) and must not run while
`lab/dev.py` or a lifecycle check owns the runtime. `lab/README.md:101` already says do not run
manual lifecycle commands concurrently with the runner.

## 3. Rate limits and abuse controls

### 3.1 Upstream defaults, named

| Variable | Default for this pin | Enforcement site |
|---|---|---|
| `GOTRUE_RATE_LIMIT_EMAIL_SENT` | 30, interpreted as 30 events per 1 hour because `conf.Rate.Decode` applies `defaultOverTime = time.Hour` (`internal/conf/rate.go:10,32-40`) | `internal/api/apilimiter/apilimiter.go:189` builds the counter, `internal/api/mail.go:792-806` applies it, process wide with no client key, called as `a.limiterOpts.Email.Allow()` |
| `GOTRUE_RATE_LIMIT_OTP` | 30 | `internal/api/apilimiter/apilimiter.go:233-238` builds `Recover`, `Resend`, `MagicLink`, `Otp`, `User` and `Signups` from this one value through `newLimiterPer5mOver1h` (defined at `:358`), that is rate 30 per 5 minutes, 1 hour expiry, burst 30 |
| `GOTRUE_RATE_LIMIT_VERIFY` | 30 | `apilimiter.go:202-205`, tollbooth limiter, burst 30 |
| `GOTRUE_RATE_LIMIT_TOKEN_REFRESH` | 150 | `apilimiter.go:197-200`, burst 30 |
| `GOTRUE_SMTP_MAX_FREQUENCY` | 1 minute | per user, `internal/api/mail.go:325,401,443,486,527` |

The mapping from variable to endpoint is explicit in
`apilimiter.go:79-100` (`tollboothFieldsToEnv`): `fieldSignups`, `fieldRecover`, `fieldResend`,
`fieldMagicLink`, `fieldOtp` and `fieldUser` all map to `GOTRUE_RATE_LIMIT_OTP`.

`GOTRUE_RATE_LIMIT_HEADER` matters for how those buckets are keyed. It names the header that
carries the client address. Two consequences for sbarbase:

- Today no gateway sets a trusted client address header for Auth, so leaving the variable
  empty makes the bucket key the direct peer address, which is the gateway for every request
  from every visitor. One visitor's signup attempts then consume the whole environment's
  bucket.
- Setting it to a header an untrusted client can send is worse than leaving it empty, because
  the limit becomes attacker chosen. Only set it once the gateway strips any client supplied
  copy of that header and writes its own.

### 3.2 Values to set per environment

| Variable | Value | Reason |
|---|---|---|
| `GOTRUE_RATE_LIMIT_EMAIL_SENT` | `30` (keep the default) | 30 mails per hour for a whole environment is the process wide ceiling on provider spend. Raise only with the provider's own quota in front of it. |
| `GOTRUE_RATE_LIMIT_OTP` | `30` (keep the default), lower to `10` for a small application | Bounds signup, recover, resend, magiclink and otp per bucket. |
| `GOTRUE_RATE_LIMIT_VERIFY` | `30` (keep the default) | Bounds token verification brute force. |
| `GOTRUE_SMTP_MAX_FREQUENCY` | `60s` (the default) | Stops a single account from being mailed repeatedly. |
| `GOTRUE_MAILER_OTP_EXP` | `3600` | One hour instead of the default 86400. A confirmation link is usually clicked at once and a shorter life shrinks the window for a leaked mail. |
| `GOTRUE_MAILER_OTP_LENGTH` | `6` (the default) | Combined with `rate_limit_verify` this is the brute force margin: 6 digits, 30 verifications per bucket. |
| `GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED` | `true` (the default) | An email change requires both addresses. Turning it off lets a stolen session move the account to an attacker address in one step. |
| `GOTRUE_SMTP_LOGGING_ENABLED` | `false` (the default) | The only upstream log line about mail records the recipient address. Do not add that to container logs. |
| `GOTRUE_MAILER_EMAIL_BACKGROUND_SENDING` | `false` until section 7 is decided | See section 7. |

`GOTRUE_RATE_LIMIT_EMAIL_SENT=0` deserves a warning because it looks like "unlimited" and is
the opposite. `internal/api/mail.go:792-800`:

```go
	// if the number of events is set to zero, we immediately apply rate limits.
	if config.RateLimitEmailSent.Events == 0 {
		emailRateLimitCounter.Add(...)
		return EmailRateLimitExceeded
	}
```

Zero rejects every mail attempt with 429. The tool `lab/mail_config.py` must therefore accept
zero and warn, rather than treat it as a valid "off switch". Turning mail off is done by
removing the mail configuration, not by setting a number.

### 3.3 What an unbounded setting would allow

- `GOTRUE_RATE_LIMIT_EMAIL_SENT` at a very large number, or absent with a permissive rewrite:
  an attacker who can reach `/recover` or `/signup` triggers mails without a process wide
  ceiling. The environment drains the installation's provider quota, and the shared sending
  domain accumulates complaints that jeopardise delivery for every other environment on the
  same relay. Because each environment is a separate Auth process, this spend is contained to
  one environment's configuration, but it is not contained on the provider side if
  environments share a relay identity. That is a reason to require a distinct sending
  identity, at minimum a distinct `admin_email`, per environment.
- `GOTRUE_RATE_LIMIT_OTP` unbounded: unlimited signup and recovery attempts per client, which
  is both user enumeration and mail amplification.
- `GOTRUE_RATE_LIMIT_VERIFY` unbounded: a 6 digit OTP becomes brute forceable inside its
  lifetime.
- `GOTRUE_SMTP_MAX_FREQUENCY` at zero seconds: the per user cooldown disappears and one
  account can be mailed in a loop while the process wide counter is still under its ceiling.

### 3.4 The autoconfirm interaction, which is a real hazard

`internal/api/mail.go:802-806` applies `GOTRUE_RATE_LIMIT_EMAIL_SENT` only when autoconfirm is
false:

```go
	// TODO(km): Deprecate this behaviour - rate limits should still be applied to autoconfirm
	if !config.Mailer.Autoconfirm {
		// apply rate limiting before the email is sent out
		if ok := a.limiterOpts.Email.Allow(); !ok {
```

So an environment left with `GOTRUE_MAILER_AUTOCONFIRM=true` and a working relay has an
unthrottled path for recovery and magic link mail. The per client bound that remains is
`GOTRUE_RATE_LIMIT_OTP` on those endpoints. This is why the recommended configuration for an
environment that actually sends confirmation mail sets `autoconfirm` to false, and why
`lab/mail_config.py` refuses a write that leaves `autoconfirm` true unless the operator passes
`--allow-autoconfirm`, printed as a warning naming this section.

## 4. Templates

### 4.1 What the pinned version requires

- Subject lines are inline Go templates in environment variables, one per mail type,
  `GOTRUE_MAILER_SUBJECTS_<TYPE>` (`configuration.go:626`, defaults in
  `templatemailer.go:127-141`).
- Bodies come from `GOTRUE_MAILER_TEMPLATES_<TYPE>`. That value is a **URL**, not a directory.
  `internal/mailer/templatemailer/template.go:442-471`:

```go
	url := getEmailContentConfig(&cfg.Mailer.Templates, typ, "")
	if url == "" {
		// We preserve the previous behavior of returning the default.
		tempStr := getEmailContentConfig(defaultTemplateBodies, typ, "")
		temp := template.Must(template.New("").Parse(tempStr))
		return temp, nil
	}
	if !strings.HasPrefix(url, "http") {
		url = cfg.SiteURL + url
	}
	tempStr, err := o.fetch(ctx, cfg, url)
```

  `fetch` (`:473-500`) does an HTTP GET with a 10 second timeout, requires status 200, reads at
  most `GOTRUE_MAILER_TEMPLATE_MAX_SIZE` bytes and caches the result for
  `GOTRUE_MAILER_TEMPLATE_MAX_AGE`.
- With no `GOTRUE_MAILER_TEMPLATES_*` set, the compiled in bodies are used
  (`templatemailer.go:32-152`), and `checkDefaults()` at init guarantees each type has both a
  subject and a body (`template.go:25-30,567-618`).
- There is no templates folder variable and no templates volume in this pin. See the
  unconfirmed note in section 2.4.
- The template data available to a body is fixed and checked at init: `ConfirmationURL`,
  `Data`, `Email`, `NewEmail`, `RedirectTo`, `SendingTo`, `SiteURL`, `Token`, `TokenHash`
  (`template.go:573-583`).

### 4.2 Recommendation: ship the upstream defaults, and do not add a template surface yet

Recommendation: for the first increment, set no `GOTRUE_MAILER_TEMPLATES_*` and no
`GOTRUE_MAILER_SUBJECTS_*`. Reasons:

1. The bodies are versioned with the pinned Auth image, so they cannot drift from it, and
   every body is already a working, plain, functional message.
2. Overriding a body requires an HTTP endpoint reachable **from inside the environment's
   internal network**. `NETWORK` is created with `--internal`
   (`lab/durable_runtime.py:186`: `lab.docker('network', 'create', '--internal', ...)`), so
   the Auth container has no egress to the public internet and cannot fetch a template from a
   public HTTPS URL. A relative path is worse: it resolves against `GOTRUE_SITE_URL`, which is
   `http://localhost` today, that is, the container itself.
3. Adding a template service means adding a process to the network and a new failure mode on
   the path that sends confirmation mail, which is the path signup depends on.

### 4.3 Where per environment templates would live, if and when they are added

If the operator later needs branded mail, the design that fits the constraints is:

- Source of truth: one HTML file per mail type per environment inside the installation's
  configuration tree, under a mail root keyed by the environment identifier, for example
  `<mail root>/<environment runtime id>/confirmation.html`. The file is deployment content,
  not a credential, so it does not belong under `.secrets/`.
- Delivery: the per environment gateway, which already has an environment aware route
  (`src/gateway/handler.ts`, `src/control/application.ts:36-48`), serves that file on a path
  inside the environment's prefix, and the gateway joins `NETWORK` so the Auth container can
  reach it by container name.
- Wiring: `GOTRUE_MAILER_TEMPLATES_CONFIRMATION` (and the other types) set to the absolute
  internal URL, for example `http://<gateway container name>:<port>/e_<id>/mail-templates/confirmation.html`.
  An absolute `http://` URL is required because of the `strings.HasPrefix(url, "http")` test
  above, and it must be absolute because `SITE_URL` is `http://localhost`.
- Caching: keep `GOTRUE_MAILER_TEMPLATE_MAX_AGE` at its 10 minute default, so a template edit
  takes effect within 10 minutes, and leave `GOTRUE_MAILER_TEMPLATE_RELOADING_ENABLED` false
  so a broken template cannot fail a send in the background path.
- Failure: `fetch` failing raises through `wrapError` and the send fails, so an override is a
  new way for signup to break. The verification in section 6 must therefore be rerun after any
  template override, with the count assertion changed from 1 to 2 mails.

That is a later increment with its own gate. It is specified here only so that the first
increment does not accidentally ship a partial template mechanism.

## 5. The operator decision surface

### 5.1 What the operator must supply

| Decision | Constraint that comes from the pin, not from preference | Failure mode if violated |
|---|---|---|
| Provider and relay host | The host must be reachable from inside the environment's internal network. A public provider is reachable only if the network is not internal, which it is today. A relay on the same network is reachable by container name. | Mail fails at send time, signup returns 500. |
| Port | 465 selects implicit TLS in the pinned client (`gomail` `smtp.go:46`: `SSL: port == 465`). Any other port is plaintext and is upgraded only if the server advertises STARTTLS (`smtp.go:81-87`). | Port 587 against a relay without STARTTLS sends the password in clear text. Port 465 against a relay that speaks STARTTLS on 465 fails to connect. |
| Credentials | `user` and `pass`. An empty `user` means the client sends no AUTH at all (`smtp.go:90`: `if d.Auth == nil && d.Username != ""`). | A relay requiring AUTH rejects the submission. |
| Certificate | The pinned client never sets `TLSConfig`, so `tlsConfig()` returns `&tls.Config{ServerName: d.Host}` (`smtp.go:117-122`) with verification on. There is no knob in the Auth configuration to skip verification. | A self signed certificate, or a host given as a bare IP, fails at handshake on 465 and on any STARTTLS upgrade. Use a provider hostname with a valid certificate. |
| From address | `admin_email` is required: it is the address in `FormatAddress(c.AdminEmail, c.SenderName)` (`configuration.go:588-591`). | An empty `admin_email` produces an empty `From`, which relays reject. |
| Reply to | `reply_to`, carried in `GOTRUE_SMTP_HEADERS`. No first class variable exists for it in this pin. | Without it, replies go to the sending address, which may be unmonitored. |
| Deliberate choice | One sending identity per environment. Two environments sharing the same `admin_email` share sender reputation and provider quota. | Abuse in one environment degrades delivery for the other. |

What the operator must not have to decide, because sbarbase fixes it: the Auth image and
version, the JWT and database connection variables, the site URL and action path defaults, the
rate limit locals, and whether the management realm sends mail (it never does).

### 5.2 What sbarbase must never log or print

Never printed to stdout, stderr, a console response, an audit detail, an evidence file or a
container log:

1. `GOTRUE_SMTP_PASS`. On every path: the config tool, the reconcile, the error handler, and
   the console.
2. `GOTRUE_SMTP_USER` when it is a provider API key rather than a username. Treat it as
   secret, print only `user set`.
3. The raw content of `GOTRUE_SMTP_HEADERS`, which can carry an authentication header.
4. The full contents of the mail configuration file.
5. Auth response bodies on failure, which can echo the request. The runtime already takes this
   position at `lab/durable_runtime.py:397`.

The existing error discipline must be extended, not replaced: the module already collapses any
exception into `SystemExit('Durable runtime operation failed; retained state is available for
reconciliation.')` at `lab/durable_runtime.py:396-398`. A mail failure must land in that same
class, with one refinement: the operator needs to know which of "SMTP unreachable", "SMTP
refused credentials" or "configuration invalid" happened. That is answered by a classified
status word, not by echoing the transport error. Section 7 fixes the three words.

Two accepted limitations, stated rather than hidden:

- The SMTP password is passed to the container with `--env-file`
  (`lab/durable_runtime.py:132-135`), so it appears in the container's `Config.Env` and is
  readable by anything that can read the Docker socket or run `docker inspect`. This is the
  same exposure every other generated secret in this installation already has (the Auth
  database password, the JWT secret, the storage keys). It is not made worse by this design,
  and it is not fixed by it.
- `GOTRUE_SMTP_LOGGING_ENABLED` is fixed to `'false'` by the builder, so no code path in this
  design can turn on recipient address logging by accident.

### 5.3 How the console shows the state without exposing a credential

Read surface, in `src/control/http.ts` beside the existing environment routes, `GET`
only, behind the same owner, admin or viewer policy as its neighbours:

```
GET /management/v1/environments/<uuid>/mail
```

The state is not copied into the catalog. The runtime writes
`.lab/upstream/mail-state.json` (`lab/mail_state.py`), one entry per environment runtime
identifier, and the route serves that environment's entry through the catalog row that maps
the uuid to the runtime id. No table exists, so there is no second source to agree with, and
an environment with no entry answers `{"data":{"state":"unconfigured"}}`.


The response carries no password field and no user field, by construction: the recorded summary
itself carries `user` and `pass` only as the literal markers `set` or `empty`, and the route omits
them by name. It holds only what the console renders. It is written by the reconcile operation
(section 2.6 step 7) and not by any HTTP path.

Read surface, in `src/control/http.ts` beside the existing environment route
(`http.ts:46-56`), `GET` only, behind the existing owner/admin/viewer policy:

```
GET /management/v1/environments/<uuid>/mail
```

Response shape, no credential in any field:

```
{"enabled":true,"state":"applied","host":"smtp.example.com","port":587,
 "from":"Example <noreply@example.com>","replyTo":"support@example.com",
 "credentials":"set","autoconfirm":false,"secureEmailChange":true,"otpExp":3600,
 "rateLimitEmailSent":"30","rateLimitOtp":30}
```

`credentials` is the literal string `set` or `empty`. There is no write route in this
increment, and section 9 records why.

Console, in `ui/Connection.tsx` next to the existing connection and key sections: a section
headed `Email` that renders one of four states, each with the specific next action, never the
password:

| State | Rendering | What the operator sees |
|---|---|---|
| `unconfigured` | `Email is off for this environment.` plus, for an owner, the exact command to run | `Email is off. Confirmation, recovery and magic link messages are not sent, and signup returns a session immediately.` |
| `applied` | host, port, from, reply to, `Credentials set`, autoconfirm flag, and the two rate limit values | facts only, plus `The environment's Auth service was restarted to apply this.` |
| `failed` | the classified reason from section 7, never a transport error | `Email could not be applied. The environment keeps working and sends no mail.` |
| `off` | as unconfigured, plus `A previous configuration was removed.` | the last change and who made it |

The console never offers a "send test message" button in this increment. Auth has no admin
mail test endpoint, and the only way to trigger a real message is to call an application
endpoint such as `/recover` for a user of that environment, which sends a real recovery mail
to a real person's address. Section 9 records the better version, where the console triggers
`POST /recover` for the operator's own address only if that address exists as a user in that
environment, and the section 6 probe is what proves delivery in the meantime.

## 6. The local verification path

### 6.1 The Mailpit pin, and the one thing that must be added

Mailpit v1.30.2 is already present on this host. Verified facts, all read from the local
daemon, not recalled:

```
public.ecr.aws/supabase/mailpit:v1.30.2
Id          sha256:37a38e48e9338cd7e89dfeb487f37b02ebfcd9cb23111bed2d345e79d37d6dd6
RepoDigest  public.ecr.aws/supabase/mailpit@sha256:37a38e48e9338cd7e89dfeb487f37b02ebfcd9cb23111bed2d345e79d37d6dd6
Exposed     1025/tcp (SMTP), 1110/tcp (POP3), 8025/tcp (HTTP and API)
Entrypoint  /mailpit
Healthcheck ["/mailpit","readyz"]
```

Add it to the pin set, one component, one change, in its own commit, following
`docs/engineering/UPSTREAM-UPDATE-POLICY.md` rule 5. New key `mailer` in `lab/images.lock.json`:

```json
"mailer": {
  "tag": "public.ecr.aws/supabase/mailpit:v1.30.2",
  "id": "sha256:37a38e48e9338cd7e89dfeb487f37b02ebfcd9cb23111bed2d345e79d37d6dd6",
  "digests": ["public.ecr.aws/supabase/mailpit@sha256:37a38e48e9338cd7e89dfeb487f37b02ebfcd9cb23111bed2d345e79d37d6dd6"]
}
```

`lab/install_server.py:47-61` walks every lock file and returns one reference per key with an
`id`, and `lab/pinned_images_check.py` compares the local digest against it, so this one
addition makes Mailpit a verified pin with no code change. `lab/pin_update.py verify` then
reports one more pinned component. Verification command:

```
/usr/bin/python3 lab/pin_update.py verify
/usr/bin/python3 lab/pin_update.py show
/usr/bin/python3 lab/pinned_images_check.py
```

Machine identity: Mailpit is a test mailbox. The pin exists so the probe cannot silently run a
different mailbox binary, and the evidence names the digest for the reason
`docs/engineering/UPSTREAM-UPDATE-POLICY.md` gives: "A pin that resolves to a different digest at the same
tag is a failure, not a warning."

Also note, because it is a prerequisite and it was measured on this host: the runtime network
is internal (`lab/durable_runtime.py:186`), and a container on an internal bridge network is
still reachable from the host at its bridge address. Verified during the writing of this
document with a throwaway network and a throwaway Mailpit container: the host read
`http://<container bridge address>:8025/api/v1/info` and got
`{"Version":"v1.30.2","Messages":0,...}`, and an SMTP submission to port 1025 with AUTH
succeeded and appeared at `GET /api/v1/messages`. Both the throwaway container and its network
were removed afterwards. That is why the probe reads mail back from the host and needs no
publishing and no helper container.

### 6.2 The probe

New file `lab/mail-check.py`, run with `/usr/bin/python3`, in the style of the existing live
probes (`lab/admission-check.py`, `lab/connection-limit-check.py`): it takes an exclusive lock,
uses the owned runtime, and removes what it created in a `finally` block. It never prints a
credential and it writes evidence to `docs/evidence/mail-checks.json` with the check list only,
matching the shape of `docs/evidence/bootstrap-checks.json`.

Which environment: two environments that are already published in
`.lab/upstream/endpoints.json`, referred to below as A and B. A is the environment that gets
mail. B is the neighbour that must be unaffected. The probe refuses to run unless at least two
environments are published, and it uses their runtime identifiers verbatim, so it works on
whichever two the operator has provisioned rather than assuming fixture names.

Step by step, with the assertion each step carries:

1. **Preflight.** Require the owned runtime running: the durable database container and the
   storage container exist, and `.lab/upstream/endpoints.json` names at least two
   environments. Require host headroom as the other probes do.
2. **Start one mailbox per environment.** For each of A and B, `docker run -d` with the pinned
   Mailpit digest, `--network sbarbase-durable-net`, `--label io.sbarbase.owner=mail-probe`,
   `--name sbarbase-durable-mailprobe-<id>`, `--memory 128m`, `--cpus 0.1`, no volume, and
   arguments `--smtp-auth-accept-any --smtp-auth-allow-insecure --max-messages 200`.
   On a user defined network the container name resolves by DNS, so the SMTP host the
   environment will use is the container name. Wait for readiness through the image's own
   healthcheck, `State.Health.Status == healthy`, then read
   `http://<bridge address>:8025/api/v1/info` and require `Messages` to be 0.
   The two flags matter: without them Mailpit refuses insecure PLAIN and LOGIN authentication,
   and the point of the probe is to exercise the credential path, not to skip it.
   The `--internal` network means no egress is needed or used.
3. **Configure A only.** Write `.secrets/upstream/<A>-mail.json` through
   `lab/mail_config.py write --stdin`, with `host` set to A's Mailpit container name, a port of
   1025, a user and a password, A's own `admin_email`, a `reply_to`, `autoconfirm` false,
   `max_frequency` of `1s` so the probe is not slowed by the production cooldown, `otp_exp` of
   300, and the section 3.2 rate limits. Confirm what was written without reading it:
   `/usr/bin/python3 lab/mail_config.py show <path>` must print `pass set` and the host, and
   must not print the password.
4. **Reconcile A only.** Run the new subcommand for A. Assert: A's Auth container was
   recreated (its container id changed), A's `/health` returns 200, and B's Auth container id
   is unchanged. The second assertion is the first half of the isolation claim.
5. **Prove a real message leaves A, and only A.** Create a user by calling A's signup endpoint
   directly at the Auth container's endpoint from `.lab/upstream/endpoints.json`, body
   `{"email":"mailprobe-<random>@example.test","password":"<random>"}`. Assert:
   - the response is 200 and has no `access_token`, because A now has autoconfirm false and
     the identity is unconfirmed;
   - `GET http://<A mailbox bridge address>:8025/api/v1/messages` returns `total` equal to 1;
   - that message's `To` address is the probe address;
   - the message's `From` address equals A's `admin_email` and its `From.Name` equals A's
     `sender_name`;
   - `GET /api/v1/message/<id>/raw` contains a `type=signup` query parameter and a non-empty
     `token=`, which proves the action link was built and not emptied;
   - the `Reply-To` header is present and equals A's `reply_to`, which is the only proof that
     the `GOTRUE_SMTP_HEADERS` JSON path works at all.
6. **Prove B did not send, and could not.** Call B's signup endpoint with a different address.
   Assert:
   - B's response is 200 and **has** an `access_token`, because B is still autoconfirm true and
     therefore still on the noop client;
   - B's mailbox `total` is 0;
   - A's mailbox `total` is still 1. This is the isolation claim in its strongest form: A's
     newly configured mail settings produced no mail for B, and B's submission did not appear
     in A's mailbox either.
   - Operationally, A's SMTP host is a container name inside A's own configuration and B's
     environment has no `GOTRUE_SMTP_*` key at all, which is checkable without a mailbox:
     `docker inspect <B auth container>` environment must contain no `GOTRUE_SMTP_` entry.
     Assert that too, because it is the property, while the mailbox counts are its evidence.
7. **Prove the failure is contained.** With A configured, stop A's mailbox container and call
   A's recovery endpoint for the address created in step 5. Assert:
   - A returns 500 with `error_code` `unexpected_failure` and message
     `Error sending recovery email`, which is the mapping at `internal/api/mail.go:423-428`;
   - B's signup still returns 200 with an `access_token`, and B's mailbox is still empty;
   - A's `/health` still returns 200, so an unreachable relay does not make the environment
     unhealthy, only the mail dependent operations fail. This distinction is what section 7
     exposes to the operator.
8. **Prove credentials are checked, not assumed.** Restart A's mailbox without
   `--smtp-auth-accept-any`, reconfigure A's mail file with a wrong password, reconcile, and
   call recovery again. Assert 500 and, in A's Auth container log, the classified reason. Then
   restore `--smtp-auth-accept-any` and reconcile back, and assert one more successful send.
   This step is what proves the credential is actually transmitted and honoured.
9. **Cleanup, in a `finally` block.** Remove both mailbox containers, remove both probe mail
   files, reconcile A back with `--off`, assert A's environment has no `GOTRUE_SMTP_` key and
   A's `/health` returns 200, and delete the probe users through each Auth service role token
   derived from `runtime.json`, the way `lab/bootstrap-check.ts:69-72` deletes its identity.
   Assert afterwards that the retained state is unchanged: A and B's Auth containers exist,
   both `/health` endpoints return 200, and neither mailbox container nor probe mail file
   remains.

Evidence file content, `docs/evidence/mail-checks.json`: the list of check names above with a
boolean each, the pinned Mailpit digest, the two environment identifiers, and the statement
that no credential value and no message body was retained. The coverage limit must be stated
in the same file's `scope` field: one host, one internal network, one delivery path, no public
provider, no real certificate, no STARTTLS or port 465 path, no template override.

### 6.3 What this proves and what it does not

Proves: the pinned Auth really submits mail over SMTP; the AUTH path and the header path work;
the confirmation link is built; the switch is per environment; an unconfigured neighbour
neither sends nor is affected; a dead relay fails the mail dependent operation and nothing
else; a wrong credential is refused; cleanup restores the retained state.

Does not prove: delivery through a real provider, certificate verification against a real
certificate, port 465, STARTTLS upgrade, template override, provider quota behaviour, or that
mail is not silently dropped by a provider's spam filter.

### 6.4 The reconciliation assertions, which need their own checks

The probe covers the happy path. Three properties of the reconcile need separate, cheaper
checks in `lab/test_mail_config.py` and one Python test for the runtime:

1. Reconciling an environment whose configuration has not changed does not recreate the
   container. Asserted by container id equality across two consecutive calls.
2. Removing the mail file and reconciling with `--off` removes every mail key from the
   container environment, which is the removal case the pre-existing one directional drift
   guard would have missed.
3. A mail file that fails schema validation makes `mail_config.load` raise and makes the
   reconcile refuse before any container is touched. Asserted by container id equality and a
   non-zero exit.

## 7. Failure semantics

The governing fact is that the pinned mailer is synchronous. `mailmeclient.DialAndSend` is
called from `internal/mailer/templatemailer/template.go:91-98`, which is called from
`internal/api/mail.go` inside the request, so a send that fails fails the request that caused
it.

| Situation | What happens | What the operator sees |
|---|---|---|
| No mail file for the environment (today's posture) | The noop client accepts and discards. Signup returns a session because autoconfirm is true. Nothing is logged about mail except one info line naming the site URL at startup. | `Email is off for this environment.` No error anywhere, because nothing failed. |
| Mail configured, relay reachable, credentials right | The message is submitted before the response is written, then tokens and timestamps are updated in the database. | `applied`, in the console. |
| Relay host unreachable, DNS wrong, connection refused, dial timeout | `dial.DialAndSend` returns an error after a 10 second dial timeout, and the calling endpoint returns 500. `internal/api/mail.go:345`: `return apierrors.NewInternalServerError("Error sending confirmation email").WithInternalError(err)`. Signup fails, so the user is not created: the failure is before the confirmable user is returned. | The application sees 500 with `error_code` `unexpected_failure`. The console shows `failed` with the reason `smtp_unreachable`. The reconcile status and the audit row name the environment. |
| Credentials refused | Same path. The relay answers 535 during AUTH, gomail returns the error, the endpoint returns 500. | `failed` with the reason `smtp_rejected`. |
| Certificate invalid, or the host is a bare IP, on 465 or after a STARTTLS upgrade | Same path, at the handshake, through `tls.Config{ServerName: d.Host}` verification. | `failed` with the reason `smtp_tls`. |
| Rate limit reached | 429 before any send attempt: `internal/api/mail.go:341` returns `apierrors.NewTooManyRequestsError(apierrors.ErrorCodeOverEmailSendRateLimit, ...)`. The user row is not updated, so the previous token survives. | The application sees 429 with `error_code` `over_email_send_rate_limit`. This is a degradation, not an outage: other users still receive mail while the process wide bucket has room. |
| Per user cooldown not elapsed | 429 `over_email_send_rate_limit` from `validateSentWithinFrequencyLimit`, with a message stating the seconds left (`internal/api/errors.go:255-260`). | The application sees 429. |
| Wrong configuration file content | `mail_config.load` raises during the reconcile, before any container is touched. The running container keeps its previous configuration. | `failed` with the reason `invalid_configuration`, and a statement that the environment is unchanged. |
| Relay dies between two requests | Later sends fail and later signups fail until it recovers. Already confirmed users keep signing in, because `signInWithPassword` does not send mail. Already created unconfirmed users cannot be confirmed by mail, but a resend is possible once the relay returns. | `failed` in the console. Session issuing and the database are unaffected. |
| `GOTRUE_MAILER_EMAIL_BACKGROUND_SENDING` true (not recommended for this increment) | `internal/api/api.go:198-200` adds the task middleware, the send is deferred, and signup then succeeds even with a dead relay, so the user is created and simply never receives a link. | The trade is explicit: signup availability improves and correctness degrades. Choose it only after deciding what happens to an unconfirmed identity, and note that with autoconfirm false the deferred send is also outside the `GOTRUE_RATE_LIMIT_EMAIL_SENT` check in a different way than the synchronous path. Leave it false in the first increment. |

Three consequences to design around, stated plainly:

1. **Signup depends on the relay when confirmation is on.** With autoconfirm false, an
   environment with a broken relay cannot accept new signups at all. There is no queue and no
   retry in this pin. The operator's mitigation is either to fix the relay or to switch the
   environment back off, which returns it to autoconfirm behaviour only if `autoconfirm` is
   also set true.
2. **Existing users are never locked out by a mail problem.** Password sign in, sessions and
   token refresh do not use the mailer. Only signup, recovery, magic link, invite, email
   change and the notification types touch it.
3. **The health endpoint does not report the relay.** Auth's `/health` is independent of SMTP,
   which is why step 7 of the probe asserts both facts at once. A `failed` console state must
   therefore be produced by the reconcile and by classified send failures, not inferred from
   health.

The three reason words, produced and stored so that no transport error text is ever rendered:
`smtp_unreachable`, `smtp_rejected`, `smtp_tls`, plus `invalid_configuration`. They map to
operator actions: check host and network, check credentials, check the certificate and the
host name, fix the file. The classification must be derived from the error class inside the
runtime, never from the relay's raw text, in the same spirit as the existing rule at
`lab/durable_runtime.py:393`: `# Stable local worker protocol. Never classify failures from raw stderr.`

## 8. Implementation task list

Each step names the files it touches and the command that verifies it. bun only. Steps 1 to 4
change no behaviour and are safe to land first.

**Step 1. Pin the test mailbox.**
Files: `lab/images.lock.json` (one new key, section 6.1), `docs/engineering/UPSTREAM-UPDATE-POLICY.md`
(one row in the Current pins table, one entry under Pending components is not needed), a new
`docs/upstream/2026-09-21-mailer-1.30.2.md` with the four sections rule 2 requires.
Verify:
```
/usr/bin/python3 lab/pin_update.py verify
/usr/bin/python3 lab/pin_update.py show
/usr/bin/python3 lab/pinned_images_check.py
/usr/bin/python3 -m unittest lab/test_pinned_images.py
```

**Step 2. Write the operator configuration tool.**
Files: new `lab/mail_config.py`, new `lab/test_mail_config.py`.
Verify:
```
/usr/bin/python3 -m unittest lab/test_mail_config.py
/usr/bin/python3 lab/mail_config.py write /tmp/mail-probe.json --stdin
/usr/bin/python3 lab/mail_config.py show /tmp/mail-probe.json
```
The `show` output must contain `pass set` and must not contain the password value.

**Step 3. Add the optional mail block to the shared builder.**
Files: `lab/run.py` (the `auth_configuration` signature and body only), `lab/mail_config.py`
(the `load` function).
Verify, and this is the disabled-by-default proof:
```
/usr/bin/python3 -m unittest lab/test_mail_config.py
/usr/bin/python3 -c "import sys;sys.path.insert(0,'lab');import run;v={'auth':'x','jwt':'y'};a=run.auth_configuration('e_'+'0'*24,v,'db');b=run.auth_configuration('e_'+'0'*24,v,'db',None);assert a==b,set(a)^set(b);assert not any(k.startswith('GOTRUE_SMTP_') for k in a);print('builder unchanged without mail')"
```

**Step 4. Confirm the existing suites still pass.**
Files: none.
Verify:
```
/usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'
bun test
```

**Step 5. Add the reconcile operation and the subcommand.**
Files: `lab/durable_runtime.py` (new `Runtime.reconcile_mail`, a `mail` choice in the argument
parser at `:370`, and the two directional mail key comparison described in section 2.6).
Verify with the runtime up:
```
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id>
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id>
```
The first run reports a change or `unchanged`, the second must report `unchanged` and must not
change the container id. Read the id back without printing any secret:
```
docker inspect --format '{{.Id}}' sbarbase-durable-<environment runtime id>-auth
```

**Step 6. The live delivery and isolation probe.**
Files: new `lab/mail-check.py`, new `docs/evidence/mail-checks.json`, one line in
`lab/README.md` under the live probe list.
Verify:
```
/usr/bin/python3 lab/mail-check.py
/usr/bin/python3 -c "import json;d=json.load(open('docs/evidence/mail-checks.json'));print(sum(1 for c in d['checks'] if c['passed']),'of',len(d['checks']))"
```
Assert after it finishes: no `sbarbase-durable-mailprobe-*` container exists, no
`*-mail.json` probe file remains, and both environments' `/health` return 200.

**Step 7. Add the runtime tests for the three reconcile properties.**
Files: `lab/test_runtime_reuse.py` or a new `lab/test_mail_reconcile.py` following the existing
style of `lab/test_runtime_reuse.py`.
Verify:
```
/usr/bin/python3 -m unittest lab/test_mail_reconcile.py
```

**Step 8. The catalog table and the read route.**
Files: `src/control/catalog.ts` (the new table plus its migration block beside the existing
`PRAGMA table_info(provision_jobs)` style), `src/control/http.ts` (the `GET` route),
`src/control/handler.ts` if the route needs to reach the credentials handler instead (it does
not: this is metadata).
Verify:
```
bun run typecheck
bun test
bun lab/management-check.ts
curl -s -H "authorization: Bearer <management session token>" http://127.0.0.1:<port>/management/v1/environments/<uuid>/mail
```
The response must contain no field named `pass`, `password` or `user`.

**Step 9. The console surface.**
Files: `ui/Connection.tsx` (one section), `ui/api.ts` (the type).
Verify:
```
bun run build:ui
bun lab/console_build_check.py 2>/dev/null || /usr/bin/python3 lab/console_build_check.py
```
Then the visual check per `docs/design/CONSOLE-QA.md`: the four states, and a screenshot with
the section visible. The section must render no value that could be a credential.

**Step 10. Document the operator procedure.**
Files: `docs/guides/operator-setup.md` (a new section after the run steps), `docs/decisions/README.md` (the
decision record: per environment mail file, no password through HTTP, defaults for templates).
Verify:
```
/usr/bin/python3 -m unittest lab/test_doc_references.py
```
The new section must state the three commands an operator runs (write, show, reconcile) and
the exact words of the disabled default.

**Step 11. Update the pin table's coverage.**
Files: `docs/engineering/UPSTREAM-UPDATE-POLICY.md` if the mailer pin row needs its Notes column filled,
`docs/engineering/checkpoints.md` (the evidence row).
Verify: `/usr/bin/python3 -m unittest lab/test_doc_references.py` and a read of the pin table
against `lab/pin_update.py show`.

### Deliverable of each step, so the step is done when

1. `pin_update.py verify` reports no problem and one more pinned component.
2. `unittest` passes and `show` prints `pass set` and no password.
3. The equality assertion prints `builder unchanged without mail`.
4. Both suites pass with the same or more tests than before.
5. Second reconcile reports `unchanged`, container id stable across it.
6. The evidence file lists every check from section 6.2 as passed and the cleanup assertions hold.
7. Three reconcile properties covered by named tests.
8. The read route returns the shape in section 5.3 and no credential field.
9. The console renders the four states.
10. The operator section exists and the doc reference test passes.
11. The pin table and `docs/engineering/checkpoints.md` agree with `pin_update.py show`.

## 9. Open items, and what is deliberately not in this increment

1. **No write route for mail configuration.** The password would cross the HTTP boundary and
   would then have to be held somewhere in the API process or the job queue. Both are worse
   than a 0600 file written by the trusted operator, and `docs/guides/operator-setup.md:36-38`
   already takes that position for the bootstrap: "The setup script is for the trusted host
   operator... It is not an HTTP endpoint." Reconsider only together with a secret transport
   design for the job queue.
2. **No console test message.** Section 5.3 records the better version: a
   `POST /management/v1/environments/<uuid>/email/test` that calls the environment's `/recover`
   for the calling operator's address only if that address exists as a user in that
   environment, and returns a refusal explaining why when it does not. It needs its own
   rate limit reasoning, because it would consume the environment's `GOTRUE_RATE_LIMIT_OTP`
   bucket from the platform's own address.
3. **No template override.** Section 4.3. It needs the gateway on the runtime network first.
4. **No account change notifications.** `GOTRUE_MAILER_NOTIFICATIONS_*` stay off. Turning them
   on sends mail on ordinary account activity and needs its own abuse reasoning.
5. **No invite flow.** `GOTRUE_EXTERNAL_EMAIL_ENABLED` is already true and the invite endpoint
   requires admin credentials, so nothing is exposed today, but an invite UI is a separate
   feature with its own rate limit story.
6. **No per environment unconfirmed user policy.** With autoconfirm false an unconfirmed user
   exists in the environment's database. What the application should do with such a user, and
   how RLS should treat them, is an application level decision for the environment's owner,
   not a platform one.
7. **No second relay path.** No port 465 verification, no STARTTLS verification, no
   certificate verification against a real certificate. Section 6.3 names all three, and the
   pinned client's certificate behaviour is quoted in section 5.1 so the operator can avoid
   the cases the probe does not cover.
8. **The mail key set is fixed.** Adding a variable later means touching the builder, the file
   schema and the test that pins the disabled posture. That is intentional: an implicit
   default would defeat the equality guarantee.

## 10. Source index

Every claim in this document resolves to one of these.

sbarbase, this repository:

| Claim | Source |
|---|---|
| The Auth environment content today | `lab/run.py:121-130` |
| The REST builder, unchanged | `lab/run.py:133-137` |
| Component lab uses the same builder | `lab/run.py:143-144` |
| The distro probe's own inline Auth env | `lab/distro-check.py:71-76` |
| Runtime secret key layout | `lab/durable_runtime.py:104-109`, `:292-294` |
| Per service env file, mode 0600, `--env-file` | `lab/durable_runtime.py:131-135`, `lab/run.py:39-42` |
| The one directional drift guard | `lab/durable_runtime.py:119-123` |
| Service launch loop | `lab/durable_runtime.py:309-313` |
| Management realm override, no SMTP | `lab/durable_runtime.py:352-354` |
| Internal network creation | `lab/durable_runtime.py:186` |
| Resume preconditions | `lab/durable_runtime.py:225-240` |
| Secrets never reach console | `lab/durable_runtime.py:396-398` |
| Never classify from raw stderr | `lab/durable_runtime.py:393` |
| Ignore check before persisting | `lab/durable_runtime.py:90-93` |
| Bootstrap input path, no echo, lock | `lab/bootstrap.py:12-13`, `:21-26`, `:28-41`, `:44-46` |
| Bootstrap admin creation with `email_confirm:true` | `lab/bootstrap-auth.ts` `create(...)` |
| Probe cleanup convention | `lab/bootstrap-check.ts:69-73` |
| Operator supplied secret file precedent | `lab/operator_file.py:66-91`, `:104-114` |
| Pins walked from lock files | `lab/install_server.py:47-61` |
| Pin digest comparison | `lab/pinned_images_check.py` `evaluate` |
| Pin table and staging rules | `docs/engineering/UPSTREAM-UPDATE-POLICY.md:42-84` |
| Bootstrap deliberately does not email | `docs/guides/operator-setup.md:36-40`, `:62-65` |
| Ignored secrets tree | `.gitignore:1-2`, `lab/run.py:13-15` |
| Catalog schema shape | `src/control/catalog.ts:22-53` |
| Environment route regex and methods | `src/control/http.ts:46-56` |
| Control handler split | `src/control/handler.ts:7-12` |
| Connection and key console sections | `ui/Connection.tsx`, `ui/api.ts` |
| Do not run lifecycle commands concurrently | `lab/README.md:101` |

Upstream, tag `v2.196.0`, commit `0204331ca41a5b49f076b6fa3dc6c0d20b996590`:

| Claim | Source |
|---|---|
| SMTP variable names | `example.env` at that tag: host, port, user, max_frequency, pass, admin_email, sender_name |
| Mailer variable names | `example.env` at that tag: autoconfirm, urlpaths, subjects, templates, secure email change, notifications |
| Rate limit variable names | `example.env` at that tag, plus `internal/api/apilimiter/apilimiter.go:15-100` |
| SMTP struct fields and defaults | `internal/conf/configuration.go:573-586` |
| Mailer struct fields | `internal/conf/configuration.go:622-664` |
| `OTP_EXP` default 86400, `OTP_LENGTH` clamp | `internal/conf/configuration.go:1171-1178` |
| `MAX_FREQUENCY` default 1 minute | `internal/conf/configuration.go:1180-1182` |
| `SECURE_EMAIL_CHANGE_ENABLED` default true | `internal/conf/configuration.go:631` |
| `LOGGING_ENABLED` default false | `internal/conf/configuration.go:582` |
| `EMAIL_BACKGROUND_SENDING` default false | `internal/conf/configuration.go:639` |
| Template cache defaults | `internal/conf/configuration.go:646-661` |
| `autoconfirm` plus `allow_unverified` is a startup error | `internal/conf/configuration.go:1151-1153` |
| URL path defaults `/verify` | `internal/conf/configuration.go:1155-1169` |
| `Rate` decode, hour default | `internal/conf/rate.go:10,17-60` |
| Noop client when the host is empty | `internal/mailer/templatemailer/template.go:41-46` |
| Noop returns nil for a non-empty recipient | `internal/mailer/noopclient/noopclient.go` `Mail` |
| gomail client, From, header, no TLS config | `internal/mailer/mailmeclient/mailmeclient.go:26-70` |
| gomail version | `go.mod:33` |
| gomail port 465 implies TLS | gomail `smtp.go:46` at commit `81ebce5c23df` |
| gomail STARTTLS opportunistic, certificate verified | gomail `smtp.go:81-87`, `:117-122` |
| gomail skips AUTH when the user is empty | gomail `smtp.go:90` |
| Body template is a URL, fetched over HTTP | `internal/mailer/templatemailer/template.go:442-500` |
| Inline default subjects and bodies | `internal/mailer/templatemailer/templatemailer.go:32-152`, `:127-141` |
| Template data keys | `internal/mailer/templatemailer/template.go:567-583` |
| Send happens inside the request | `internal/mailer/templatemailer/template.go:91-98` |
| Email rate limit, process wide, skipped under autoconfirm | `internal/api/mail.go:792-806` |
| 429 on the email rate limit | `internal/api/mail.go:341`, `:376`, `:419`, `:461`, `:504` |
| 500 on a send failure, per endpoint | `internal/api/mail.go:345`, `:380`, `:423`, `:465`, `:508`, `:560` |
| Signup returns 500 when confirmation mail fails | `internal/api/signup.go:250-252` |
| Per user cooldown and its 429 | `internal/api/mail.go:325,341,401,443,486,527` |
| Limiter buckets and their construction | `internal/api/apilimiter/apilimiter.go:189-238`, `:358` |
| Background sending middleware | `internal/api/api.go:198-200` |
| Email provider reachability and endpoint list | `internal/api/api.go:229-265` |

## 11. Boundary against the operator notification path

Two different things in this repository plan both talk to an SMTP server, and they must not be
merged.

| | This document, application mail | `03-notifications.md`, operator notification |
|---|---|---|
| Who sends | The environment's own Auth process, from inside that process | A platform side notifier that the plan of that document defines |
| Who receives | Application end users: signup confirmation, recovery, magic link, email change, invite | The trusted installation operator |
| Scope | One environment, per environment configuration, per environment Auth container | One installation, one configuration, one sender |
| Trigger | An application request to that environment's Auth endpoint | A provisioning, admission, fence, recovery, pressure or console event |
| Switch off | Remove that environment's mail file and reconcile it | Remove the installation's notification configuration |

Rules that follow, and that a reviewer should enforce:

1. **No shared key and no shared file.** Application mail never reads the notification
   configuration, and the notification sender never reads
   `.secrets/upstream/<environment runtime id>-mail.json`. Sharing either would make one
   environment's abuse spend the installation's alerting budget, and would make an operator
   alert depend on the health of one application's relay.
2. **No shared credential.** An environment's SMTP credential must not be the same credential
   the installation uses for operator alerts. An environment owner who rotates or revokes their
   own relay credential must not be able to silence operator alerts.
3. **Distinct reason vocabularies, in distinct records.** This document stores
   `smtp_unreachable`, `smtp_rejected`, `smtp_tls` and `invalid_configuration` as the apply
   state of one environment in the runtime's own `.lab/upstream/mail-state.json`, served read
   only by `GET /management/v1/environments/<uuid>/mail`. The notification plan
   uses `smtp_refused` and `smtp_temporary_failure` as delivery outcomes in its own outbox.
   The same underlying fault therefore has two names in two places on purpose: one answers
   "did this environment's mail get applied", the other answers "did this alert get delivered".
   Do not collapse them into one column.
4. **The verification probes must not collide.** This document's probe is `lab/mail-check.py`,
   it joins the owned runtime network and it names its containers
   `sbarbase-durable-mailprobe-<environment runtime id>`. The notification plan's probe is
   `lab/notification-check.py` with its own private network and its own container name. They
   use the same pinned image and the same SMTP and API ports, so they must not be run
   concurrently: run one, let it clean up, then run the other. The lab budget rule in
   `lab/README.md` already forbids concurrent lab use, and that rule is sufficient here.
5. **One image, one pin.** Both use the `mailer` pin added in step 1 of section 8. Neither may
   pull a second mailbox image, and neither may rename that pin's component key.
