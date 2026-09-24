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

A clean technical page in four parts: a hero (headline, one sentence, a single "open source, in development" badge, Install and Docs buttons, and the explainer video), the hierarchy, how a request travels, and the install steps. Off-white by day and deep slate by night, with coral as the one accent. Only the two diagrams keep the hand-drawn wobble and the palette from `docs/diagrams/` (coral for shared parts, teal for per environment parts, mustard for databases), on a light paper card in both themes.

- `src/content.ts`: the short Arabic and English copy, the links, and the five install commands exactly as `docs/guides/quickstart.md` runs them.
- `src/render.ts`: the prerendered pages and both diagrams as inline SVG. The hierarchy figure and the request figure are written left to right and mirrored for Arabic, so they read in the reader's direction.
- `src/app.js`: day and night toggle (remembered per reader), the 12 second request walkthrough (play, pause, seek, five steps), and a copy button per install step.
- `src/style.css`: tokens for both themes, the diagram ink and card colours, and all motion behind `prefers-reduced-motion: no-preference`.
- `public/assets`: self-hosted fonts and their licences. The UI uses Noto Sans Arabic and Manrope; `hand.woff2` is Marhey (SIL OFL 1.1, `OFL-Marhey.txt`), used only for text inside the diagrams.
- `public/media`: the narrated explainer film, one file per language (`explainer.ar.mp4`, `explainer.en.mp4`, 59 s, 1920x1080), its poster and captions timed to the voice (`explainer.ar.vtt`, `explainer.en.vtt`, off by default). The film is drawn in code in `film/` at the repository root: `bun render.mjs sbarbase-explainer.html` renders the picture and `bun build-voice.ts` lays the recorded narration (`film/voice/`, texts in `film/narration.json`) on it and writes these files.

Everything is readable without JavaScript. The walkthrough is an illustration, not live traffic; it pauses offscreen and in hidden tabs.

## GitHub and Cloudflare

Source: https://github.com/M7MMAD-OMAR/sbarbase. GitHub Actions checks and packages the site. Deployments currently use authenticated Wrangler. Native Cloudflare Builds connection requires Workers Builds Configuration access, which the current local OAuth session does not grant. Do not claim push-to-deploy is enabled until that connection is verified.

Cloudflare references:
- https://developers.cloudflare.com/workers/static-assets/
- https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/
