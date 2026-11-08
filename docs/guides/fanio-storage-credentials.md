# Fanio Storage credential issuer

This release adds a Storage-only credential issuer for the existing Fanio runtime. It does not create buckets or database roles, migrate objects, change images or enable native serving. Fanio keeps R2 serving until its separate migration gates pass.

The issuer reads an atomic service-owned mode 0600 version-1 policy at `.secrets/upstream/storage-runtime-credentials.json`. Policy records retain SHA-256 bootstrap hashes only, bind environment, purpose, epoch, bucket and role, and reject hash reuse across purposes or environments. Keep plaintext bootstrap values in Fanio server secret custody. Environment signing keys remain on Sbarbase. A separate publishable gateway key is required.

Originals and evidence have distinct bootstrap values and scoped roles. Issued JWTs use audience `sbarbase:storage` and expire after 3600 seconds. Removing a bootstrap policy prevents new issuance but does not revoke a JWT already issued. Immediate revocation requires underlying role capability or environment signing identity action.

The HTTP gateway rejects Storage-audience JWTs on non-Storage routes. This does not inspect Realtime websocket token frames. Realtime and Functions remain absent under this qualification.

Local evidence includes 26 issuer/private-reader/gateway tests, 11 Fanio config/token and actual issuer-to-client bridge tests, and the targeted runtime closure typecheck. Actual installed role flags, restrictive policies, bucket privacy, native signature acceptance and scoped API behavior require separate operator evidence.

Apply only the narrow signed safe release through the existing management updates apply request. The supervisor drains work, applies the exact release and retains backend custody through its supported update lifecycle. Handover requires its existing conditions and does not guarantee zero console interruption. Do not kill Bun, manually signal it, use ordinary systemctl restart, or substitute direct upgrade.py start --to for coordinated activation.

Rollback uses the supported management updates rollback request after its verdict permits. Preserve all data, current placement readers, native-only writes and independent recovery journals. Issuer rollback never means restoring stale commerce data or deleting buckets or objects.
