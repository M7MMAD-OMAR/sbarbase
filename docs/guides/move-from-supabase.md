[العربية](move-from-supabase.ar.md)

# Move a project from Supabase

One command copies a Supabase project (Cloud or self-hosted) into a new Sbarbase environment: tables, row level security, users with their passwords, and Storage files. Your application keeps using supabase-js; only the address and the key change.

## 1. Check first

From the Supabase dashboard, open **Connect** and copy the **Session pooler** or direct connection string with your database password. Then, on the Sbarbase server:

```bash
python3 lab/import_project.py <environment> --dry-run <<'JSON'
{"database_url": "postgresql://postgres.<ref>:<db password>@aws-0-<region>.pooler.supabase.com:5432/postgres"}
JSON
```

It reads the project without changing anything and lists what cannot move (for example Vault secrets, or an extension this server does not have), what behaves differently, and what you will set again by hand.

## 2. Import

Create a **new, empty** environment in the console, then run the same command without `--dry-run`, adding the project's API address and its `service_role` key (Project Settings, API) so the Storage files can be copied:

```bash
python3 lab/import_project.py <environment> --report import-report.json <<'JSON'
{"database_url": "postgresql://postgres.<ref>:<db password>@aws-0-<region>.pooler.supabase.com:5432/postgres",
 "api_url": "https://<ref>.supabase.co", "service_role_key": "<service_role key>"}
JSON
```

Settings are read from stdin, never from the command line, so passwords appear in no process list. The source is only read.

What moves, in order:

1. The `public` schema: tables, views, functions, policies and your own triggers, with the grants the source gave `anon`, `authenticated` and `service_role`, and no more.
2. Users and their sign-in identities with password hashes: people sign in with their old passwords.
3. Every row in `public`.
4. Buckets, then every file, with its owner, so Storage policies on `owner_id` keep working.
5. Your triggers on `auth.users` and `storage.objects` (for example "create a profile at sign-up"), recreated after the rows so they do not run again for imported users.
6. A comparison of row counts, users, identities, buckets and files, source against target. The import only reports success when they all match.

## 3. Point your application at Sbarbase

1. Create a publishable key on the environment's page.
2. Replace the Supabase URL and anon key in your application with the environment's address and the new key.
3. Set again what lives outside the database: [sign-in settings and OAuth providers](sign-in.md), email, [Edge Functions](edge-functions.md) (deploy the same `supabase/functions` folder), [Realtime](realtime.md).

People sign in again once: sessions do not move. Everything else, including their passwords, does.

## Limits

- The target must be a new, empty environment. A failed import leaves it to be deleted and created again; it is never half-repaired.
- Only the `public` schema is copied among your own schemas; other schemas are listed in the check.
- Vault secrets, cron jobs and the project's JWT secret do not move; the check says when that matters.
- Files larger than the upload limit (`SBARBASE_UPLOAD_LIMIT_MB`) are reported and not copied.
