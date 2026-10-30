[العربية](CHANGELOG.ar.md)

# Changelog

All notable changes to sbarbase. The format follows Keep a Changelog and the
project uses Semantic Versioning, which for a 0.x version means the interface may
still change between releases.

The declared package version is 0.9.2. Historical 0.1.0 and 0.2.0 entries retain their original scope. Later Docker and VM rehearsals are dated in [status](docs/reference/status.md); no independent public-server or production release acceptance is established.

## [Unreleased]

### Added

- **Room on the Server page, and one cleanup.** Operators see memory and disk against the numbers the supervisor itself enforces (the 2 GiB memory reserve, what a new environment needs, the disk warning and critical lines), whether the host has swap, and the size of Docker's build cache with a **Clear build cache** button that asks the supervisor to run one `docker builder prune --force`: images, containers and volumes are never touched, because the way back of an update needs the images. New routes `GET /management/v1/server/capacity` and `POST /management/v1/server/build-cache` (installation operators only, audited); the catalog schema moves to version 9, so the previous release refuses a catalog this one has opened and the way back is the pre-upgrade snapshot. `docs/engineering/RESOURCE-POLICY.md` states what the memory ceilings mean on a host with no swap. See [Services](docs/guides/services.md).
- **On demand mode for Edge Functions, and for Realtime where an operator allows it.** Each environment chooses Off, On or On demand for Realtime and Edge Functions on its Services page, with the exact effect in words before it is applied, and the quiet period in minutes (5 or more). An On demand service has no container while nothing uses it: the first request that carries a verified API key starts it and is held up to 20 seconds (then `503` with `retry-after`), and it stops after its quiet period with no open WebSocket and no call in flight. The page shows **Sleeping** and **Waking…** plainly and says under each service: "Starts on the first request, stops after N quiet minutes. The first request after a pause can take a few seconds." The wake limit (6 an hour per service and environment, then `429`), the hold and the floor are shown read only. A call without a key can start Edge Functions only for a public function (`verify_jwt` off). One wake at a time on the installation, through the same admission as a manual start; Realtime's replication slot is removed on a sleep and created again after a wake (checked live: [evidence](docs/evidence/optional-services-2026-10-30/README.md)); restarts, updates, backups and restores never start a sleeping service. Operators have a kill switch (every On demand service behaves as On, saved choices kept) and installation defaults on **Server**. Every wake, sleep and change is an audit event with its actor and reason. **Defaults, from the measurement:** a wake answered in 1.9 to 2.5 s for Edge Functions and 12.3 to 14.8 s for Realtime, so a **new environment starts with Edge Functions On demand and Realtime Off**, and Realtime can be chosen as On demand only after an operator allows it (`realtimeOnDemand`). Environments that exist keep what they have. `PUT /environments/{id}/realtime` and `/functions` still take `{"enabled"}` (On or Off) and now `{"mode","idle_minutes"}`; a deploy turns Edge Functions on only when they are Off and says `turned_on`. A manual turn off of Edge Functions waits up to 10 seconds for running calls. The catalog schema moves to version 8. New `lab/on-demand-check.py`. See [Services](docs/guides/services.md).
- **Hints for services nobody uses.** Realtime or Edge Functions that has been on for 7 days with no use shows "No connection for 11 days, 183 MiB. Turn it off?" on the Services page, and the installation operator gets one notice per service, repeated at most every 14 days. Nothing is ever turned off by a hint. The 7 days is an installation setting with a floor of 1 day (`GET` and `PUT /management/v1/installation/services`, operators only, audited). The catalog schema moves to version 7, so the previous release refuses a catalog this one has opened; the way back is the pre-upgrade snapshot.
- **A Services page for every environment.** One page lists Realtime, Edge Functions, scheduled jobs, direct database access and Studio with each one's state, the memory its container holds now (measured once a minute by the supervisor, shown as out of date after three minutes), when it was last used, and what turning it on needs against the server's free memory. Every member can read it; only owners and admins switch a service. Turning off Realtime or Edge Functions while connections are open or calls are running asks first and says how many. New route `GET /management/v1/environments/{id}/services`; the switches and their audit events are unchanged. See [Services](docs/guides/services.md).

### Still open

- **Purging a deleted environment is refused**, because nothing proves that only that environment wrote the shared PostgreSQL cluster and Storage objects; delete and restore are soft.

## [0.10.2] - 2026-10-30

### Added

- **Walkthrough guides with screenshots.** New guides for the Authentication workspace (`docs/guides/authentication.md`), the Edge Functions workspace and a tour of every console section (`docs/guides/console-tour.md`), in English and Arabic, with screenshots of demo data.

### Changed

- **Confirm email and the email limits no longer need custom SMTP.** They are sign-in settings now (`MAILER_AUTOCONFIRM`, `RATE_LIMIT_EMAIL_SENT`, `RATE_LIMIT_OTP`, `RATE_LIMIT_VERIFY`, `SMTP_MAX_FREQUENCY`, registry page `emails`), so they show and save with custom SMTP off, and turning custom SMTP off no longer resets them. `PUT /environments/{id}/smtp` still takes `confirm_email` and the four limits (a number sets one, `null` puts it back to the default, omitted keeps it) and now also accepts a body with only these and `enabled: false`; they are stored in the sign-in settings under one revision, and a first save creates those settings with the values Auth already runs with if none exist. A new mail file no longer carries them. **Upgrade:** a mail file written by an earlier version keeps deciding what the sign-in settings do not name, so no environment changes behaviour on upgrade; the first save from the page, or turning SMTP off, moves its values into the sign-in settings and out of the file. A form the page cannot hold (`30/1h`, `1h30m`) stays in the file. With nothing saved and no mail file, the page now shows Confirm email off, as the environment runs, instead of on. `lab/mail_config.py` accepts a file without these five keys. Live check (`lab/auth-names-check.py --only mail_settings`, [evidence](docs/evidence/settings-routes-2026-10-30/mail-settings-names.json)): through the sign-in settings with no mail file, Confirm email off left a new user unconfirmed, the hourly limit refused the second mail, and the minimum interval was sent as seconds and obeyed. The two 5 minute limits (sign-in emails, token verifications) showed no refusal on `/magiclink`, `/otp` or `/verify`, with or without a client address header, so their effect is not shown; they keep the names the mail file always used.
- **One shared helper for the settings routes.** The SMTP, phone and email template routes use `src/control/settings-route.ts` for their path match, management check, stale page and spacing refusals, order of reading and writing, revision and error answers, and for private file reading and writing. URLs, statuses and messages are unchanged, except that a mail file that is not JSON answers 409 instead of 500, a file holding only `null` is refused instead of read as absent, and a first SMTP save creates the directory.

## [0.10.1] - 2026-10-30

### Fixed

- **A refused phone or email template save no longer stays on disk, and template bodies follow what Auth runs.** When an apply fails, the phone settings and the email templates go back to the copies the running Auth was started with, so one bad save no longer fails every later apply. The internal template route serves the applied copy, so a pending or rolled back save cannot change the mail the running Auth sends. Template braces must open and close in order, and the templates notice reads the right mail file.
- **Safer SMTP host and output masking.** A custom SMTP host that is this machine, link-local (the cloud metadata address included), a numeric form of those, or one of this installation's own containers is refused. A function's Output also hides the JSON escaped and base64 forms of its secrets (values of six characters or more).

## [0.10.0] - 2026-10-30

### Added

- **Phone, Hooks, Single Sign-On and Audit Logs pages.** Phone writes an environment's SMS provider (Twilio, Twilio Verify, MessageBird, Textlocal, Vonage) and credentials to a private file through `PUT /management/v1/environments/{id}/sms` (credentials never returned), with the phone confirmation switch and an hourly SMS limit; the environment's Auth gets outbound access while it is on. Hooks offers the seven Auth hooks as Postgres functions (`pg-functions://postgres/<schema>/<function>`). Single Sign-On turns SAML on (the console makes and keeps a per environment signing key, `<runtime>-saml.json`, mode 0600) and lists, adds from pasted metadata XML, and removes SAML identity providers through the environment's own Auth admin API (`GET`, `POST /sso-providers`, `DELETE /sso-providers/{id}`, audited by provider id). Audit Logs reads Auth's audit entries newest first (`GET /auth-audit`) and has the Write audit logs switch. The two admin API pages need no database access and reach only three allowed Auth paths with that environment's own service token. The live check tried each variable: a Postgres function hook adding a claim, SAML with the admin API, every SMS provider's variables, the SMS limit and SMS autoconfirm. Not tried: delivery through a real SMS provider, a sign-in through a real identity provider, and the other six hooks one by one.
- **Email templates in the console.** Emails > Templates edits the subject and HTML body of the six messages Auth sends (Confirm sign up, Invite user, Magic link, Change email address, Reset password, Reauthentication), with Source and Preview views, the list of variables and Reset to default, through `GET` and `PUT /management/v1/environments/{id}/mail-templates`. The pinned Auth fetches a template from an address when it sends, so the console writes a private templates file per environment (`<runtime>-templates.json`, mode 0600, with a random token) and serves the bodies on its internal route on the runtime network (`/<runtime>/mail-templates/<token>/<message>`, one plain 404 for an unknown environment, token or message; the token is the protection, since other containers on the host's Docker networks can reach that address). The runtime now records that route's address (the host's address on the runtime network and the Studio port) in the Studio state at start, so the route is open without a Studio session. Auth gets `GOTRUE_MAILER_TEMPLATES_*` and `GOTRUE_MAILER_SUBJECTS_*` through the same apply with rollback; a bad file refuses the apply instead of sending default messages. Evidence: the live check fetched all six templates from the network gateway address, sent each custom subject and body, and showed that Auth keeps a fetched body in memory.
- **A live check of the Authentication setting names, and five settings removed because they do nothing.** `lab/auth-names-check.py` starts the pinned Auth on a private network with a throwaway database and tries each setting with a real request (sign-up refused, token lifetime, password length, single session, session time box and idle timeout, factor limits, manual linking, the audit switch, leaked passwords, a hook, SAML with the admin API, SMS provider variables and the SMS limit, and a mail template fetched from the network gateway address). Five settings the page offered are gone because the check showed no effect: refresh token rotation and reuse interval, and the token refresh, anonymous and Web3 rate limits (Auth keys them by a client address header the gateway does not send). A file that still holds them loads, and the keys are dropped. The empty Rate Limits tab is gone; the Emails page's limits now say they count for the whole environment. Evidence: `docs/evidence/auth-live-names-2026-10-30/`.
- **CPU time, memory and per function output from the runtime's event worker.** The Edge Functions runtime now starts with `--event-worker`: a small worker (`lab/functions/event-worker`) keeps what it learns from each worker's boot, log and shutdown events and sends it to the container's main service, signed with a key derived from the environment's secret so no function can pass for it. The Overview gets a **Usage** section (average and longest CPU time per worker, memory, heap and external share) and Logs shows what each function wrote, with the environment's own secret values hidden. The numbers are per worker lifetime and arrive when the runtime drops a worker, about a minute after its last call; they live in memory in the Functions container. **Each environment's Functions container is recreated once at the next start**, because its command changed, and a later change to the event worker recreates it too. Live check in the hardened container: [evidence](docs/evidence/functions-f3-event-check-2026-10-30/results.json).
- **Edit and deploy functions in the console, with a start check.** The Edge Functions Code tab is now an editor (line numbers, colours, **Add File**, rename, delete, shared files, unsaved changes asked about before you leave), and **Add function** starts from a template (Hello world, Webhook, Read with supabase-js, Browser calls with CORS). Settings has a **Verify JWT** switch (`PATCH /management/v1/environments/{id}/functions/{name}` with `{"verify_jwt": true|false}`, no new version). Secrets takes several rows and pasted `NAME=value` lines. A deploy to a running environment first asks the runtime to start the new version (`POST /_sb_boot/<name>/<version>` on the main service, service role token, two at a time); a version it cannot start (a syntax error, an import it cannot find) is refused with `422` and the runtime's message and the running version stays; a deploy without an index entry point (`index.ts` or `index.js`) is refused earlier with `400`. An error thrown at the top of a file, or a missing `Deno.serve`, is not caught, and the file's top level code runs once during the check. The main service changes, so each environment's Functions container is recreated at the next start. The 100 function cap is checked again when a deploy goes live. Live check in the hardened container: [evidence](docs/evidence/functions-f2-boot-check-2026-10-30/results.json).
- **Confirm email and the mail limits.** The Emails page gains Confirm email (the opposite of `GOTRUE_MAILER_AUTOCONFIRM`) and the email rate limits and minimum interval per user, written to the same mail file with the same checks.
- **Custom SMTP in the console.** A new Emails page (Enable custom SMTP, sender email and name, host, port, username, password) writes the environment's private mail file through `PUT /environments/{id}/smtp` and applies it with the existing sign-in apply (swap with rollback). The password is written and never returned (answers say only whether one is set); off removes the file; the audit lists key names only. This reverses the earlier rule that the SMTP password never crosses the management API, on purpose. Overview now records the mail state after an apply (applied, or off when mail was removed). Turning SMTP on makes new sign-ups confirm by email (the file defaults to confirmation required), and Applied does not verify delivery; a failed apply puts the previous mail file back. Step A3a of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md); templates, Confirm email and the mail rate limits follow.
- **An Edge Functions section in the console, read side.** Owners and admins get **Edge Functions** in each environment, laid out like the Supabase cloud's: a function list with search, sort, refresh, address copy, created, updated and deployments; per function Overview (invocations, 5xx and 4xx rates, errors since the last deploy, response time, over 15 minutes to a day), Logs (the calls of a time range, from the last 200 it holds, with tokens in paths hidden, and the runtime's output for the whole environment), a read only Code viewer with a file tree, Settings (invoke examples in five languages, delete by typing the name), a Test panel that sends from the browser with the publishable key, and a project shaped ZIP Download; and a Secrets page with a digest per secret (HMAC-SHA256 with a key of the environment, kept outside the folder the runtime mounts), the time it was set and the default secrets. The numbers are counted in memory by the gateway for deployed function names only and start again at a restart. A deploy now keeps when the function was created and counts deployments, and refuses a 101st function. The old Edge Functions block under Services is gone; its switch, deploy command, one file deploy and secrets moved into the new section. Editing in the console and CPU and memory per function come in later steps ([plan](docs/engineering/plans/2026-10-30-edge-functions-execution.md), [guide](docs/guides/edge-functions.md#the-edge-functions-page)).
- **Sessions, Rate Limits, Attack Protection and Multi-Factor pages.** New pages in the Authentication workspace for single session per user, refresh token rotation and reuse interval, the token refresh, anonymous and Web3 rate limits, leaked password protection, minimum password length, TOTP enroll and verify switches and the maximum number of factors. They come from one registry entry each (`store: config`), are validated by type and range on both sides, and add a variable to Auth only once set, so existing environments keep the environment they have. Key ownership is now one set: the sign-in settings own these keys even where the mail settings own the rest of a prefix. Names for sessions, refresh tokens, the three rate limits, TOTP and the factor count were found in the pinned Auth binary; the names for leaked password protection and minimum length come only from the Studio schema, and none was tried on a running Auth (a wrong name is ignored without an error). Leaked password protection opens outbound network access for that environment's Auth. The captcha setting is not offered because its names could not be confirmed. Step A2 of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md).
- **Sign In page: manual linking and provider switches, spaced saves, audit of changed settings.** New switches: Allow manual linking (`GOTRUE_SECURITY_MANUAL_LINKING_ENABLED`), and per provider Skip nonce checks (Apple, Google) and Allow users without an email; Google takes a comma-separated client ID list. A switch that is off adds no variable, so existing environments keep exactly the environment they have. Settings files are version 2 when one of them is on (version 1 files still load). The settings file keeps the version 1 shape until one of these switches is on, so a build from before them still reads it. A second save of one environment within 10 seconds of its last state change is refused (429, with `Retry-After`), and the audit event now lists the platform key names a save changed, never values. Step A1 of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md). Only the manual linking name was confirmed in the pinned Auth binary; `EXTERNAL_<PROVIDER>_SKIP_NONCE_CHECK` and `EXTERNAL_<PROVIDER>_EMAIL_OPTIONAL` come from Studio's schema and are not yet confirmed against a running Auth.
- **A tabbed Authentication page with provider logos.** Sign-in settings now read like the Supabase cloud's Authentication pages: a menu (Sign In / Providers, URL Configuration, Sessions), user signup switches, a list of 19 providers each with its logo and an Enabled badge, a side panel per provider (switch, client ID, secret, server address, callback URL with Copy), one sticky save bar with Discard and a state badge. Surfaces differ by tone, with no bordered boxes; the layout uses logical properties so it mirrors right to left; the logos are local files (`ui/assets/providers`, Simple Icons, CC0, source and trademark note recorded; Microsoft and WorkOS show a letter). The save request is unchanged. Replaces the read only section of the previous entry. Step U0 of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md).
- **Safer saves of sign-in settings.** A save from a page opened before another save is refused (409) instead of overwriting it; a save made while an apply runs stays pending; restarts and upgrade replacements use the last applied settings (`<runtime>-auth.applied.json`, mode 0600) and refuse an unreadable, newer, linked or world readable file instead of starting Auth on defaults; an interrupted apply puts the old Auth back first; a stale `.pending` copy is removed. Step A0.5 of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md).
- **Authentication configuration view per environment.** `GET /management/v1/environments/{id}/auth-config` and a read only section under Sign-in settings show everything the environment's Auth is set to (site URL, redirect URLs, signup, anonymous sign-ins, session lifetimes, every provider with its client ID, secret state and callback URL) under the Supabase platform's key names, from one registry (`src/control/auth-config-registry.json`) that a Python test checks against what `lab/auth_settings.py` writes. A secret is never returned, only whether one is set; each environment sees only its own settings; owners and admins only. First step of the [Authentication workspace plan](docs/engineering/plans/2026-10-30-auth-providers-workspace.md); editing stays in the sign-in form for now.
- **A locked down Edge Functions runtime and real worker limits.** The container now starts with every capability dropped, `no-new-privileges`, a read-only root, 32 MiB tmpfs mounts for `/tmp` and the Deno cache, and the owner of the environment's private directory as its user, never root; a kept container is accepted only if `docker inspect` shows the same, and a `docker exec` check removes one whose user cannot read its code or secrets. Each worker gets a `permissions` object (`allow_net`, `allow_env` and `allow_import` open, reads and writes limited to `/tmp`, three literal metadata addresses in `deny_net`) and a 64 MiB `/tmp` quota. `netAccessDisabled` and a top level `importMapPath` were removed: the pinned runtime ignores both. `deny_net` is defence in depth only, since it takes no CIDR range and the bare decimal form, the NAT64 form and any host name that resolves to a listed address pass it. The quota is per worker, but `/tmp` is one 32 MiB tmpfs shared by all workers, so that is the real bound, and the Deno cache is a separate 32 MiB tmpfs whose overflow fails later imports until the container is recreated. Proven by unit tests and by measurements on one host with the pinned runtime ([sandbox](docs/evidence/functions-sandbox-hardening-2026-10-30/results.json), [container](docs/evidence/functions-container-hardening-2026-10-30/results.json)); not run in a full Sbarbase installation, and these are untested: native-addon npm packages under the `noexec` tmpfs (they may fail to load), the kept-container path against a real upgrade restart, a host that gives these containers swap (it hides the out of memory kill), rootless Docker, a remapped user namespace, SELinux enforcing, and Docker Desktop or another OS. The 384 MiB container holds four warm functions but is thin under a large call, and after an out of memory kill it stays down until the next start of Functions.
- **Edge Function calls are counted and capped per environment.** Function calls now reach Usage like the other services, and at most 4 run at once in an environment (`serviceConcurrency.functions` on a route replaces the 4); the next answers `429` with `retry-after: 1`, so a long call cannot take the share REST and Auth need. The 150 s deadline applies to the header wait and the streamed body, a client that leaves before the response headers arrive is answered and counted as `408`, one that leaves during a streamed body frees its slot and keeps the status already counted, and an invalid key is refused before it can take a slot. A function with `verify_jwt` off still accepts calls with no key, so anyone who can reach it can fill those 4 slots for that environment. Proven by unit tests (`tests/functions-gateway.test.ts`); never measured under load on a real server.
- **Host firewall rules for Edge Functions, generated and never applied.** `deploy/functions-egress.sh plan | verify-plan | apply --output PATH` builds rules from the subnets Docker reports that keep functions off the host's addresses and off private, link local, CGNAT and other Docker networks, with an allow list for the host's resolvers and for what you name (`--allow`, `--allow-host`). The `nft` output is the reference; `ufw` and `firewalld` output are translations. The tool writes a file and prints the activation commands and changes no firewall. Proven by unit tests and by the tool's own plan checks; the ufw and firewalld output was not run on a real firewall (the ufw output also lacks the reverse-path check and neighbour discovery handling), and the rules have not been applied or probed from a function on a real server, which needs the owner's approval. See [server deployment](docs/guides/server-deployment.md#limit-what-edge-functions-can-reach).

## [0.9.2] - 2026-10-30

### Added

- **Session lifetimes per environment.** Sign-in settings gain three optional lifetimes: the access token lifetime (`GOTRUE_JWT_EXP`), the longest a session may stay open from its start (`GOTRUE_SESSIONS_TIMEBOX`), and the idle time before a sign out (`GOTRUE_SESSIONS_INACTIVITY_TIMEOUT`). An empty field sets no variable, so no cap applies and a person who signs in once stays signed in, which is what a store that never signs a buyer out wants; the three fields are what turns that default into a deliberate setting. Saved in the environment's sign-in settings file, applied by recreating only that Auth, proven by unit tests on the Python and the control plane side and by the console's own save path.
- **A typed sign-in code instead of a link.** The mail file gains one optional key, `magic_link_template`, which sets `GOTRUE_MAILER_TEMPLATES_MAGIC_LINK` and nothing else. `lab/email-templates/magic-link-code.html` is the body this ships for it: the one time code from `{{ .Token }}` at the top, the link as the fallback. With `otp_length: 8` a buyer types eight digits. An empty value keeps the pinned Auth's own body, which carries the link only, so nothing changes for a file written before this key existed.

### Fixed

- **Google, GitHub and the other OAuth providers could not sign anyone in on a real server.** Auth fetches the provider's discovery document and token endpoint itself, but an environment's Auth stayed on the `--internal` network unless the environment had a mail configuration, so `authorize?provider=google` answered 400 with a failed lookup of `accounts.google.com` while the console showed the provider as applied (found on a real Google sign-in, issue 22). An environment now joins the outbound network while it has a mail configuration or at least one enabled provider, from every path that creates or recreates its Auth (start, mail, sign-in settings, the rolling replacement); removing the mail file no longer cuts off an environment whose provider is still enabled. Proven by unit tests on those paths and a mutation check; a real provider sign-in through the fixed server is still unmeasured.

- **No environment's Auth could reach a mail relay outside the host.** Every environment ran on a network created `--internal`, so its Auth resolved no public name and opened no public connection: measured on a real installation, `getent hosts smtp.resend.com` failed and `nc -z smtp.resend.com 587` reported BLOCKED from `sbarbase-durable-net`, while the same probe on `sbarbase-durable-outbound` reported REACHABLE. A mail configuration could therefore be written, validated and applied, and the first message would still have failed. An environment now joins the outbound network while, and only while, it has a mail configuration, from every path that creates or recreates that Auth (start, mail, auth, the rolling replacement), and leaves it again when the file is removed. An environment without mail keeps the internal-only posture, and the management realm never joins it. Proven by unit tests and by the two measurements above; a real message through a real relay from a real server is still unmeasured.

### Changed

- **The console's Email section shows how to turn mail on.** For an environment with no configuration it renders the two host commands with that environment's runtime id, and one line saying why there is no form: the SMTP password never crosses the management API. The mail answer carries the runtime id for that, and a configured environment shows the sign-in mail body it applies.

## [0.9.1] - 2026-10-26

### Added

- **A live measurement of the upgrade features (cp9), and its numbers in the docs.** A `workflow_dispatch` workflow on a free GitHub Actions runner installs a release, creates one environment, pulses it every 200 ms and runs the real scenarios: a plain restart, a code only release with the handover on, the same release after a `SIGKILL` of the serving console, a PostgREST pin change with the rolling on and off, and a database restart through the window. Measured: plain restart 22.2 s of failed environment reads; handover 7.2 s with no failed console read; REST rolling no failed read in 1826 s against 12.8 s; database window 3.4 s; killed handover refused at H2 and fell back at 29.8 s. The arm with the handover off recorded no failure and is published as inconclusive. A runner is not a production server: one environment, no customers.

## [0.9.0] - 2026-10-26

### Added

- **Soft delete and restore of an environment on an ordinary installation.** Delete used to answer `409` "Exact ownership inventory unavailable" on every installation. Now the exact inventory is recorded at provisioning for new environments and derived at delete time from verifiable durable state for existing ones (exact container names and owner labels, the database and logins named by the runtime id); anything ambiguous or foreign refuses with a precise reason and changes nothing. Delete stops that environment's containers, closes its logins and removes its published address, crash safe with a journal written before each effect; restore reverses exactly what was recorded. Purge is still refused, because it would remove objects of the shared PostgreSQL cluster and Storage process. Every external call is bounded by a timeout, and an environment in a lifecycle state other than active is not probed by the upgrade health check. Proven by unit tests and stubs only; never run on a real server.
- **`lab/rolling.py repair --force ENV`** repairs a swap record the ordinary repair does not recognise, never removing a container it did not create; a start now holds only that environment (its reason shows in `lab/upgrade.py status` and `current.json`) and starts the others.

### Changed

- A fenced environment waits for a roll like a paused one; an environment that is not running is skipped with a visible reason and retried by itself when it runs again; `x-sbarbase-pid` is sent only by a process taking part in a handover; the database window re-reads the upgrade hold marker under its locks.

## [0.8.0] - 2026-10-26

### Changed

- **The in-place handover, REST rolling and the database window are on by default.** `SBARBASE_HANDOVER` is on unless it is `0`; `SBARBASE_ROLLING_SERVICES` unset means `rest` is replaced side by side after a pin change (Auth stays opt in with `auth,rest`; `none`, `0`, `off` or `false` turns rolling off); `SBARBASE_DATABASE_WINDOW` makes `lab/upgrade.py database --window SETTING` available unless it is `0` (the window still runs only when you ask for it). Each opt out restores the previous behaviour. These are proven by unit tests and by one real code only channel update; they have not been measured under load or fault injection on a real server. The Docker launcher now forwards `SBARBASE_ROLLING_SERVICES`, `SBARBASE_DATABASE_WINDOW` and `SBARBASE_DATABASE_WINDOW_SECONDS`. Known limits: `docs/engineering/upgrades-known-limits.md`.

## [0.7.1] - 2026-10-26

### Added

- **A bounded held window around a PostgreSQL restart, off by default** (`SBARBASE_DATABASE_WINDOW`, `lab/upgrade.py database --window SETTING`): it checkpoints, holds application traffic for at most 30 seconds by default (cap 120), restarts the database, checks it answers and releases the hold, and a failure or timeout always releases it. It takes the upgrade lock and is refused during a backup. Proven by unit tests only.
- **Operator documentation for upgrades**: what an upgrade does by release type, the three off by default switches, the graceful stop, and a list of every known limit (`docs/engineering/upgrades-known-limits.md`).

## [0.7.0] - 2026-10-25

### Added

- **`deploy/hyperdrive`**, run from your own computer, creates a Cloudflare Hyperdrive for an environment in one command (opens external access, replaces the developer password, makes the Workers VPC service, creates the Hyperdrive, prints its id). The database access guide gains a short Hyperdrive version and a table of the Supabase commands that work with `--db-url`.
- **Rolling service updates, off by default** (`SBARBASE_ROLLING_SERVICES`): after an upgrade, an environment's Auth and REST are replaced one environment at a time, with the first environment as a canary, a health watch, and a way back. Proven by unit tests only.
- **In-place handover of the console process, off by default** (`SBARBASE_HANDOVER`): an update that changes code only can replace the console process without a full restart, which shortens the interruption and does not remove it. Proven by unit tests only.
- **A design study for per-environment Query Performance** (`docs/engineering/plans/2026-10-24-query-performance-per-environment.md`), no code.

### Changed

- **The console process now stops gracefully.** On a stop it refuses new connections, lets running requests and database sessions finish (up to `SBARBASE_DRAIN_MS`, 15 seconds by default), then exits, so a stop takes at least about a second and can take the full drain time when a client keeps a database session open. Its fixed ports are bound so that a newer process can share them during a handover, which also means a second process of the same user can bind a port a first one holds.

## [0.6.4] - 2026-10-24

### Fixed

- **External database access: hardening found by a third independent review.** A renewed database certificate that the listeners could not take is tried again at the next check instead of being logged as renewed; a stop during a renewal no longer leaves a TLS server listening; the loopback TLS handoff is keyed by its inner port as well; `database-password` makes its output file before it changes the password, so a bad path costs nothing, and names the saved file when the change is refused; two simultaneous password writes no longer share one temporary file.

## [0.6.3] - 2026-10-24

### Added

- **`sbarbase database-password <environment> --output FILE`**, on the server, replaces an environment's developer database password as the console's New password does, waits for the supervisor to apply it, and writes the connection string to a new private file; the password is never printed. For an operator who cannot sign in to the console.

## [0.6.2] - 2026-10-24

### Added

- **`sbarbase database-external <environment> [on|off]`**, on the server, shows or sets an environment's external database access from the shell, the twin of the console's **Allow external connections** button for an operator who cannot sign in (same catalog row and audit entry, actor `local:operator`; it needs database access on).

## [0.6.1] - 2026-10-24

### Added

- **External database access, for services that cannot use an SSH tunnel (Cloudflare Hyperdrive, for one).** The database listener on 6543 now answers `SSLRequest` and serves TLS 1.2 or newer from a private certificate authority Sbarbase makes on first start with the server's `openssl`. A second listener (loopback, port 6545) takes TLS only and only for environments whose owner or admin pressed **Allow external connections** (off by default, needs database access on, audited, closed again when database access is turned off). It spends every attempt from a per-address and a total budget and closes an environment's external logins for a minute after ten failed passwords; PostgreSQL's SCRAM stays the only password check. `GET` and `PUT /management/v1/environments/{id}/database/external` and the console section show the state and the certificate authority to download. Sbarbase opens no firewall port and holds no Cloudflare credential: the guide shows the Cloudflare Tunnel with Workers VPC path to Hyperdrive, and a tunnel or private network works for any other client. The console also copies the SSH command and the local connection string, and `deploy/db-tunnel` opens the SSH tunnel with one command. The control catalog is now schema version 5 (a new table). New settings: `SBARBASE_DATABASE_EXTERNAL_PORT`, `SBARBASE_DATABASE_EXTERNAL_BIND`, `SBARBASE_DATABASE_TLS_NAMES` ([guide](docs/guides/database-access.md), [design](docs/engineering/EXTERNAL-DATABASE.md)). Not yet run through a pooler: one login keeps its 10 connection limit.

## [0.6.0] - 2026-10-23

### Added

- **Exposed schemas for the Data API.** Each environment chooses which schemas its PostgREST serves, in order, with its extra search path and row limit: in the console (Database connection, Data API) or through `GET` and `PATCH /v1/projects/{ref}/postgrest`. They are kept in the environment's own database, never in the catalog the cluster shares, applied by a PostgREST pre-config function and served after a reload, and written again at every start. The first start after upgrading recreates each REST container once to name that function. A schema that does not exist is refused, and a refused change is never served, not even after a restart. While a schema is exposed, the database refuses to rename or drop it, because PostgREST answers every request with 503 once one exposed schema is gone; a schema missing at start is left out and reported. Platform schemas (`auth`, `storage`, `vault` and the rest) are never exposed. Studio gets the same schemas. 60 of 60 live checks passed on 2026-10-22 against the pinned images ([evidence](docs/evidence/data-api-checks-2026-10-21.json), [guide](docs/guides/data-api.md), [design](docs/engineering/DATA-API-SCHEMAS.md)). Not yet run on a real server.
- **Scheduled jobs and outgoing HTTP.** Owners and admins turn **Scheduled jobs** on for an environment (Services, or `PUT /management/v1/environments/{id}/scheduled-jobs`). Its database then has `cron.schedule`, `cron.schedule_in_database`, `cron.unschedule`, `cron.alter_job`, `cron.job` and `cron.job_run_details` as pg_cron 1.6.4 has them, and `net.http_get`, `net.http_post`, `net.http_delete`, `net.http_request_queue` and `net._http_response` as pg_net 0.20.4 has them, so migrations and code written for Supabase Cron and pg_net work unchanged. The real extensions stay refused: they serve one database per cluster and reach every network the server does. A scheduler process runs each environment's jobs as its developer role, through a login of its own that a write pause, a fence and a restore close, at most 2 at once and 10 minutes each, and performs the queued requests under a policy that refuses loopback, private and reserved addresses, the server's own addresses and its Docker networks, except the installation's public address. 39 of 39 live checks passed on 2026-10-22 against the pinned PostgreSQL ([evidence](docs/evidence/scheduled-jobs-checks-2026-10-22.json), [guide](docs/guides/scheduled-jobs.md), [design](docs/engineering/SCHEDULED-JOBS.md)). Not yet run on a real server.

### Fixed

- **Changing Realtime, database access or Studio no longer waits without bound on the environment's database.** Writing the database's connection limit, and Realtime's grants on the database, had no timeout, under the installation's operation lock or the write pause lock, and a session of the environment could hold the database's catalog row (a no-op `GRANT ... ON DATABASE` left open), stopping every other environment's changes and starts. It now gives up after 5 seconds and the change reports a failure instead ([design](docs/engineering/SCHEDULED-JOBS.md)). Not yet run on a real server.
- **Turning direct database access on no longer holds up other environments.** It gave the developer login its rights on the tables in `public` while holding the installation's operation lock and the write pause lock, so a transaction one environment's application left open on a table could delay every other environment's console changes and starts, and every Studio start, by up to 10 seconds per statement. The rights are now given under that environment's own lock; the operation lock is held only for the checks and to open and publish the login, and opening it first ends the environment's sessions that hold the login's role row ([evidence](docs/evidence/developer-login-lock-checks-2026-10-22.json), [design](docs/engineering/DATABASE-ACCESS.md)). The supervisor now runs sign-in settings, Realtime, Edge Functions, database access and signing changes in one child per environment, and a change that loses the lock to another one is asked again instead of shown as failed. Not yet run on a real server.

### Security

- **A tenant could become a superuser of the shared cluster.** When direct database access was turned on, and whenever Studio started, the platform ran a superuser script in the environment's database that called `pg_has_role(...)` without naming its schema. The environment's developer login can create functions in `public`, and a `public.pg_has_role(text, text, text)` matched that call better than PostgreSQL's own, so it ran as the superuser. Those scripts now run with `search_path` set to `pg_catalog` and name it on their calls; a live check plants such a function and runs the old script as a control ([design](docs/engineering/DATA-API-SCHEMAS.md)). Present since 0.5.8. The event trigger guards also search the session's temporary schema last.

## [0.5.13] - 2026-10-19

### Fixed

- Signing out of the console no longer ends your access tokens. The console also signs out on its own when a session expires, so an agent's MCP or `/v1` access could stop with `401` while nobody did anything. Tokens now end only as the agents guide says: when revoked or expired, at a password reset or factor recovery, or when the MFA policy changes. A token already stopped this way stays stopped: create a new one.

## [0.5.12] - 2026-10-19

### Fixed

- A Studio opened before the upgrade to 0.5.11 also starts again by itself after it: on that first restart the activity record does not exist yet, so the time the console last asked for the session is used.

## [0.5.11] - 2026-10-19

### Fixed

- **No sign-in flash.** With a stored sign-in, the console waits on a neutral screen while the Auth client checks it, instead of showing the sign-in page for a moment and then the console. With no stored sign-in, the sign-in page shows at once.
- **Studio stays open while it is used.** The Studio session renews itself while its tab makes requests. It ends after eight hours without any request or seven days after it was opened from the console; signing out, a password change or losing the role still end it at once ([Studio](docs/guides/studio.md)).
- **Studio comes back after a restart.** A restart or an upgrade brings back a Studio used in the last eight hours, after the upgrade is confirmed. An open tab shows "Studio is starting", reloads by itself and continues.

## [0.5.10] - 2026-10-19

### Fixed

- **Docker installs can set a public Studio address.** `compose.yaml`, `deploy/compose.sh` and the host preflight now forward `SBARBASE_STUDIO_HOST`; before, the console container could never receive it ([Studio](docs/guides/studio.md)).

## [0.5.9] - 2026-10-19

### Fixed

- **Studio's SQL editor and Edge Functions page.** Saved SQL snippets now live in a folder inside the Studio session instead of failing with "SNIPPETS_MANAGEMENT_FOLDER env var is not set", and the Edge Functions page lists each function's deployed code, copied when Studio opens.
- **Studio can change what migrations made.** Studio works as the environment's developer login, as Supabase Studio works as a project's `postgres` user: it can alter tables, policies and row level security, and sees the developer's own schemas, Vault and migrations. What it creates belongs to the same owner ([Studio](docs/guides/studio.md)).
- **A blocked Studio route is caught.** Opening Studio checks its route to Auth and Storage from inside Studio, not from the host, and names the firewall when it is blocked. The server guide lists the two narrow firewall rules it needs ([server deployment](docs/guides/server-deployment.md#firewall-two-routes-from-containers-to-the-host)).

## [0.5.8] - 2026-10-19

### Added

- **One setting for Studio's public address.** `lab/studio_address.py plan DOMAIN` lists what to provide once (a wildcard DNS record, a certificate that covers it and the setting); `apply DOMAIN` writes `SBARBASE_STUDIO_HOST` for the console and the TLS proxy and restarts them; `verify DOMAIN` says which step is missing. The TLS proxy now reads `SBARBASE_STUDIO_HOST` when it has no `--studio-host` flag, so one drop-in configures both. After that, every new project opens Studio with no DNS or certificate work ([Studio](docs/guides/studio.md)).

### Changed

- The console's notice for a server without a public Studio address now names this setup instead of only the variable.

### Added

- **An environment can create its own schemas and use Vault.** The developer login, used by direct database access and by agents, may now create and rename schemas of its own, so migrations that keep code outside `public` (for example `create schema app`) run as on Supabase, and so does `supabase db push`. Names the platform uses (`auth`, `storage`, `extensions`, `realtime`, `vault`, `cron`, `net`, `graphql`, and any starting with `supabase` or `sbarbase`) stay refused, by a guard the login can neither drop nor disable. Vault is installed in each environment with the grants Supabase gives `postgres` and `service_role`, without the right to pass them on; all environments of a server share its root key. The `supabase_realtime` publication now always exists and belongs to the login, so migrations can add tables to it as on Supabase. Extensions that reach the server's network or see every environment, such as `http`, `pg_net`, `dblink`, `postgres_fdw`, `pg_stat_statements` and `pgroonga` (which can write files anywhere on the server), are now refused for the environment's login; the image's supautils used to create them for it. Turn database access off and on once for an environment whose access is already on ([database access](docs/guides/database-access.md)).

### Fixed

- **Turning database access off and on keeps a migration's narrower default grants.** Each time access was turned on, the API's default grants to `anon`, `authenticated` and `service_role` were given again, undoing a migration that had revoked them, for example execute rights on new functions. They are now given once, the first time ([database access](docs/guides/database-access.md)).

### Changed

- **The console has a new look.** Colours, spacing and components follow Supabase's design tokens, each environment screen has one primary action, and a contrast check guards the palette (pull request 18).

## [0.5.7] - 2026-10-19

### Fixed

- **Studio opens behind the TLS proxy.** Studio compresses its pages. The console's Studio proxy and the TLS proxy both let Bun's `fetch` decode the body while keeping the `content-encoding` header, so the next hop received plain bytes labelled gzip and failed with a decompression error (Cloudflare showed 520). Both now pass Studio's bytes and header through together.

## [0.5.6] - 2026-10-19

### Fixed

- **No more sign-outs after an hour away.** The console's access token lasts an hour. A request made with an expired one signed you out and deleted the session on the server, although the session could have been renewed. The console now renews the token and retries once, and signs out only if the renewal is refused.

## [0.5.5] - 2026-10-19

### Fixed

- **No more Studio tab that never loads.** On a server whose Studio has no public address, opening the dashboard from the console's public address left a tab reading "Starting Supabase Studio" forever. The console now learns this from the Studio status before opening anything, shows the SSH tunnel steps and opens no tab; a tab already opened for a new project says what happened and closes ([Studio](docs/guides/studio.md)).

### Changed

- **The console looks and reads like Supabase.** Every page now uses Supabase's own colour tokens and the look of its buttons, fields, cards, alerts, badges and tables, with stronger edges so sections, fields and buttons no longer blend into one grey. The main action on a screen is the one solid green button, read-only values such as tokens and commands look different from fields you type into, a new key or token appears in a green-edged panel, and focus rings are visible in both themes. Text and control edges meet WCAG AA in light and dark, checked by `tests/ui-contrast.test.ts`. Routes, labels and behaviour are unchanged ([console design](docs/reference/console-design.md)).
- **The console keeps you signed in for thirty days.** `SBARBASE_MANAGEMENT_SESSION_SECONDS` now defaults to 2592000 instead of 43200, so a sign-in no longer ends after twelve hours. Set a lower value to keep the old behaviour ([configuration](docs/reference/configuration.md)).
- **Telegram notifications are in plain words.** A message says what happened, where (by name), the likely cause and what to do, without ids, field names or reason codes. Email, the webhook and the catalog keep the full record ([OPERATOR-NOTIFICATIONS](docs/engineering/OPERATOR-NOTIFICATIONS.md), section 3.6).

## [0.5.4] - 2026-10-18

### Changed

- **Install with Docker is the front door.** The Docker guide starts from a bare Ubuntu 26.04 machine: `docker.io`, `docker-compose-v2` and `docker-buildx` from the distribution, then clone, check, up and bootstrap, with a note to wait for the console before bootstrap ([Install with Docker](docs/guides/docker.md)). The website's install section shows these five commands; the systemd quickstart stays linked as "Without Docker". `lab/vm-docker-rehearsal.sh` ran it in a local virtual machine, 14 checks ([vm-docker-install-checks.json](docs/evidence/vm-docker-install-checks.json)); not yet on a real server.

### Security

- **Edge Functions leave the shared runtime network.** Each environment's Functions container reaches only its own Auth and REST on that environment's network, and Storage through a host listener that pins the environment's tenant, so one environment's functions cannot reach another's services ([FUNCTIONS-NETWORK-ISOLATION](docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md)). The outbound network refuses traffic between its containers.

## [0.5.3] - 2026-10-17

### Added

- **Studio from the public address.** `SBARBASE_STUDIO_HOST` sets each environment's Studio address as a pattern, such as `studio-{id}.example.com`, and the TLS proxy serves it with `--studio-host`. With a wildcard DNS record and a certificate that covers it, the console opens Studio on that address instead of `<id>.studio.localhost`, which only an SSH tunnel reaches ([Studio](docs/guides/studio.md#open-it-from-anywhere)). Until then, the console opened from its public address explains the tunnel instead of opening a tab the browser cannot reach.

### Security

- The TLS proxy serves only its public host, loopback names and matching Studio addresses, and answers any other host with 421, so a wildcard DNS record never shows the console under another name.
- On a public address, Studio's session cookie is `__Host-` and `Secure`; a Studio entry ticket works once; a request that changes something must come from the Studio page itself; a request another site starts is refused, except a plain visit, which lands on the project home without its query.

### Fixed

- Studio containers no longer show as unhealthy while they serve: the image's own health check asked an address Studio does not listen on.
- Notices on the Functions, Sign-in, Database, Environments and Updates pages kept their original look; a style added in 0.4.0 had changed it.

## [0.5.2] - 2026-10-17

### Fixed

- A table an agent created through the MCP endpoint or `/v1` answered 404 to the app until PostgREST reloaded its schema some other way. After a migration or a query that may change data, the server now tells PostgREST to reload, as Supabase's database does. Found by an agent's live deploy on the real server.

## [0.5.1] - 2026-10-17

### Fixed

- **An Edge Function's answer could not be read by most clients.** The gateway passed the caller's `Accept-Encoding` to the Edge runtime, which compressed its answer; the gateway's own fetch then unpacked it but kept the `content-encoding: gzip` header, so `fetch` and supabase-js failed on the body. Upstream services are now asked for uncompressed answers; the TLS proxy or CDN compresses for the client. Found by an agent's live deploy on the real server.
- `supabase functions deploy --use-api` now lands the same layout as a console deploy: the CLI's `supabase/functions/<name>/...` and `supabase/functions/_shared/...` paths become the function's own files and its shared code.
- An access token's name may now hold parentheses, apostrophes and `@ : / +`.

## [0.5.0] - 2026-10-17

### Added

- **AI agents can deploy to Sbarbase.** A person creates a personal access token (`sbp_...`) on the console's new **Access tokens** page: scoped to everything they can reach, one organization or one project, read only or not, expiring within a year. With it, an agent such as Claude Code, Cursor or Codex connects to `https://<console>/mcp`, which runs Supabase's own MCP server (the official `@supabase/mcp-server-supabase`). It can create projects, run SQL and migrations, deploy Edge Functions, read keys, logs and security advisors ([AI agents](docs/guides/agents.md)).
- **A Supabase-compatible management API** under `/v1`, for the same tokens: organizations, projects (each environment is a project, with a 20-letter ref), API keys, SQL, migration history, Edge Functions and their secrets, logs and advisors.
- **A Claude Code skill** (`deploy/agent-skill/sbarbase/SKILL.md`) that teaches an agent what "deploy to Sbarbase" means.
- **Publishable keys can be copied again** from the API keys page, as on Supabase, since they are public by design. This applies to keys created from this version; secret keys are still kept only as a hash and shown once.

### Changed

- The developer login now keeps migration history in a `supabase_migrations` schema made for it. It still cannot create schemas. Turn database access off and on once for an environment whose access was already on.

### Fixed

- The database access guide said `supabase db push --db-url` works with the developer login. It never did: the CLI's `CREATE SCHEMA IF NOT EXISTS` needs a right the login does not have, on purpose. The guide now says so and shows `psql` instead.

## [0.4.0] - 2026-10-17

### Added

- **Secret API keys**, as on Supabase. On an environment's **API keys** page, or `POST /management/v1/environments/{id}/keys` with `{"type":"secret","name":"backend"}`, create a named `sb_secret_` key. Behind the gateway it acts as `service_role` for REST, Auth admin, Storage, Realtime and Edge Functions; a signed-in user's token still wins. A request that looks like a browser's is refused with it. Like publishable keys, it is kept only as a hash and shown once; creating and revoking keys is audited ([API keys](docs/guides/api-keys.md)).

## [0.3.2] - 2026-10-17

### Added

- **`sbarbase reset-password <email>`** sets a new password for a management account from the server's shell, for an operator who lost it. The new password is asked twice without echo; every console and Studio session of that account ends; its authenticator stays; the change is audited ([operator setup](docs/guides/operator-setup.md#a-forgotten-password)).

### Fixed

- The capability registry still named version 0.2.0 in 0.3.0 and 0.3.1, so its verifier refused them as a version mismatch. `lab/release.py prepare` now writes the version there too.

## [0.3.1] - 2026-10-17

### Fixed

- **0.3.0 could not be installed through the update channel.** Its manifest declared the Auth and Storage schema migrations as data migrations, which puts a release outside the channel entirely. Those migrations are already detected from the pin changes and need the operator's confirmation (`attended`); 0.3.1 declares none and carries the same code as 0.3.0. The one-time database restart and the way back limit above four environments are in its notes.

## [0.3.0] - 2026-10-17

Moving from 0.2.0 migrates the Auth and Storage schemas of every environment, so the way back is a restore of the backup the upgrade takes first. The first start raises the database's connection limit and restarts it once. Nothing here is claimed production ready.

### Added

- **Up to eight environments per installation** (four before). The runtime raises PostgreSQL's `max_connections` from 100 to 200 on start, the database container may run 320 processes, and the gateway admits 64 requests in flight (8 per environment). A synthetic drill of eight environment databases under full load on the 1 GiB, one CPU database tier peaked at 613 MiB ([evidence](docs/evidence/capacity-drill-2026-10-16.json), `lab/capacity-drill.sh`). On a 4 core server, eight fit only without Realtime or Edge Functions. Deleting an environment still does not free its place.
- **Telegram notifications connected from the console.** An installation owner pastes the bot token on the new Notifications page; it is checked with Telegram, kept 0600 on the server and never shown again, and the chat is bound by a one-time `/start` link. Admins see the state, only owners change it, every change is audited, and a new chat does not receive the old backlog. The notification drain rereads its configuration, so a change applies without a restart.
- **Alerts for low disk space and stopped services.** Under 10 % or 5 GiB free is a warning, under 5 % or 2 GiB critical; the database, Storage, the management sign-in or an environment's Auth, REST or Realtime container stopped for five minutes is critical. Each is reported at most once an hour.
- **An outside uptime check** (`deploy/uptime-worker/`): a Cloudflare Worker that asks `/health` every five minutes and sends one Telegram message after two failures and one on recovery.
- **The upstream currency work** ([plan](docs/engineering/plans/2026-10-14-upstream-currency.md)): upstream drift tracking; an attended move of the database onto another PostgreSQL image (`lab/upgrade.py database`); the database volume as state; rebuilding the whole cluster from one backup run (`lab/cluster_rebuild.py`) and taking it into use (`lab/cluster_takeover.py`); a per-environment write pause; backup and restore of the management database; starting the database alone for a restore after a failed Storage way back.
- **Updated upstream services:** Auth v2.197.0, PostgREST v14.18, Storage v1.83.3, Realtime v2.144.0, Edge Runtime v1.77.4, Studio 2026.10.12 and postgres-meta v0.100.0. PostgreSQL stays 17.6; 17.11 waits for its rehearsal.

### Fixed

- **Studio and postgres-meta are no longer reachable from other environments.** Neither checks its callers, and both sat on the shared runtime network, where another environment's Edge Functions or the database's `pg_net` could run SQL as the Studio login while a session ran. Each session now has its own network, postgres-meta listens on loopback inside Studio's namespace, and every start checks that nothing answers on the shared network.
- **Restore on a server installed under umask 002.** The runtime state directory was left 775 and every restore refused its journal; each creator of it now makes it 0700.

### Verified on the real server

- Upgrades from 0.2.0 through the update command with the new images, confirmed by the health checks.
- A restore of an environment from its local backup, and a restore served only from the encrypted off-site copy in Cloudflare R2; the data came back exactly as backed up.
- Studio confinement probed from a container on the shared network before and after the fix.

### Still open

- A cluster takeover whose own replacement never becomes ready has no exit (S4); its fourth part is not accepted.
- A restore onto a new server from the off-site copy, an interrupted restore, and a whole installation under real traffic have not been exercised.
- A way back to an older version refuses jobs once more than four environments exist.

## [0.2.0] - 2026-09-26, and changes until 0.3.0

These entries were listed as unreleased; they cover 0.2.0 and what was merged after its tag until 0.3.0.

### Added

- Current product and portability roadmap, Docker-only verification contract, explicit native-placement identity and scoped evidence ledger. Native activation, complete recovery and release acceptance remain open.
- Bounded native-effects verification now preserves raw ACL distinctions and enforces phase deadlines. The latest actual fixture still refuses its routine privilege model; this is verification work, not accepted native-effects operation.
- Updated current documentation and repository layout, preserving historical results and distinguishing implemented shared-runtime features from experimental native placement.

- **Rename, move and delete clients, projects and environments.** Owners and
  admins rename; owners move a project to another client they own, which
  revokes its API keys, and delete what is empty. An environment is deleted
  only while nothing runs or changes for it; its keys stop working at once
  (the gateway answers 401), and its data stays on the server. From the
  console or the management API.
- **Restore a backup onto a new installation.** `sbarbase relink <runtime>
  <backup>` recreates the backup's client, project and environment with their
  original ids, never matching by name, and `sbarbase restore` then fills it.
  Members, API keys and the signing key do not travel; users sign in again
  with their old passwords. Rehearsed onto a second VM on 2026-09-25.
- **Rotate an environment's signing key.** Owners and admins press **Rotate
  signing key** (or `POST .../signing-key/rotate`); the supervisor gives the
  environment a new JWT secret, recreates its Auth and REST, updates its
  Storage and Realtime tenants and Edge Functions, and ends every session.
  Publishable keys keep working. A rotation that stops halfway is finished by
  the next attempt or start. Also: a restart no longer drops an environment's
  direct database access from the published state.
- **Move a project from Supabase in one command.** `lab/import_project.py`
  reads a Supabase project (Cloud, self-hosted, or another environment) and
  copies it into a new, empty environment: the `public` schema with its
  policies and the source's own grants, users with their password hashes,
  every row, buckets and files with their owners, and triggers on
  `auth.users` and `storage.objects`, then compares counts before reporting
  success. `--dry-run` only inspects.
- **Encrypted off-site backup copies.** `lab/offsite.py configure` takes S3
  settings (Cloudflare R2, Backblaze B2, AWS S3, MinIO) and a passphrase on
  stdin; from then on each daily backup is encrypted on the server
  (AES-256-GCM in chunks) and copied to the bucket, the newest 30 per
  environment are kept, and `fetch` brings one back for `backup.py restore`.
- **Direct database access per environment.** Owners and admins turn on a
  developer login and get a PostgreSQL connection string, with the password
  shown once, for `psql`, `pg_dump`, `supabase db push --db-url`, Prisma or
  Drizzle over an SSH tunnel. The login works like a project's `postgres`
  user within its own database, including triggers on `auth.users` and
  Storage policies, but is not a superuser. A loopback listener (port 6543)
  passes on only developer logins to their own database.
- **Edge Functions per environment.** Deploy a Supabase project's
  `supabase/functions` folder with one command (`lab/functions-deploy.ts`),
  `_shared` and `verify_jwt` in `config.toml` included, or write a function in
  the console. Each environment that deploys one gets its own pinned upstream
  edge-runtime; functions are called through the gateway with
  `supabase.functions.invoke()`, get `SUPABASE_URL` and the anon and service
  role keys for their own environment, read secrets set in the console, reach
  the internet for `npm:` imports, and can take keyless webhooks when
  `verify_jwt` is off.
- **Uploads up to 50 MiB, or the limit you set.** File uploads to Storage are
  passed through the gateway and the TLS proxy as they arrive instead of being
  capped at 1 MiB. `SBARBASE_UPLOAD_LIMIT_MB` (50 by default, as on Supabase)
  sets the limit for the gateway, the proxy and Storage together; a change
  applies at the next start by recreating the shared Storage container. Other
  API bodies stay at 1 MiB.
- **Logs and metrics per environment.** Every member sees the last hour of
  requests through the gateway on the environment's page: totals, client and
  server errors, median and 95th percentile response times, requests per minute
  and per service, and the memory and processor use of its own services. Owners
  and admins also read the last requests and the Auth, REST, Storage and
  Realtime logs, with keys, tokens and passwords redacted before a line leaves
  the server. Counts live in memory and start again after a restart.
- **Realtime per environment.** Owners and admins turn Realtime on for an
  environment from the console; it runs the pinned upstream Realtime for that
  environment alone, with its own login, and stops when turned off. supabase-js
  broadcast, presence and database changes (every `public` table, filtered by
  row level security) work through the gateway, which checks the key before any
  socket reaches Realtime. The login holds administrator rights only while
  Realtime creates its own schema, first start and after an upgrade.
- **Sign-in settings and OAuth providers per environment.** Owners and admins
  set the site URL, allowed redirect addresses, sign-up and anonymous sign-in,
  and any of 19 providers (Google, GitHub, Apple and others) from the console.
  Saving recreates only that environment's Auth, keeping the previous one if the
  new one does not start. Email links and OAuth steps reach Auth without a key,
  as on Supabase. `SBARBASE_PUBLIC_URL` sets the address links and callbacks use.
- **Browser access.** A page on any domain can call Auth, REST and Storage
  through the gateway, as on Supabase: every answer carries
  `Access-Control-Allow-Origin: *`, a preflight gets 204 without a key, and no
  credentials mode is offered. The operator login stays same-origin.
- **Upgrades with an automatic way back.** `lab/upgrade.py start` checks the new
  version, pulls its images and backs up every environment, then moves the
  checkout; the restart replaces only the Auth, REST and Storage containers whose
  pin or configuration changed. If that start fails, the supervisor moves back to
  the previous version by itself. A version that changes the PostgreSQL image is
  refused. CI upgrades PostgREST v14.15 to v14.16, then recovers from a broken
  version, on a clean machine.
- **Fair share admission at the gateway.** Each environment is guaranteed 8
  requests in flight; a busy one borrows idle slots up to 24 of 32, while the
  unused share of every environment active in the last minute (at least 8 slots)
  stays free. Checked over loopback HTTP (`docs/evidence/fair-share-checks.json`),
  not against Supabase or under sustained load.
- **Saturation notice.** After 15 minutes in a row of refusals at its limit, or of
  crowding out a neighbour, an environment raises one `environment.saturated`
  operator notification. Borrowing alone never does.
- **Telegram** as a third notification channel beside email and webhook.
- **Invitations.** Owners and admins invite someone by email and role; the
  console shows a one-time link that lasts 7 days. The invitee chooses a password
  and joins, or signs in first if they already have an account. Unit tested with
  a fake management Auth, not yet against the live one.
- **Member roles in the console.** An owner changes a member's role or removes
  them; access ends at the next request. The last owner stays.
- **Activity.** Owners and admins see what happened in their organization,
  newest first, in the console (`GET /organizations/{id}/audit`). A project
  moved in from another client shows only what happened since it arrived.
- **A gateway share per environment.** The console's environment page shows each
  environment's guaranteed share. Installation operators raise or lower it and
  see the installation's allocation; a client organization sees only its own
  share. The gateway applies a change at the next request, and a raise that
  would exceed the gateway's 32 is refused.
- **Supabase Studio per environment.** Owners and admins start the original,
  pinned Studio and postgres-meta for one environment from the console, open it
  on its own address behind the console login, and stop it. Studio signs in with
  a per-start login that is not a superuser and exists only while it runs. CI
  uses tables, SQL, users and buckets through it on a clean machine.
- **Install with Docker.** `docker compose up -d --build` on any Linux host with
  Docker: the image carries Python 3.14, Bun and the Docker CLI, and starts the
  pinned Supabase services as sibling containers. CI runs build, first operator,
  first project through supabase-js, restart and a clean stop on a clean machine.
- **Empty-server rehearsal in a local VM.** `lab/vm-rehearsal.sh` boots a
  disposable Fedora 44 Cloud VM, installs from a clean clone with the one-command
  acceptance, creates a first project and reboots. Passed on 4 vCPU and 6 GiB
  (`docs/evidence/vm-empty-server-rehearsal.json`). Not a real server.
- **First project check.** `lab/first-project-check.ts`, and `--first-project` on
  `deploy/server-acceptance.sh`: login, project, environment, key, supabase-js
  Auth, REST and Storage through the gateway, and a refused revoked key.
- `SBARBASE_CONSOLE_PORT` pins the console's loopback port for a TLS proxy.
- CI for the unit suites, with a manual empty-host acceptance job. The Python
  suite also runs against an unreachable Docker endpoint, so a unit test that
  needs the daemon fails in review.
- **Import inspection (phase 0).** `lab/import_inspect.py` reads a Supabase
  source read-only and reports what an import would refuse, warn about or leave
  manual (`docs/evidence/import-inspect-checks.json`). The import itself does not
  exist yet.
- Management API: create an organization (owners and admins of the bootstrap
  organization), list an organization's members, retry a failed or cancelled
  environment; the console has matching screens.
- The control catalog records its schema version and refuses one written by a
  newer release.
- A recovery export records the owning organization, project and environment.
- Documentation restructured into explain, guides, reference and decisions, with
  hand-drawn diagrams, a quickstart, a server guide, `SECURITY.md`, a threat
  model, `CONTRIBUTING.md` and a roadmap.

- **Container generation migration.** `lab/migrate-generation.py` replaces a
  managed database container on the same data volume under an fsynced intent
  record, one checkpoint per phase and an explicit `--reconcile`. Five SIGKILL
  crash points pass on the disposable fixture
  (`docs/evidence/fresh-worker-generation-crash-all.json`). It refuses the
  retained placement: that run is a deliberate attended step and has not happened,
  so `lab/durable-check.ts` stays disabled and the two blocked load measurements
  stay blocked.

### Fixed

- Found by the milestone runs in the rehearsal VM on 2026-09-25: redeeming an
  invitation answered 500, because the account calls went to `/auth/v1/...` on
  the management Auth service itself, which serves them at its root; the first
  upgrade after an acceptance was refused as local changes, because the
  acceptance rewrites tracked evidence (it is now copied aside to
  `.lab/upgrades/`); the acceptance waited for the console right after
  installing the unit, before the images existed, so an empty host always
  timed out (the unit now starts after the installation); and an image pull
  gave up after one immediate retry (now four attempts, waiting 15, 45 and 90 s).
- A backup counted its rows before `pg_dump` ran, so a sign-up in between put a
  row in the dump that the manifest did not count, and the restore refused the
  backup. The counts and the dump now read one snapshot.
- The loopback listener announced `connection: close` after refusing a request
  but kept the connection open; a pooled client reused it and a one-second
  cleanup cut off the next long request. The sustained arrival run now passes.
- Found by the empty-server rehearsal: the acceptance checked the console before
  building it; block IO limits named a partition, which the kernel rejects, so the
  database never started on a usual VPS disk layout; body-less POST and DELETE
  requests got a body over the real listener, so key issuance and revocation
  answered 400; image pulls ran under a 600 second timeout with no progress; a
  failed first launch was reported as a retained source to adopt.
- An owner or admin of any organization could read every organization's
  notifications, including their ids and failure reasons. Each caller now sees
  only their own organizations' events.
- CI was red on a host whose root filesystem is a partition: a test still
  expected the partition where the runtime correctly names the whole disk.
- Five admission unit tests called Docker for real and failed without a daemon.
- The server acceptance recorded the unit as active in the same second it
  started it. After every start it now waits, at most 300 seconds, until the
  console answers over loopback, and fails with the last thing it saw.

### Changed

- The control catalog schema is now version 3: the delivery table accepts the
  Telegram channel. The migration is one way; an older release refuses a catalog
  this release has opened.
- The application gateway lends idle slots instead of refusing every request
  above 8 per environment; the overload probe keeps measuring the fixed share.
- The start requirement is derived from the placement a start runs (1792 MiB of
  limits on an empty server, plus 512 MiB per environment, plus the 2560 MiB
  reserve), and the preflight, the unit's `ExecStartPre` and the runtime use one
  computation. It was a fixed 5888 MiB in the preflight and 6 GiB in the runtime.
- Every GitHub Actions step is pinned to a full commit SHA, with the tag it was
  resolved from as a comment, so a moved tag cannot change what CI runs.
- CPU limits are admitted as ceilings: up to twice the cores after one core for
  the host. The old rule asked for 8 cores.
- A new environment is refused when the next restart could not admit it.
  On an installation that moved an environment, that check and the preflight
  now count the current recovery target's containers in one computation; the
  check used to count the source placement only, and a running target's memory
  is now counted as in use rather than twice. A restored target that was never
  cut over is counted by neither, because the next start does not run it.
- Project names are unique within an organization and environment names within
  a project; a clash answers 409, not 500. The API refuses an environment past
  the installation limit with 409 before queueing it.
- Moving a project cancels its queued jobs in the move and points its jobs at the
  destination, so later events name the new owners.

- The two load vehicles no longer overwrite their own evidence on failure or let
  cleanup replace the real error, and the SDK vehicle probes its fixture first.
- `NOTICE` no longer describes Supabase Studio as served; it is planned.

### Notes

- A `LICENSE` file (Apache-2.0) is present in the repository. The 0.1.0 entry
  below says there is none; that entry is kept as it was released.

## [0.1.0] - 2026-09-21

The first tagged milestone. It carries the platform's isolation and accounting
work, per environment mail, operator notifications, and the measurement evidence
that says what the isolation does and does not do.

### Added

- **Resource tiers and block IO separation.** Every container is launched under a
  policy tier with a CPU weight, a block IO weight and per device read and write
  bandwidth and IOPS limits. The device is resolved from the host rather than
  written down, and a launch refuses rather than starting a container with no
  block IO separation. `lab/resource_policy.py`, `docs/engineering/RESOURCE-POLICY.md`.
- **A counted placement derived from the daemon.** Admission reads the containers
  the daemon attributes to the owner label instead of a hand written name list, so
  a component nobody told the module about is still counted, and a labelled
  container outside the recorded placement is a refusal while it runs.
- **Continuous host pressure sampling and a level 1 response.**
  `lab/pressure_admission.py` takes a bounded series over a window, summarises it,
  and refuses new admissions while the most recent reading is at or over a
  threshold, appending every crossing to a durable ledger. Every decision carries
  what is not implemented next to the action.
- **Per environment mail.** One 0600 configuration file per environment, an Auth
  environment built from it, an explicit reconcile command that recreates one
  environment's Auth to apply or remove a configuration, and a non secret state
  file. `lab/mail_config.py`, `lab/mail_state.py`, `docs/engineering/ENVIRONMENT-EMAIL.md`.
- **Operator notifications.** A durable outbox and delivery tables whose rows
  commit inside the transaction that records the state change they describe, a
  drain inside the worker, an email channel and a signed webhook channel, a
  fail-closed credential redaction gate, and eleven event kinds wired at their
  durable state changes (installation started, stopped and failed start, worker
  restart and restart limit, fence applied and released, export completed and
  failed, restore verified and failed). `lab/notify.py`,
  `lab/notification_producers.py`, `docs/engineering/OPERATOR-NOTIFICATIONS.md`.
- **Console surfaces for both.** A read only route for an environment's mail state
  and one for the undelivered notification count, with the mail state on the
  environment surface and the count in the console shell.
- **Disposable probes with negative controls.** A mail probe that proves delivery,
  an unconfigured environment, a dead relay and a wrong credential, and a
  notification probe that proves delivery, deduplication, the redaction gate and
  that a broken channel records a failure instead of changing the operation it
  reports.
- **Documentation.** Three design records, an adversarial review that states the
  unfakeable acceptance criteria, an execution plan, a plan for the deferred
  container generation migration, and the measurement evidence under
  `docs/evidence/`.
- Per environment upstream Studio presented on the administration path, and the
  console following the operating system theme by default.

### Changed

- Placement arithmetic is derived rather than listed, and the block IO rows are
  the mechanism that binds, because the weight column was measured not to.
- The unwritten `environment_mail` catalog table was removed in favour of the
  runtime's own summary file, which is the single source the console reads.
- The mail configuration comparison runs in both directions, so a key the desired
  configuration no longer has cannot survive in a reused container.

### Fixed

- Mail: the unchanged path recorded a state the vocabulary does not define, so a
  second run against an unchanged configuration raised instead of reporting. A
  source level test now walks every record call site and requires a defined state.
- Mail: a configuration an operator deleted could survive a restart and keep a
  live SMTP credential, rate limits and relay cooldown in the reused container.
- Notifications: the escalation window was the severity window, so a broken channel
  would have escalated about 72 times per six hours instead of once, and two broken
  channels could escalate each other.
- Placement: an owned container outside the recorded placement refused installation
  startup where the earlier revision admitted, and a labelled container under any
  other owner escaped the count while the module's own documentation claimed it was
  counted.
- Placement: every recovery target creation site now carries its tier label, and
  the guard that claimed every launch site passes a tier is a floor rather than an
  exact count that a new launch site breaks.
- Notification probe: its default run now exercises the fail-closed redaction gate,
  which it did not, and its summary comparison no longer re-reads a render time
  sentence that made one check a coin flip.
- Mail: a dangling symlink is refused instead of silently reading as unconfigured.

### Measured, with the evidence committed

- `docs/evidence/resource-policy-cgroup-mapping.json`: `--cpu-shares` maps to the
  cgroup v2 `cpu.weight` sublinearly (2048 asks for 174, not 800), `--blkio-weight`
  did not bind at all on this host, and `--device-write-bps` did, landing a numeric
  `wbps` in `io.max`.
- `docs/evidence/noisy-neighbor-sql-runs.json`: five runs of the neighbour
  experiment, three preserved. No repeatable effect: the median, the p95 and the
  maximum stay flat between the baseline and the phase where a second environment
  saturates the shared engine. One earlier run showed a tail spike that four later
  runs did not reproduce, and it is recorded as an outlier rather than as the
  neighbour penalty.
- `docs/evidence/pressure-response-checks.json`: a real threshold crossing on a
  disposable container, the level 1 refusal, one durable ledger line, and a return
  to admitting after recovery.
- `docs/evidence/auth-templates-source-v2.196.0.json`: what a template variable of
  the pinned Auth version does, read from the tag's own source with a digest per
  file. A template value is an HTTP URL, never a file.
- Probes re-run by the maintainer on the landed tree: mail 36 checks, notifications
  21 checks, both cleaning up what they created. Gates: 575 Python tests, 87 Bun
  tests, UI typecheck, console build check.

### Not in this release

Stated here rather than left to be discovered:

- **No server rehearsal.** The deployment readiness matrix is implemented and
  proven on a workstation, and it is not a rehearsal against real hardware.
- **The container generation migration is deferred**, so a retained database
  container cannot be recreated. The tier contract therefore applies to placements
  created after it, and the retained placement on a development host stays
  grandfathered. `docs/engineering/CONTAINER-GENERATION-MIGRATION.md` is the plan.
- **Two measurements are blocked, not merely unrun**: the arrival driven pressure
  experiment and the mixed SDK load. Their only fixture generator,
  `lab/durable-check.ts`, is disabled pending that migration, and its fixture on a
  development host names a retired environment. One load vehicle also overwrites
  its own evidence when it fails, and its cleanup replaces the real error.
- **The tier tables are uncalibrated.** The block IO rows bind and separate the
  tiers, and no experiment has measured what a tier receives under load.
- **Three notification kinds are emitted by nobody**: pressure, runtime refused and
  headroom changed. Nothing calls the pressure response on a schedule.
- **Mail is read only in the console** and the operator still chooses an SMTP
  provider. Templates are the upstream defaults, and an override has to be served
  from inside the runtime network.
- **No licence file.** `NOTICE` states the copyright and the upstream attributions,
  including that Supabase Studio and postgres-meta are used as unmodified pinned
  images and that the console is sbarbase's own work. Choosing a licence is the
  maintainer's decision and is not made here.