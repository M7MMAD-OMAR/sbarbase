---
name: sbarbase
description: Deploy or connect an app to a self-hosted Sbarbase server (Supabase-compatible). Use when the user says Sbarbase, "deploy to Sbarbase", "our server", or wants a Supabase project created, migrated or deployed on their own Sbarbase installation instead of supabase.com.
---

# Deploying to Sbarbase

Sbarbase runs many Supabase projects on the user's own server. It speaks Supabase: supabase-js,
the Supabase MCP tools and Supabase's Management API all work against it. These steps assume
the `sbarbase` MCP server is connected (`claude mcp list` shows it). If it is not, tell the user
to create an access token in the Sbarbase console (Access tokens) and run:

```bash
claude mcp add --transport http sbarbase https://<their console>/mcp --header "Authorization: Bearer sbp_..."
```

Never ask for the token in chat and never write it to a file in the repository.

## Words

- A Sbarbase "environment" is what the Supabase tools call a "project". Each has its own
  database, keys and API address. A Sbarbase project groups environments (production first).
- The project URL is the server's address plus `/<runtime>`, for example
  `https://console.example.com/e_0123...`. Always take it from `get_project_url` or the
  `api_url` field of `get_project`; never build a `supabase.co` address.

## Deploy an app

1. **Find or create the project.** `list_projects`; if the user named an existing one, use it.
   Otherwise `get_cost`, then `confirm_cost`, then `create_project` with the app's name and the
   organization the user chooses (`list_organizations`). A new project shows `COMING_UP`; call
   `get_project` every 20 to 30 seconds until it is `ACTIVE_HEALTHY` (about a minute or two).
2. **Database.** Apply each file in `supabase/migrations/` in order, oldest first, with
   `apply_migration` (name = the file name without the timestamp and `.sql`, query = its
   content). Check `list_migrations` first and skip ones already applied (their names match).
   The first call that writes may answer that database access is being turned on: wait a minute
   and retry. An answer asking to turn database access off and on once is for the user, in the
   console. If the repo has `supabase/seed.sql` and the user wants seed data, run it with
   `execute_sql`.
3. **Security check.** Run `get_advisors` (security). Every public table needs row level security
   and policies; fix what it reports with a new migration, and tell the user.
4. **Edge Functions.** For each folder in `supabase/functions/` (except `_shared`), call
   `deploy_edge_function` with its files; put shared code under names starting `../_shared/`.
   Use `verify_jwt` from `supabase/config.toml` (`[functions.<name>] verify_jwt = false`), true by
   default. Secrets the functions read (`Deno.env.get`) are set by the user in the console (the
   environment's Edge Functions section); list which ones are needed, never ask for their values.
5. **App settings.** `get_project_url` and `get_publishable_keys` (a project without a
   publishable key gets one named `default`). Write them where the app expects them, usually
   `.env` or `.env.local` (for example `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`,
   or the `NEXT_PUBLIC_` names). Keep `.env` out of git.
6. **Verify.** Call one REST endpoint or function with the publishable key, and read `get_logs`
   (service `api`, `auth` or `edge-function`) if it fails. Report what was created and the URL.

## Rules

- A secret key (`sb_secret_...`, acts as `service_role`) never goes into browser or mobile code;
  only into server code or function secrets. The server refuses it from browsers.
- Do not run destructive SQL (DROP, TRUNCATE, DELETE without WHERE) unless the user asked for it.
- Sbarbase has no branching, no type generation endpoint (use
  `supabase gen types typescript --db-url "<connection string>"` when the user has the database
  connection string) and no project deletion through the API (the console does it).
- A token may be read only; if a write is refused with "read only", say so and stop.
- SQL, keys and function code need the user to be an owner or admin of the organization; a
  viewer's token only lists.
