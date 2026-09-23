# Fragment: `lab/durable_runtime.py` mail changes (not applied here)

> APPLIED on 2026-09-21 by the parent, with `lab/mail_state.py` written to satisfy
> Hunk 4. Kept as the record of what was applied, not as work left to do.

`lab/durable_runtime.py` is owned by another agent running in parallel, so this
fragment carries the exact change instead of applying it. Nothing in it is a
suggestion about style: every hunk below is required for the per environment mail
configuration to reach a running environment's Auth process, and for the probe's
`docs/engineering/ENVIRONMENT-EMAIL.md` section 6.2 step 4 (reconcile A only) to exist at all.

Source of the change: `docs/engineering/ENVIRONMENT-EMAIL.md` sections 2.3, 2.6 and 8 steps 5
and 7. Acceptance criteria: `docs/engineering/reviews/three-topics-redteam.md` B-T1, B-T2,
B-T3, B-T4 and B.5 question 1. Supporting code that already exists in this
branch: `lab/mail_config.py` (schema, 0600 storage, `load`) and
`lab/run.py auth_configuration(e, v, database_host, mail=None)`.

## Hunk 1. Carry the environment's mail configuration into its Auth container

The one call site that builds an application environment's Auth environment.
Today it passes three arguments, so no `GOTRUE_SMTP_*` key ever reaches an
environment container.

Context, around `lab/durable_runtime.py:309`:

```python
    def activate_services(self,e,v,*,creating):
        endpoints = {}
        for service, builder, port, suffix in [('auth', lab.auth_configuration, 9999, '/health'), ('rest', lab.rest_configuration, 3000, '/')]:
            name = PREFIX+'-'+e+'-'+service
            self.launch(name, service, builder(e, v, DB), '256m', .25, existing_only=not creating)
```

Required change:

```python
    def activate_services(self,e,v,*,creating):
        endpoints = {}
        # The environment's own mail configuration, per environment and read once at
        # process start. mail_config.load returns None when the environment has no
        # mail file, and the builder then returns exactly today's dict.
        mail = mail_config.load(e)
        for service, builder, port, suffix in [('auth', lab.auth_configuration, 9999, '/health'), ('rest', lab.rest_configuration, 3000, '/')]:
            name = PREFIX+'-'+e+'-'+service
            config = builder(e, v, DB, mail) if service == 'auth' else builder(e, v, DB)
            self.launch(name, service, config, '256m', .25, existing_only=not creating)
```

Do **not** change the management realm. `lab/durable_runtime.py:352` keeps its
three argument call on purpose: the operator's identity realm never gains SMTP,
and `lab/bootstrap.py` therefore never depends on a working relay
(`docs/engineering/ENVIRONMENT-EMAIL.md` section 1.4).

Add the import beside the other local module imports:

```python
import mail_config
```

## Hunk 2. `Runtime.reconcile_mail`, insert after `resume`

Insert directly after the `resume` method (context: `resume` ends with
`self.activate_services(e,self.values['environments'][e],creating=False)`).

```python
    def reconcile_mail(self, e, off=False):
        """Apply one environment's mail configuration, or remove it with `off`.

        Auth reads its SMTP configuration once, at process start
        (docs/engineering/ENVIRONMENT-EMAIL.md section 2.1 rule 4), so a mail change is a
        container recreate and never a live patch. The comparison below runs in
        both directions on purpose: `launch` compares the desired keys only, so a
        key dropped from the desired dict would keep a stale SMTP value in a
        recreated container, and dropping every key is exactly what `off` does.
        The environment database holds all Auth state, so recreating the container
        process loses nothing.
        """
        effect_receipt.require_settled(STATE)
        effect_receipt.require_permission(STATE, e)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        path = STATE/'endpoints.json'
        published = json.loads(path.read_text()) if path.exists() else {}
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Reconcile requires a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        name = PREFIX+'-'+e+'-auth'
        actual = inspect('container', name)
        if not actual:
            raise RuntimeError('Reconcile requires the retained Auth container')
        mail = None if off else mail_config.load(e)
        desired = lab.auth_configuration(e, self.values['environments'][e], DB, mail)
        marked = lambda key: (key.startswith('GOTRUE_SMTP_') or key.startswith('GOTRUE_MAILER_')
                              or key.startswith('GOTRUE_RATE_LIMIT_'))
        configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
        if {k: v for k, v in configured.items() if marked(k)} == {k: v for k, v in desired.items() if marked(k)}:
            mail_state.record(e, 'unchanged', mail)
            print('Environment mail configuration is unchanged.')
            return
        lab.docker('rm', '-f', name)
        self.launch(name, 'auth', desired, '256m', .25)
        self.wait(self.endpoint(name, 9999)+'/health')
        mail_state.record(e, 'off' if off else 'applied', mail)
        print('Environment Auth service recreated to apply the mail configuration.')
```

Two notes the reviewer must decide, both outside this fragment's file:

1. `mail_state.record` is the non-secret summary writer described in Hunk 3. The
   console surface and the catalog row are separate increments; the runtime must
   not open `.lab/upstream/control.sqlite`, which the control plane owns.
2. The operation must hold `.lab/upstream/operation.lock` the way `provision`
   does, and it must not run while `lab/dev.py` owns the runtime
   (`lab/README.md:101`).

## Hunk 3. The subcommand

Context, the argument parser and dispatch at the bottom of the file:

```python
    parser.add_argument('command', choices=['up', 'stop', 'provision'])
    parser.add_argument('environment', nargs='?')
```

Required change:

```python
    parser.add_argument('command', choices=['up', 'stop', 'provision', 'mail'])
    parser.add_argument('environment', nargs='?')
    parser.add_argument('--off', action='store_true',
                        help='remove every mail key; the environment returns to the noop client')
```

and inside the existing locked branch:

```python
                if args.command=='stop':stop()
                elif args.command=='mail':
                    runtime=Runtime(operation_fd=lock.fileno(),worker_runtime=args.environment or '')
                    runtime.reconcile_mail(args.environment or '', off=args.off)
                else:
```

Both calls the design requires then exist:

```
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id>
/usr/bin/python3 lab/durable_runtime.py mail <environment runtime id> --off
```

## Hunk 4. The non-secret state the console and the catalog read

Not in `lab/durable_runtime.py`: a new small module, so that no credential ever
reaches a state file. Shape, one entry per environment, written with the existing
`atomic()` helper:

```json
{"e_1f0624c545789214eef426c9": {"state": "applied", "host": "smtp.example.com", "port": 587,
  "from": "Example <noreply@example.com>", "reply_to": "support@example.com",
  "credentials": "set", "autoconfirm": false, "at": 1758400000}}
```

The four states are the ones `lab/mail_config.py` already names: `unconfigured`
(no file and no state entry), `applied`, `failed` and `off`. `failed` is written
by the operator path, because the pinned mailer is synchronous and a send failure
fails the request that caused it rather than a background job
(`docs/engineering/ENVIRONMENT-EMAIL.md` section 7).

## What this fragment does not do

- No `GOTRUE_SMTP_*` key is ever added to the management realm.
- No write path for the password over HTTP, no console test message, no template
  override, no per environment templates (design step 9, 10 and 11 are separate).
- No change to `lab/run.py launch_services` (the component lab keeps its three
  argument call) and no change to `lab/distro-check.py`.