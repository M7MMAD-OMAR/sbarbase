# Pinned Studio SMTP regression fixtures

These are unmodified module extracts from the original Supabase Studio image
`2026.10.12-sha-3699278`, commit
`369927892fde63d07b199d4e80fe05ae780573d5`.

Copyright Supabase and contributors. Licensed under Apache-2.0; see the repository
`LICENSE` and `NOTICE`. The production image is not modified on disk. Proxy
compatibility is bound to the SHA-256 of the complete extracted module.
`manifest.json` records module and original chunk hashes.

Original source:

- https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/studio/data/auth/auth-config-query.ts
- https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/studio/components/interfaces/Auth/SmtpForm/SmtpForm.tsx

Tests execute the original and transformed modules with controlled query and form
dependencies. They verify scope, cache keys, hosted behavior, self-hosted template
preservation, confirmation text, exact module binding and HTTP representation rules.
