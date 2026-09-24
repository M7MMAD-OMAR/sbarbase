# Diagrams

## Current diagrams

These SVG files are hand-authored and meant to be edited: open one in a text editor, change the label or shape, and keep the claim in its `<title>` and `<desc>` true. Each file is self-contained (its own filters and arrow markers, ids prefixed per file, a paper background so it reads on a dark page, no external fonts or scripts). Coral marks a shared component, and therefore a shared failure boundary; teal marks a service that exists once per environment; a dashed lilac outline marks something planned and not built.

| File | Claim | Used in |
|---|---|---|
| [full-stack-vs-shared.svg](full-stack-vs-shared.svg) | A full stack per project repeats every component; Sbarbase shares the gateway, the PostgreSQL engine and Storage, and runs Auth and REST per environment. | [why](../explain/why.md), [architecture](../explain/architecture.md) |
| [request-path.svg](request-path.svg) | The gateway checks the key for the environment named in the path and refuses with 429 or 503 instead of queueing; each environment reaches only its own database. | [architecture](../explain/architecture.md) |
| [ownership-vs-placement.svg](ownership-vs-placement.svg) | Ownership (client, project, environment) is recorded apart from placement, so a move keeps the owner; a second server is planned. | [hierarchy](../explain/hierarchy.md) |
| [restore-flow.svg](restore-flow.svg) | Stop writes, encrypted export, restore and verify on an independent engine, then switch; a failed verification leaves the source fenced and untouched; exporting stops shared Storage. | [recovery](../explain/recovery.md) |
| [trust-boundaries.svg](trust-boundaries.svg) | Untrusted visitors enter only through the TLS proxy and the gateway; operators are trusted; the engine and Storage are shared. | [isolation and trust](../explain/isolation-and-trust.md) |
| [docs-map.svg](docs-map.svg) | The four doc sections and the engineering notebook, one purpose each. | [docs README](../README.md) |

To check one after editing, render it with a headless browser (for example `chrome-headless-shell --screenshot --window-size=<viewBox width>,<height>`) and look at the result; keep font sizes between 12 and 15 and leave slack in every box, because viewers without the first fonts in the stack get wider fallbacks.

