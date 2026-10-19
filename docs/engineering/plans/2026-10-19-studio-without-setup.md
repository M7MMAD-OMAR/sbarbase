# Studio that opens without setup

Status: proposal, nothing built. Written 2026-10-19 after the owner could not open Studio from the public console and saw only the SSH tunnel notice.

## The problem

On a server without `SBARBASE_STUDIO_HOST` the console cannot open Studio. It explains an SSH tunnel instead ([Studio guide](../../guides/studio.md)). Turning it on needs three things at once: a wildcard DNS record, a certificate that covers the wildcard, and the same pattern in the service and in the TLS proxy (`--studio-host`). A person who runs Sbarbase for clients should not need to know any of that. The goal is that pressing **Open Supabase dashboard** works on a fresh install, and where it cannot, the console says exactly one thing to do.

## Why the address is separate today

The Studio image serves one project at the root of its own address. Each environment therefore gets its own origin, which also keeps its session cookie, tickets and same-origin checks apart from every other environment's. Serving Studio under a path of the console origin (`/studio/<id>/`) would put every environment's Studio on the console's origin, so one Studio page could read the console's browser storage, which holds the management session. That is a security regression, not just a convenience, so it is not an option unless the origin split is kept some other way.

## What was learned on 2026-10-19

- Studio opened from the public console on the owner's server after a one-time setup: an Origin CA certificate covering `*.sbarah.com` (key generated on the server), `SBARBASE_STUDIO_HOST` and the proxy's `--studio-host` set to `studio-{id}.sbarah.com`, and one DNS record for the one environment. Checked layer by layer: the Studio address answered with the console's own 401 page, then the dashboard loaded and every `/api/` request returned 200.
- That setup exposed a real defect, fixed in 0.5.7: Bun's `fetch` decodes a compressed body but keeps `content-encoding`, so Studio's gzip pages failed at the next hop (Cloudflare 520). Both proxies now fetch with `decompress: false`.
- One DNS record per environment is not a design. A zone wide `*` record on the owner's domain is also unacceptable there, because the zone holds many unrelated sites (Workers, Netlify) and an installation must not touch them.

## Why one origin per environment stays

- Under a path of the console origin: Studio renders data application visitors control, and the console origin holds the management session. Rejected.
- One shared Studio origin with the environment chosen by cookie: a script running in one environment's Studio could call `/__sbarbase/switch` from the same origin and open another environment the same operator can reach, crossing a tenant boundary. Studio's browser state is also keyed by the fixed project ref `default`, so environments would share saved SQL. Rejected.
- A port on the console host: cookies are not port scoped and Cloudflare proxies few ports. An iframe: the Fetch-Metadata gate refuses it and it adds clickjacking surface. Rejected.

## The permanent shape

The same one the PaaS tools use (Coolify, Dokploy, Vercel previews): the operator chooses one base domain once, with a wildcard DNS record and a wildcard certificate, and every project and environment gets its own address under it automatically. Creating, renaming or deleting a project never touches DNS or certificates.

1. **A domain meant for it.** The base domain must hold nothing else, because the wildcard answers every unnamed name under it. A dedicated domain, or a name two levels deep where the certificate story allows it. On Cloudflare's free plan the edge certificate covers only first level names, so the pattern is `studio-{id}.dedicated.example`.
2. **Console setup card.** Asks for the base domain, shows the one DNS record and the certificate names with copy buttons, and checks each live (DNS answers, certificate covers the name, proxy answers a probe). It names the missing step; it never falls back to the generic tunnel text.
3. **One server command** that writes the service setting and the proxy flag from the single domain, validates the pattern and restarts both.
4. **Later, optional:** built in certificates (ACME DNS-01 for the wildcard, or on demand certificates for DNS only names) so even the certificate step disappears. This stores a DNS provider token and needs its own review.

## Acceptance

- On a fresh install, entering one base domain and adding one wildcard record is the whole setup; new projects need nothing.
- A missing record, a certificate that does not cover the name, or a proxy and service that disagree is reported by the card in plain words, English and Arabic, never by a tab that fails to load.
- No change to the per-environment origin, cookie, ticket and write checks in the Studio guide.
- Reader-facing docs updated in English and Arabic in the same change.

## Open questions

- Whether the Studio image supports a base path at all. It does not change the decision above.
- Which DNS providers to support for built in certificates, and where the token is kept.
