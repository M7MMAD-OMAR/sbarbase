# Image delivery

Measured on 2026-10-10 with the built console and its actual static handler in local Chromium. The login composition, transparent artwork and original Supabase Studio are preserved.

| Artwork | Previous WebP bytes | New WebP bytes | Preferred AVIF bytes |
| --- | ---: | ---: | ---: |
| 384 by 576 | 208,834 | 61,998 | 38,058 |
| 768 by 1152 | 768,182 | 212,468 | 119,992 |

At desktop scale 1 the preferred artwork transfer drops 81.8%. At scale 2 it drops 84.4%. WebP fallback drops 70.3% and 72.3%, respectively. These are encoded body bytes, not claims about production load time or Core Web Vitals.

The picture sources apply above 760 CSS pixels, matching the existing visibility breakpoint. A transparent inline fallback avoids fetching hidden artwork on mobile. The browser chooses one responsive format and size. Dimensions remain explicit and decorative artwork has low fetch priority. AVIF receives `image/avif` and the same immutable caching and restrictive static path policy as WebP.

## Reproduce

Use Python with the public Pillow 12.3.0 wheel, including AVIF and WebP encoders:

```sh
python3 -m venv /tmp/sbarbase-image-tools
/tmp/sbarbase-image-tools/bin/pip install Pillow==12.3.0
/tmp/sbarbase-image-tools/bin/python lab/optimize_console_images.py
bun run build:ui
bun lab/image-loading-browser.ts
```

The generator uses the checked-in PNG master, not a previously compressed derivative. Encoding settings are explicit. Encoder libraries and platform versions can change output bytes; regenerate, inspect and rerun budgets before accepting a new toolchain. Building or serving the console does not require Python or Pillow.

The browser check records actual selected URLs and encoded transfer bytes at 1440 pixels with scales 1 and 2, 900 pixels and 390 pixels. It also exercises WebP fallback by making the AVIF source ineligible. This is format selection simulation in Chromium, not a test on an older physical browser. Captures and network receipts are written under `output/playwright/image-loading`.

## Asset inventory and boundaries

The two artwork variants were the heavy console assets, previously 977,016 bytes combined. The console logo is already a 7,904 byte WebP at 96 by 116 pixels. The website copy has the same size. Its social JPEG is 58,220 bytes at 1200 by 630, and its video poster is 118,940 bytes at 1920 by 1080. These remain candidates for a separate website delivery checkpoint; social card compatibility must remain tested.

Design screenshots include a 836,062 byte PNG concept, three original Supabase reference PNGs between 59,902 and 274,786 bytes, and four JPEG console captures between 20,023 and 42,195 bytes. They are documentation references, not downloaded by the console. The original cactus and logo PNG masters are 2,486,256 and 503,968 bytes. They remain editable source material. Frozen evidence and reference images are preserved so their recorded hashes stay valid.

This image checkpoint does not close the full engineering programme. Fresh host installation, native recovery and transfer, resource calibration, maintenance, public release and remaining scope-specific acceptance in the engineering plans still require their own implementation and evidence. Track current acceptance in the checkpoint run and engineering ledger rather than treating a successful image build as product acceptance.

## Format research

[MDN image formats](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Image_types) documents transparent AVIF and WebP and picture fallback. [MDN multimedia performance](https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Performance/Multimedia) covers responsive dimensions and image compression. The local transfer receipts above measure this particular product.
