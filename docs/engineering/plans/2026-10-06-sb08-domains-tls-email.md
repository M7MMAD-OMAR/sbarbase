# SB-08: domains, TLS and email

Status: implementation in an isolated checkout, no runtime or public acceptance.

## Identity and authority

Imported baseline commit: `5f2ba22`. The declared 537 public source files have
identity `2fc47eca54cf0798d5ba2f5ba52bfdee18c89a27d4767925a72c2acf8af37d18`.
The six coordination documents are additional context. All 543 declared files,
6912347 bytes and file modes were verified against manifest SHA256
`bc15781ad666020553db5b9be96716f7b60392e5d070dd8cc38cbad3f1f68531`.
The import transfers no native or production acceptance. Deliver only commits
after the baseline to the coordinating task.

Original service reference: Supabase `self-hosted/v0.8.2`, commit
`564eab8ad7840b13324f68b1bfac074ef8d51c21`. Original images, Auth TLS verification,
ETags and neighboring environment settings remain required.

## Contract

| ID | Outcome | Required evidence |
| --- | --- | --- |
| D1 | Exact DNS A and AAAA instructions, public origin and provider access loss guidance | Strict configuration and dual-stack observations |
| D2 | HTTPS certificate hostname, lifetime, private storage and renewal configuration | TLS chain checks, wrong-host/expired refusal, renewal and rollback |
| D3 | Per-environment Auth site, redirects, OAuth callback and SMTP transport | Original Auth confirmation/reset, OAuth and management recovery email |
| D4 | Current management MFA, actor epoch, installation role and environment ownership at every effect | Delayed body, revocation, replacement and neighbor tests |
| D5 | Guided, accessible setup with honest pending/failed states | Actual keyboard, browser, Studio and WebSocket checks |
| D6 | Linux Docker/Compose runtime with durable certificate state | Frozen image, daemon restart, reboot and restoration evidence |
| D7 | Public production validation separate from local evaluation | Owned public DNS/TLS pilot and independently reviewed observations |

Initial exclusive source: `lab/domain_transport.py`,
`lab/test_domain_transport.py`, `src/control/deployment-setup.ts`,
`tests/deployment-setup.test.ts`, `ui/DeploymentSetup.tsx`, this plan and dedicated
review/evidence records. Existing files require coordinated ownership before
changes. Graph closure has identified the handler/application callers and
installation-operator callers. Catalog, worker, installer, mail, gateway and
Compose integration will be reviewed as explicit deltas.

The setup intent contains opaque private references, never passwords, keys,
provider tokens or SMTP authentication material. Network observations cannot
be supplied by a browser as acceptance evidence. TLS and SMTP transport probes
do not prove mail delivery, OAuth login, WebSocket or public trust in local CAs.
No automatic SMTP sending is authorized by the initial source role.

Certificate automation uses an explicitly pinned Caddy sidecar with persistent
private data. This additional proxy leaves the original service images intact.
Managed certificates use ACME HTTP/TLS challenges, external certificates require
an operator-managed renewal route. CDN challenge handling is not assumed. Lost
DNS-provider access blocks a new public origin and identifies private recovery.
Rendering configuration is preparation, activation and renewal require actual
service evidence.

## Initial checks and resource boundary

Freeze source hashes and argv before each source check. Each check is at most
120 seconds, aggregate test execution at most 180 seconds, output at most
16 MiB. Owned source fixtures use at most 512 MiB, 0.5 CPU, 128 pids and no swap.
Only owned temporary files, subprocesses and loopback sockets are allowed.
An enforceable process profile must be recorded before checks run.

Planned checks are Python unittest for `lab/test_domain_transport.py`, Bun tests
for `tests/deployment-setup.test.ts`, control/UI typechecking and a component
render check. The checkpoint plan records exact executable argv once the bounded
runtime is assigned. No Docker, original service, VM, provider mutation, public
issuance, external email or retained-resource change belongs to this role.

After final source-bound checks, two fresh independent reviewers inspect strict
input/path/certificate handling, actual observations, authority and operations.
Reviewer reports are bound to current checkpoint plan and source hashes. Source
review does not accept original service or public runtime.

## Pending actual acceptance

SB-02 host admission and SB-06 protection need current integrated acceptance.
The coordinator must assign an owned native slot, immutable image/source,
resource/endpoint/cleanup contract, public domain/provider access and SMTP test
ownership. Then execute original browser/Auth/Studio/WebSocket flows, confirmation
and reset plus management email, failed DNS/provider recovery, certificate
renewal/reboot/rollback and neighboring configuration preservation. Retain every
failed observation. The full SB-08 outcome remains open until all these gates
have direct, current and independent evidence.

## Runnable preparation and integration boundary

The CLI reads bounded JSON on stdin and has `preview`, `proxy` and `compose`
actions. On an admitted existing Docker installation it runs inside the control
container, using its public Python runtime, for example:

```sh
docker compose exec -T sbarbase python3 lab/domain_transport.py preview --runtime e_aaaaaaaaaaaaaaaaaaaaaaaa < private-setup.json
docker compose exec -T sbarbase python3 lab/domain_transport.py proxy --runtime e_aaaaaaaaaaaaaaaaaaaaaaaa < private-setup.json
docker compose exec -T sbarbase python3 lab/domain_transport.py compose --runtime e_aaaaaaaaaaaaaaaaaaaaaaaa < private-setup.json
```

The runtime ID above is a fixture placeholder. The operator must select an owned,
ready environment. These actions only render output. They do not create the
sidecar, reconcile Auth, send mail or label a preparation applied. The current
control API has separate preview and revision-CAS preparation actions. The exact
handler/App integration patch was admitted and applied in this isolated checkout.
The dispatcher and wizard are installed in source; this is preparation only.

The additional official Caddy `2.11.7-alpine` Linux amd64 image is pinned to
`library/caddy@sha256:173b26306d711395accaeba8b67afcaad2a085ccb1e6bf26010bfe8c095a5229`.
Its registry index digest is
`sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f`.
The official Docker source is commit
`9234f5785658cae8129bfae366e196d0670468ac`. Metadata was read and hashed; the image
has not been pulled, executed or accepted. Other architectures are not admitted.

The generated sidecar uses the host network, 128 MiB memory/no swap, 0.25 CPU,
64 pids, a read-only root filesystem and private persistent `/data`. Its API is
disabled. Local evaluation binds only declared loopback addresses and disables
automatic HTTP redirects. Public HTTP/TLS challenges require a separately
admitted endpoint. Certificate renewal belongs to Caddy's managed certificate
mechanism; external certificates require validated replacement and sidecar
recreation. These mechanisms still need actual lifecycle observations.

Installation inventory and recovery must include the private setup SQLite store,
operation audit, Caddyfile/override, ACME account and certificate data, owned
local CA if applicable, and external certificate references. The shared TLS
sidecar and data are excluded from selected-environment purge. Per-environment
Auth/SMTP references retain their own ownership. This inventory proposal was
sent to the recovery and retention owners and the coordinator for registry
integration. Actual admission remains pending their agreed contracts.

## Source observation boundary

The initial source checks used a unique transient user service per check.
Observed limits: 536870912 memory bytes, zero swap, CPU quota 0.5 and 128 pids.
Each socket connects to `127.0.0.1` and the ephemeral port returned by its own
prebound fixture. The tests use no external resolver, recipient or issuer.
Those initial checks used the host network and filesystem. Later checks verify
a private user/network/mount namespace, only its loopback interface, and read-only
mounts for declared source and public dependencies. Fixtures use owned temporary
directories. The first namespace attempt executed all 34 Python cases successfully
but its boundary log mixed with terminal JSON, causing an evidence parser failure.
That failure is retained and the boundary log is separated for final checks. The final child environments contain
only the explicit public executable path and test settings. Earlier attempts
inherited the user-service environment, without using or logging credentials.

Initial negatives remain retained: Python 30/32 due to missing test-certificate
Authority Key Identifier (OpenSSL verification code 85), Bun 8/18 due to an
incorrect provisioning-claim order assumption in the fixture, and a compiler
literal inference error in that fixture. Later implicit/STARTTLS tests reproduced
a real probe bug: Python SMTP.connect overwrote the declared hostname with the
pinned IP address, producing verification code 64. Pinning the socket separately
preserves hostname verification for both SMTP TLS modes. No verification flag
was disabled. Final source checks and fresh independent reviews bind the final
source; prior passing observations are historical after later source changes.

## Documentation context

Reviewed 2026-10-06. Supabase's changelog index was fetched; current database
upgrade changes are outside this slice and no service pin is updated here.
[Supabase Docker configuration](https://supabase.com/docs/guides/self-hosting/docker)
documents the public API and Auth site URL roles.
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
documents persistent certificate storage and public challenge prerequisites.
[Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
documents transport and forwarding behavior. These are configuration references,
not observations of this product's execution.

The offline Chromium source fixture uses the installed browser and Playwright,
with all requests blocked. It checks wizard editing, busy and error states,
revision submission, keyboard navigation and environment switching. The existing
Sign-in form is the visual style reference under the same operator fixture,
English language and 1024 by 900 viewport. This comparison concerns form style
and usability, not identical content or original-service browser acceptance.

The assembled application deferred-body fixture initially timed out when Bun
cloned the streamed Request. Passing the owned request itself exercises actual
MFA revocation and completes. Browser startup first failed before any interaction
because about:blank has no HTTP origin and the baseline form initializes its
Supabase client from location.origin. The final fixture fulfills its synthetic
.invalid document entirely in Playwright, with all other requests denied and
no socket/server. Initial browser parser/startup negatives remain retained; their
execution time is charged. Captures exposed a checkbox missing the existing
check class; the wizard now reuses the console card, checkbox and primary button
classes. Final observations still certify only the bounded source fixture.

The first independent source reviews found empty URL component acceptance,
sequential list-editing delimiter loss, and a local proxy/upstream port collision.
Bounded regressions reproduced all three before repair: the Python suite ran
36 methods with 18 failing subcases, and the real browser typed a delimiter that
was lost. The validator now rejects syntactically present userinfo/query/fragment
components, the renderer refuses listener/upstream port collisions, and text
drafts preserve IPv4/IPv6 separators and redirect newlines while normalized
arrays are prepared for submission. Fresh source checks and independent review
are required after these changes. Earlier reports are retained unchanged.

A second fresh specification review found a local literal-origin/listener-address
mismatch. Its failed report was imported before repair. A separately admitted
source-only role freezes that report and a 60-second aggregate, 45 seconds per
check, 4 MiB output profile, with unchanged resource and namespace boundaries.
The original role remains charged at 167.243909 seconds of its 180-second ceiling.
Budgets and their combined total are recorded separately. New regressions
reproduced four mismatched loopback preparations and an API save that wrongly
succeeded. Literal origins now require their address in the normalized bind
list. localhost accepts only canonical 127.0.0.1 and/or ::1 listeners. Aligned
IPv4, IPv6 and dual-stack cases remain supported. Current-source tests and two
new independent reviewers are required for this repair; prior gates are stale.
