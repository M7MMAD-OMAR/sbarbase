# Sbarbase website

The public product and architecture site at https://base.sbarah.com, and the documentation. Arabic is served at `/` and `/docs/`, English at `/en/` and `/en/docs/`. This static marketing site is separate from the local administration console and does not run the database platform on Cloudflare.

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

- `src/content.ts`: the short Arabic and English copy, the links (the Docs, Docker guide, Without Docker and Choosing a server buttons open the on-site docs), and the five install commands exactly as `docs/guides/docker.md` gives them and `lab/vm-docker-rehearsal.sh` ran them.
- `src/layout.ts`: the head, top bar and footer shared by the home page and the docs pages.
- `src/render.ts`: the prerendered pages and both diagrams as inline SVG. The hierarchy figure and the request figure are written left to right and mirrored for Arabic, so they read in the reader's direction.
- `src/app.js`: day and night toggle (remembered per reader), the 12 second request walkthrough (play, pause, seek, five steps), and a copy button per install step.
- `src/style.css`: tokens for both themes, the diagram ink and card colours, and all motion behind `prefers-reduced-motion: no-preference`.
- `public/assets`: self-hosted fonts and their licences. The UI uses Noto Sans Arabic and Manrope; `hand.woff2` is Marhey (SIL OFL 1.1, `OFL-Marhey.txt`), used only for text inside the diagrams.
- `public/media`: the narrated explainer film, one file per language (`explainer.ar.mp4`, `explainer.en.mp4`, about a minute, 1920x1080), its poster and captions timed to the voice (`explainer.ar.vtt`, `explainer.en.vtt`, off by default). The film is drawn in code in `film/` at the repository root: `bun build-voice.ts timing` times the scenes to the voice, `bun render.mjs sbarbase-explainer.html` renders the picture and `bun build-voice.ts` lays the recorded narration (`film/voice/`, texts in `film/narration.json`) on it and writes these files.

## Docs

`src/docs.ts` renders the docs at build time from `../docs` with one pinned renderer (`marked`, headings slugged with `github-slugger` so GitHub anchors keep working); no copy of the docs lives in `website/`. The rendered set is `docs/README.md` and every page in `docs/guides`, `docs/explain` and `docs/reference`, the pages `lab/test_docs_bilingual.py` keeps in both languages: `X.ar.md` at `/docs/...`, `X.md` at `/en/docs/...`, one folder per page. Links into the set stay on the site, the diagrams in `docs/diagrams/` are copied to `/docs/diagrams/`, and every other link (engineering notes, evidence, decisions, source) opens its GitHub page. The side list is read from the docs map of each language. Each page has the site's header and themes, a switch to the same page in the other language, an Edit on GitHub link and a copy button on code blocks. Mermaid blocks show their source in a collapsed box, since GitHub draws them and this site does not. The build also writes `sitemap.xml` with every page and its alternate.

`tests/docs.test.ts` fails when a page of the bilingual set does not render in both languages, when a rendered internal link or its anchor has no target, or when a page carries a long dash or an external resource. The Website workflow runs on `docs/**` too. A docs change reaches the live site only after `bun run deploy`.

Everything is readable without JavaScript. The walkthrough is an illustration, not live traffic; it pauses offscreen and in hidden tabs.

## Image delivery

The video poster is a 1920 by 1080 WebP. The social card remains a 1200 by 630 JPEG. Original source JPEGs are preserved under `docs/design/masters/website-*.jpg`; regenerating outputs always uses those sources, so repeated compression does not accumulate. With the public Pillow 12.3.0 dependency installed, run `python3 website/optimize_images.py` from the repository root, then `bun run --cwd website build` and `bun website/image-loading-browser.ts`. Python is an optional authoring tool, not a website runtime or build dependency. Measurements and compatibility limits are in [WEBSITE-IMAGE-DELIVERY.md](../docs/design/WEBSITE-IMAGE-DELIVERY.md).

Install the tracked browser test dependencies and Chromium before the first browser check, from the repository root:

```sh
bun install --cwd lab/mfa-browser --frozen-lockfile
bun run --cwd lab/mfa-browser playwright install chromium
```

## GitHub and Cloudflare

Source: https://github.com/M7MMAD-OMAR/sbarbase. GitHub Actions checks and packages the site. Deployments currently use authenticated Wrangler. Native Cloudflare Builds connection requires Workers Builds Configuration access, which the current local OAuth session does not grant. Do not claim push-to-deploy is enabled until that connection is verified.

Cloudflare references:
- https://developers.cloudflare.com/workers/static-assets/
- https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/
