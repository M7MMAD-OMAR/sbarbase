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

- `src/content.ts`: Arabic and English content grounded in `docs/reference/status.md`, `docs/explain/` and `docs/decisions/README.md`.
- `src/render.ts`: prerendered pages, accessible controls and architecture diagrams.
- `src/app.js`: 12-second request walkthrough, pause/replay/seek, hierarchy selection, service paths, architecture comparison and recovery stages.
- `src/style.css`: charcoal surfaces, white identity and blue diagram accents. No green backgrounds or imagery.
- `public/assets`: self-hosted fonts, font licenses and brand assets.

The walkthrough is illustrative, not live traffic. Autoplay pauses offscreen and in hidden tabs. Reduced-motion preference disables automatic playback and CSS motion. `?t=4` freezes a deterministic walkthrough frame for visual review. Links and all core explanations render without JavaScript.

The environment databases shown share a PostgreSQL engine. Separate databases do not establish hostile-operator isolation. Four recorded local environments are evidence scope, not a capacity guarantee. Planned services and production work are labeled separately.

Per-environment administration is planned as the original upstream Supabase Studio, one process per environment, with the sbarbase console kept as the platform layer above it. Studio is not implemented, so the site presents it under planned work only.

## GitHub and Cloudflare

Source: https://github.com/M7MMAD-OMAR/sbarbase. GitHub Actions checks and packages the site. Deployments currently use authenticated Wrangler. Native Cloudflare Builds connection requires Workers Builds Configuration access, which the current local OAuth session does not grant. Do not claim push-to-deploy is enabled until that connection is verified.

Cloudflare references:
- https://developers.cloudflare.com/workers/static-assets/
- https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/
