# Sbarbase website

The public product and architecture site at https://base.sbarah.com. Arabic is served at `/`, English at `/en/`. This static marketing site is separate from the local administration console and does not run the database platform on Cloudflare.

## Run

```sh
cd website
bun install --frozen-lockfile
bun run build
bun run dev
```

## Deploy

```sh
bun run build
bun run wrangler deploy --dry-run
bun run deploy
```

The `sbarbase-site` Worker serves only `dist/`. Its custom domain is declared in `wrangler.jsonc`. Use the existing Wrangler login. No deployment credentials are stored in this repository.

## Content and interactions

The design is paper cutout: torn sheets with tape on a scribbled wall by day and a starry navy wall by night, hand lettering, and a paper cactus (sabbar) as the mascot. It follows the drawings in `docs/diagrams/`, so the site and the docs look like one thing.

- `src/content.ts`: Arabic and English copy, grounded in `docs/reference/status.md`, `docs/explain/`, the roadmap and `docs/evidence/vm-empty-server-rehearsal.json`. The admission figures the environment slider uses are in `rules`, and a test checks them against `lab/resource_policy.py`.
- `src/render.ts`: prerendered pages and every drawing as inline SVG. The request figure is written left to right and mirrored for Arabic, so the flow reads in the reader's direction.
- `src/app.js`: day and night toggle (remembered per reader), the today versus Sbarbase comparison with an environment slider, the 12 second request walkthrough (play, pause, seek, steps), clients, projects and environments with an environment moving between servers, what is shared versus per environment, the recovery steps, and copying the install command.
- `src/style.css`: the palette (paper, mustard, coral for shared parts, teal for per environment parts, lilac, navy), the torn edge as an SVG filter on a static layer, and all motion behind `prefers-reduced-motion: no-preference`.
- `public/assets`: self-hosted fonts and their licences. `hand.woff2` is Marhey (SIL OFL 1.1, `OFL-Marhey.txt`), subset to Arabic and Latin; body text keeps Noto Sans Arabic and Manrope.

Everything is readable without JavaScript. The walkthrough is an illustration, not live traffic; it pauses offscreen and in hidden tabs. Planned work (Studio per environment, moving between servers, scheduled backups) is labelled as planned wherever it appears, and the page says plainly that the install was rehearsed in a virtual machine, not on a real server.

## GitHub and Cloudflare

Source: https://github.com/M7MMAD-OMAR/sbarbase. GitHub Actions checks and packages the site. Deployments currently use authenticated Wrangler. Native Cloudflare Builds connection requires Workers Builds Configuration access, which the current local OAuth session does not grant. Do not claim push-to-deploy is enabled until that connection is verified.

Cloudflare references:
- https://developers.cloudflare.com/workers/static-assets/
- https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/
