# Studio that opens without setup

Status: proposal, nothing built. Written 2026-10-19 after the owner could not open Studio from the public console and saw only the SSH tunnel notice.

## The problem

On a server without `SBARBASE_STUDIO_HOST` the console cannot open Studio. It explains an SSH tunnel instead ([Studio guide](../../guides/studio.md)). Turning it on needs three things at once: a wildcard DNS record, a certificate that covers the wildcard, and the same pattern in the service and in the TLS proxy (`--studio-host`). A person who runs Sbarbase for clients should not need to know any of that. The goal is that pressing **Open Supabase dashboard** works on a fresh install, and where it cannot, the console says exactly one thing to do.

## Why the address is separate today

The Studio image serves one project at the root of its own address. Each environment therefore gets its own origin, which also keeps its session cookie, tickets and same-origin checks apart from every other environment's. Serving Studio under a path of the console origin (`/studio/<id>/`) would put every environment's Studio on the console's origin, so one Studio page could read the console's browser storage, which holds the management session. That is a security regression, not just a convenience, so it is not an option unless the origin split is kept some other way.

## Options

1. **Guided setup in the console.** The Studio card asks for the base domain once, then shows the three exact items (the DNS record, the certificate names, the two settings) with copy buttons, and checks each one live (DNS resolves, certificate covers the name, the proxy answers a probe). It never guesses; it shows which step is missing. Lowest risk, removes the guessing, still needs the operator to touch DNS.
2. **One command on the server.** `lab/studio-address.py set <domain>` writes the service drop-in and the proxy flag, validates the pattern, restarts both, and prints the one DNS record still needed. Pairs with option 1, which would call the same validation.
3. **Automatic certificate.** The proxy obtains a wildcard certificate itself through DNS-01 for providers whose API token the operator supplies (Cloudflare first). Removes the certificate step. Adds a stored provider token, which needs its own secret handling and review.
4. **Local-only Studio inside the console tab.** For loopback installs, `*.studio.localhost` already works. No change.

## Recommendation

Do option 1 and option 2 first, in that order of value: the console shows what is missing and a single command fixes the server side. Treat option 3 as a separate decision because it stores a DNS provider token.

## Acceptance

- On a server with the setting missing, the Studio card names the missing step in plain words, in English and Arabic, and offers the command.
- After the operator adds the single DNS record, the card turns ready without a restart of the console and **Open Supabase dashboard** opens the environment.
- A wrong pattern, a certificate that does not cover the name, or a proxy and service that disagree is reported by the card, never by a tab that fails to load.
- No change to the per-environment origin, cookie, ticket and write checks in the Studio guide.
- Reader-facing docs updated in English and Arabic in the same change.

## Open questions

- Whether the Studio image supports a base path at all. If it does not, the origin split above stays and this plan stands as written.
- Which providers to support for option 3, and where the token is kept.
