# Fanio Storage credential issuer

This release adds a Storage-only credential issuer for the existing Fanio runtime. It does not create buckets or database roles, migrate objects, change images or enable native serving. Fanio keeps R2 serving until its separate migration gates pass.

The issuer reads an atomic service-owned mode 0600 version-1 policy at `.secrets/upstream/storage-runtime-credentials.json`. Policy records retain SHA-256 bootstrap hashes only, bind environment, purpose, epoch, bucket and role, and reject hash reuse across purposes or environments. Keep plaintext bootstrap values in Fanio server secret custody. Environment signing keys remain on Sbarbase. A separate publishable gateway key is required.

Originals and evidence have distinct bootstrap values and scoped roles. Issued JWTs use audience `sbarbase:storage` and expire after 3600 seconds. Removing a bootstrap policy prevents new issuance but does not revoke a JWT already issued. Immediate revocation requires underlying role capability or environment signing identity action.

The HTTP gateway rejects Storage-audience JWTs on non-Storage routes. This does not inspect Realtime websocket token frames. Realtime and Functions remain absent under this qualification.

Local evidence includes 26 issuer/private-reader/gateway tests, 11 Fanio config/token and actual issuer-to-client bridge tests, and the targeted runtime closure typecheck. Actual installed role flags, restrictive policies, bucket privacy, native signature acceptance and scoped API behavior require separate operator evidence.

Apply only the narrow signed safe release through the existing management updates apply request. The supervisor drains work, applies the exact release and retains backend custody through its supported update lifecycle. Handover requires its existing conditions and does not guarantee zero console interruption. Do not kill Bun, manually signal it, use ordinary systemctl restart, or substitute direct upgrade.py start --to for coordinated activation.

Rollback uses the supported management updates rollback request after its verdict permits. Preserve all data, current placement readers, native-only writes and independent recovery journals. Issuer rollback never means restoring stale commerce data or deleting buckets or objects.

The native TLS proxy removes the public Host and forwards to the console loopback listener. The issuer accepts that path only when the host supplies the exact configured fixed literal-loopback console listener origin, the request Host matches it, and the proxy sets the exact configured public forwarding host and HTTPS scheme. Forwarding headers alone, other listener ports, browser headers, query strings and request bodies remain refused. A real owned loopback proxy-shape regression covers this deployment boundary.

The proxy destination comes from the same SBARBASE_CONSOLE_PORT parser as the gateway. It stays fixed across candidate promotion. The private ephemeral listener used for candidate probes and MCP is not trusted as a TLS proxy destination. The two-listener regression covers this separation. A separate network-off fixture uses the actual console TLS proxy, an upgrade-enabled fixed listener and the actual Fanio token client with an owned trusted CA. It verifies successful issuance, browser and wrong-host rejection, and untrusted-CA refusal. This is not an installed supervisor rehearsal.

Transport provenance comes from a process-local WeakMap on the original Request created by serveLocal. It records the actual bound ingress only for direct owned loopback listeners or hops proven to be opened by their front. Host and forwarding headers cannot create this context. The issuer compares it to the configured fixed console port. The private candidate port and direct inner-hop access remain rejected even with a forged fixed Host. No routing or listener ownership policy changes.
