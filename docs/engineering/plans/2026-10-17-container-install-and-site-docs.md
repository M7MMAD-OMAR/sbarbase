# Container install first, and the docs on the website

Date: 2026-10-17. Status: proposal; nothing here is implemented or accepted yet.

The owner asked for two things on 2026-10-17: installing Sbarbase must be easy, preferably with Docker or Podman, and the documentation must live on the website, not only on GitHub. This plan says what already exists, what is missing, and the order to close the gap.

## What exists today

- **A Docker route already exists.** `Dockerfile`, `compose.yaml` and `deploy/compose.sh` run the control plane in a container that drives the pinned Supabase services as sibling containers. The host needs Docker Engine 25 or newer and Compose 2.20 or newer, not Python, Bun or systemd. The steps are in [Install with Docker](../../guides/docker.md): clone, `deploy/compose.sh check`, `deploy/compose.sh up`, `deploy/compose.sh bootstrap`.
- **It is checked on a clean machine, not on a real server.** The `docker-install` job in `.github/workflows/ci.yml` builds, starts, creates the first operator and project, restarts and stops on a clean GitHub runner with Ubuntu 26.04; [docker-install-checks.json](../../evidence/docker-install-checks.json) records 15 checks. That runner already has Docker installed, so the package install step has never been run by any check.
- **The website shows the older path.** The install section of the site (`website/src/content.ts`, pinned by `website/tests/site.test.ts`) lists the five systemd commands from the [quickstart](../../guides/quickstart.md), which need Bun, the host's Python and a service account. A visitor never sees the shorter Docker route.
- **The Docker route is narrower than the site's chips.** The `local-v1` profile refuses SELinux, rootless Docker, Docker Desktop, ARM and every engine other than Docker ([host admission](../HOST-PREFLIGHT.md)). Fedora's `moby-engine` normally runs with SELinux enabled, so the Docker route is expected to be refused on Fedora 44 today. That has to be confirmed with `docker info` on a Fedora host before the site says anything about Fedora and Docker.
- **The docs live only on GitHub.** The site links to GitHub pages through the `doc()` helper in `website/src/content.ts`. `.github/workflows/website.yml` runs only when `website/**` changes, and deployment is a manual `bun run deploy` with the owner's Wrangler login; push to deploy is not connected.

## Part A: Docker as the front door

A1. **Rehearse the Docker route from a bare Ubuntu 26.04 machine, package install included.** On a disposable VM: install Docker and the Compose plugin from the distribution (expected `docker.io` and `docker-compose-v2`; the rehearsal confirms the names and that Compose reports 2.20 or newer), enable the service, clone to `/opt/sbarbase`, then `check`, `up`, `bootstrap`, a reboot, and a supabase-js call against a first environment. `lab/vm-rehearsal.sh` takes a Fedora image today; it needs an Ubuntu image and a Docker route, or a sibling script. Write the result to `docs/evidence/` with its own scope.

A2. **Add the package step to the guide.** [Install with Docker](../../guides/docker.md) assumes Docker is already installed. Add the exact commands A1 ran, in English and Arabic in the same commit.

A3. **Switch the website's install section to the Docker route.** Four or five commands, the server chips narrowed to what A1 proved (Ubuntu 26.04, x86-64, 4 cores, 8 GB), and one line under them: "Without Docker: the quickstart" for the systemd path. Update `site.test.ts` to pin the new commands against `docker.md` instead of `quickstart.md`. Keep the current wording: checked on a clean machine, not yet on a real server.

A4. **Decide Fedora.** Either Fedora keeps the systemd route only, and the site says so, or SELinux gets its own evidence: bind mount labels for the checkout, socket access from the controller, and the Supabase containers under an enforcing policy. The second is real work and overlaps Podman (Part B).

A5. **One command, later.** A short installer served from the site (`curl -fsSL https://base.sbarah.com/install.sh | sh`) that does exactly the A1 steps, prints each one before running it, stops on any refusal, and leaves `bootstrap` interactive so the password never passes through the script. It waits until A1 to A3 are done, so it automates a proven path rather than an unproven one.

A6. **A prebuilt image, later.** `compose.yaml` builds the image on the server. Publishing it would save the build, but the update channel's "needs a rebuild" class assumes a local build, and a published image needs signing and a digest pin in `compose.yaml`. Record the questions; do not start before A3.

## Part B: Podman, as a separate gated scope

Podman is not supported now, and the site must not suggest it is. The `local-v1` contract is Docker only on purpose; the [portability plan](2026-10-03-product-and-portability-plan.md) already lists other engines as a separate evaluated scope. A Podman profile would have to prove, with its own evidence:

- rootful `podman.socket` answering the Docker API well enough for the Docker CLI inside the controller and for Compose (`docker compose` pointed at the Podman socket, or `podman compose`);
- the controller's own identity through `cgroup: host`, and the project and service labels it inspects before recovery;
- the data root bind mount (`/var/lib/containers/storage` instead of `/var/lib/docker`) and block IO device discovery for the resource limits;
- `network_mode: host`, the cgroup v2 controllers the limits need, and the restart policy after a reboot (Podman restarts containers through `podman-restart.service`, not the daemon);
- SELinux on Fedora and RHEL, which is the same open question as A4.

The outcome would be a second named profile with its own branch in `deploy/host-preflight.sh` and its own CI job. Until then the refusal should name Docker as the supported engine. Rootless Podman is later still, for the same reasons rootless Docker is.

## Part C: the docs on the website

C1. **Scope.** Render `docs/guides`, `docs/explain` and `docs/reference`, the set `lab/test_docs_bilingual.py` already keeps bilingual, plus the docs map. Arabic at `/docs/...` and English at `/en/docs/...`, matching the site's languages. `docs/engineering`, `docs/evidence` and `docs/decisions` stay on GitHub, linked from the site.

C2. **One source, built in.** `website/build.ts` reads `../docs` at build time and renders Markdown with one pinned renderer; no copies of the docs inside `website/`. Relative links are rewritten: `X.md` to its English page, `X.ar.md` to its Arabic page, and anything outside the rendered set to its GitHub page. Heading anchors keep GitHub's slugs, so existing links such as `server-deployment.md#private-directory-permissions` still land. The diagrams in `docs/diagrams/` (`X.svg`, `X.ar.svg`) are copied next to the pages.

C3. **Page shape.** The site's header and themes, a side list built from the docs map, a switch to the sibling language, an "Edit on GitHub" link, right to left for Arabic, and a copy button on code blocks (reuse `website/src/app.js`). Readable without JavaScript, like the rest of the site.

C4. **Search, later.** A static index built with the pages, only after C1 to C3 work.

C5. **Links.** The site's Docs, Quickstart and Choosing a server buttons point at the on-site pages instead of GitHub.

C6. **Checks.** `website.yml` also runs on `docs/**`. The website tests fail when a page in the bilingual set does not render in both languages or when a rendered internal link has no target. The existing no long dash rule already covers the source.

C7. **Deployment stays manual for now.** A docs change reaches the site only after `bun run deploy` with the owner's Wrangler login. Connecting Workers Builds needs a permission the current login does not grant; do not describe the docs as updating on push until that is verified.

## Order

1. A1, then A2 and A3 together. This is the change a visitor notices first.
2. C1 to C3 and C5, then C6. These do not depend on Part A and can run while A1 waits for a VM.
3. A4, then Part B if there is demand; A5 and A6 after A3; C4 last.

Each step lands with its own commit, its own evidence where it runs anything live, and both languages for every reader-facing page it touches.
