# Console implementation and verification

Verified 2026-09-20. This is the historical verification record of the platform-layer console, not production release approval. The goal of bringing this console toward Studio parity is withdrawn: environment administration is served as the original upstream Studio ([integration specification](../STUDIO-INTEGRATION.md)), so the fidelity ledger below is history rather than a target. The captures are platform-layer captures and predate the redirect.

## Real workflow

The React/Vite console is served by the same loopback Bun server as the management
and project APIs. It uses the original Supabase SDK for management login, an
in-memory session with refresh, and authenticated management calls for all data.
It does not ship demo rows, fabricated metrics or privileged credentials.

Screens: login/error, organization selection, searchable projects and inline
creation, environment creation/provisioning state, connection details, one-time
publishable key display, revoke confirmation and read-only membership states.
Requests to obsolete selections are aborted. API membership checks remain the
authority even when a displayed role becomes stale.

Provisioned means the provisioning job completed; it is not a live health metric.
Queued/running environments poll their job status. The local worker still needs
to be started by the installation operator; the browser never invokes Docker.

## Browser evidence

All browser work used the owned headless Sbar Orbit session
`0c14af9a-b04e-4a14-9f90-139e6f5d48b5`. The person's desktop/browser was untouched.
The session was closed afterward. Browser/IAB was not used because workstation
instructions require Orbit. Screenshots were captured via Orbit and inspected
with `view_image`, including the selected concept and final desktop capture.

Observed against original live Auth and the durable catalog/runtime:

- Login displayed the user's organization and actual project rows.
- New project `Community` persisted, and search filtered its row.
- Its new `production` environment appeared as Queued, then Provisioned after
  the installation worker completed. No simulated completion timer was used.
- Connection discovery displayed Auth, REST and Storage.
- Key issuance showed a value once. Dismissal removed that value. Revoke required
  explicit confirmation and the row subsequently displayed Revoked.
- Sign out returned to login. Reload required login because sessions are not
  retained in browser storage.
- After the fixture's membership changed to viewer and it logged in again,
  creation/key mutation actions were absent; connection discovery still worked.
- Invalid sign-in displayed a generic error and cleared the password field.
- Desktop 1586 x 992 and mobile 390 x 844 were visually inspected. Connection
  controls stack on mobile and tables scroll inside their own frame.

The fixture management user and its private credential file were removed. Its
project/environment remain as owned lab test data, bringing the durable runtime
to three environments. The created key was revoked. Runtime and server are stopped.

No raw issued key appears in the saved screenshots. Clipboard contents, a complete
keyboard/screen-reader audit and a long-duration token refresh were not tested.

## Fidelity ledger

| Point | Concept evidence | Final implementation |
|---|---|---|
| Layout | Left rail and single project table | Rail widened from 240px to 268px after comparison; main padding 42px |
| Type | Strong Projects heading, subdued description | Heading increased to 34px; description 18px; deliberate 14px controls |
| Palette | Dark neutral surfaces with green action | #171717/#1c1c1c/#202020 and #3ecf8e, no marketing gradients |
| Table | Bordered frame, outline folders, short monospace IDs | 92px rows, 14px IDs and Lucide outline folder/chevron icons |
| Copy | Projects, New project, search, column names, count | Same functional copy; actual organization, names, row order and IDs differ |
| Responsive | Desktop concept only | Same hierarchy stacked at 390px; no clipped primary controls observed |
| Assets | Text wordmark and UI controls | Native text/SVG/CSS; concept raster is documentation only |

Above-the-fold copy review: no added metrics, claims or unavailable navigation.
Intentional deviations are real data, its API ordering, native system font fallback,
flat fills instead of generated-image lighting, and responsive/form/detail states.
The platform console has its own layout and visual system; parity with Studio is
intentionally out of scope, because the environment surface is Studio itself. See [specification](CONSOLE.md).

Saved captures: [desktop](console-desktop.jpg), [mobile](console-mobile.jpg),
[mobile connection](console-mobile-connection.jpg), [login error](console-login-error.jpg).
These are platform-layer captures taken before the administration surface moved to upstream
Studio. Concept: [generated reference](console-concept.png). The reference PNG was losslessly
reduced from 949605 to 836062 bytes with decoded RGBA equality verified. The original
is retained in the Codex generated-images directory. Orbit's JPEG captures are
retained without another lossy encoding pass; no local JPEG lossless optimizer was available.

## Engineering checks and limits

`bun run typecheck:ui`, `bun run build:ui`, 35 Bun tests (201 assertions) and six
Python tests passed. The TypeScript check currently covers the UI and Vite config,
not the whole backend. Vite reports Lucide's React Server Component directives;
this is a client-only bundle, so those directives are not used. No runtime secret
values were found in the built JS by a private programmatic comparison.

Static serving exposes only the index and flat generated JS/CSS names. CSP allows
same-origin scripts/connections, rejects framing and restricts forms/base URLs.
Four live checks accepted the console and rejected private source/secret routes
and an untrusted Host header. The first boundary-check attempt found an already
terminated server; it was repeated against a newly started owned child and passed.

Remaining: installer/supervisor, broader admin features, MFA/invitations/recovery,
production edge protections, accessibility audit, browser E2E automation in CI,
localization and real capacity/health/backup operations. Do not expose this local
console publicly or claim that the overall platform is production-ready.

## Capacity status follow-up

The environment row now shows `Capacity limit` for a failed operation carrying `capacity_exceeded`, with an owner-capacity-review explanation. Other failures retain the generic message. Frontend typecheck and build pass. Backend status scope, claim protection and retry clearing are tested, and the live worker refusal probe verifies the stored code. Existing screenshots predate this text change; no new browser visual inspection is claimed for this follow-up.
