# Website image delivery

The 2026-10-10 image continuation preserves the public website layout, Arabic and English copy, video, captions and original Supabase interfaces.

| Served asset | Before bytes | After bytes | Reduction |
| --- | ---: | ---: | ---: |
| Video poster, 1920 by 1080 | 118,940 JPEG | 51,672 WebP | 56.6% |
| Social card, 1200 by 630 | 58,220 JPEG | 49,606 JPEG | 14.8% |

The video poster uses WebP directly because the video poster attribute has no picture format negotiation. The social card keeps a JPEG URL and image/jpeg content type. Logos remain 7,904 byte transparent WebP files, already small at 96 by 116 pixels. Documentation references, original design masters and frozen evidence are not downloaded by the console or website. Preserve them for authoring and evidence integrity rather than replacing them with lossy delivery copies.

## Reproduction and checks

The public Pillow 12.3.0 wheel with WebP support is sufficient. Install it into a virtual environment and run `website/optimize_images.py`. The preserved source JPEGs are `docs/design/masters/website-explainer-poster.jpg` and `website-social.jpg`. The outputs are generated independently from those source bytes every time. These sources are the original checked-in JPEGs, not lossless render masters. No private home path or package registry is required. Python is not required to build or serve the website.

`bun website/image-loading-browser.ts` serves the built static files locally and decodes the actual poster and social card in Chromium at desktop and mobile widths for both languages. It checks one poster request, the byte budget, image dimensions and JPEG social delivery, and records network receipts and screenshots. Captures stop video loading to inspect the poster and use light theme with reduced motion. The comparison uses an identical original baseline and capture fixture. The test does not establish provider deployment, external social crawler support or Core Web Vitals.

Rebuild the website after updating assets with `bun run --cwd website build`. Run `bun test website/tests/site.test.ts`. If the film is rendered again, refresh the preserved poster source deliberately, regenerate the delivery asset and review current quality and transfer receipts. Source encoder and platform changes can change exact output bytes, so budgets and visual inspection remain required.

The browser harness uses the checked-in `lab/mfa-browser/package.json` and `bun.lock`, pinned to public Playwright 1.62.1. Install with `bun install --cwd lab/mfa-browser --frozen-lockfile`, then `bun run --cwd lab/mfa-browser playwright install chromium`. Linux hosts may additionally need Playwright's documented Chromium system libraries. No owner workstation path is required. Network receipts record the viewport separately from decoded image dimensions; the original baseline receipt has the old image-width field, while baseline PNG dimensions and the harness context establish its viewport.

[MDN image formats](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Image_types) describes WebP image delivery and JPEG compatibility. These local measurements are specific to this website. The complete engineering programme remains open; image delivery is a separate local checkpoint.
