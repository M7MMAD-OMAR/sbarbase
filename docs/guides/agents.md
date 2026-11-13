[العربية](agents.ar.md)

# AI agents and the API

An AI agent, such as Claude Code, Cursor or Codex, connects through a scoped personal access token. Each request needs both a token capability and the owner's current organization role. Depending on the grants, it can work on this server the way it works on Supabase. From a project's folder, tell it "deploy this app to Sbarbase", and it can:
- create the project;
- run the migrations;
- deploy the Edge Functions;
- set their secrets;
- read the logs;
- write the app's settings.

It connects with a personal access token and the server's MCP endpoint, which runs Supabase's own MCP server.

## 1. Create an access token

1. In the console, open **Access tokens** (under Workspace).
2. Give the token a name, such as "Claude Code on my laptop".
3. Choose what it can reach:
   - everything you can reach;
   - one organization;
   - one project.
4. Choose a permission preset: read only, settings administration, selected capabilities or all agent capabilities. Expand or change individual grants as needed. The default is read only for one project.
5. Dangerous grants require a separate acknowledgement. SQL writes, executable function deployment and secret API key creation can enable data deletion. The settings preset excludes these and explicit delete/revoke grants.
6. Choose when it expires, then press **Issue token**. Copy it: it is shown only once. The Arabic console calls this button **إصدار الرمز**.

One token has one resource scope. Use separate tokens when different projects or organizations need different permissions. Organization scopes include future projects in that organization; the all scope includes future resources the owner may access. These scopes never exceed the owner's current role.

After this source update, pre-existing access tokens migrate to read capabilities only. They do not retain implicit write access. Revoke and reissue a token to grant writes or change capabilities. Older deployed revisions may not yet implement this permission model; check the installed revision before relying on it.

A token is `sbp_` followed by 40 characters, the form of a Supabase personal access token. It acts as you, with your role in each organization. A viewer's token can list organizations, projects and functions; SQL, API keys, function code and every change need an owner or admin. Sbarbase keeps only its hash.

## 2. Connect the agent

The address is your console's address followed by `/mcp`, for example `https://console.example.com/mcp`.

**Claude Code**, once, in a terminal:

```bash
claude mcp add --transport http sbarbase https://console.example.com/mcp --header "Authorization: Bearer sbp_..."
```

**Cursor** (`.cursor/mcp.json`), **VS Code** (`.vscode/mcp.json`, with `"type": "http"`) and others that take a URL and headers:

```json
{
  "mcpServers": {
    "sbarbase": {
      "url": "https://console.example.com/mcp",
      "headers": {"Authorization": "Bearer ${env:SBARBASE_ACCESS_TOKEN}"}
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.sbarbase]
url = "https://console.example.com/mcp"
bearer_token_env_var = "SBARBASE_ACCESS_TOKEN"
```

Keep the token out of files you commit; the examples above read it from an environment variable where the client allows it.

## 3. Ask

In any project's folder: "Deploy this app to Sbarbase: create a project, apply the migrations in supabase/migrations, deploy the functions in supabase/functions, and write the URL and publishable key to .env." The [Sbarbase skill](#the-sbarbase-skill-for-claude-code) teaches Claude Code those steps.

## What the agent can do

The hosted endpoint retains Supabase's official tools and adds the two Sbarbase administration tools described below. API authorization enforces each capability even if a client lists a tool the token cannot use. Official groups include:

| Group | Tools |
| --- | --- |
| Account | `list_organizations`, `get_organization`, `list_projects`, `get_project`, `create_project` (with `get_cost` and `confirm_cost` first; the cost is always zero here, and the region it asks for is ignored) |
| Database | `list_tables`, `list_extensions`, `list_migrations`, `apply_migration`, `execute_sql` |
| Edge Functions | `list_edge_functions`, `get_edge_function`, `deploy_edge_function` |
| Development | `get_project_url` (this server's address), `get_publishable_keys` (only with `keys.write`, it can create one named `default` when the project has none it can show) |
| Debugging | `get_logs` (requests, Auth, Storage, Realtime and Edge Functions logs), `get_advisors` (security: public tables without row level security) |

A read-only token gets only the tools that read. SQL runs read only unless `database.write` is explicitly granted, including when the token can modify other settings. A read-only transaction protects stored data; it does not make SQL free of operational effects such as locks or cancelling permitted database sessions.

What is not there yet:
- TypeScript type generation. Use `supabase gen types typescript --db-url` with the [database connection string](database-access.md).
- Branching, pausing and deleting projects. Delete a project in the console.
- Storage bucket/object administration and full hosted Storage settings. The token cannot administer buckets or objects through this management registry.

## Scoped administration through MCP and API

`get_sbarbase_operations` lists the fixed operations available to the token and their input descriptions. `execute_sbarbase_operation` accepts:

```json
{
  "operation": "auth.settings.read",
  "project_ref": "abcdefghijklmnopqrst"
}
```

`project_ref` is the environment's 20-letter Supabase-compatible ref from `GET /v1/projects`, not its project UUID. Organization operations instead use `organization_id`. Project-scoped tokens cannot use organization-wide operations to list other projects or read organization audit records.

The equivalent HTTP interface is:

- `GET /v1/agent/operations`
- `POST /v1/agent/execute` with `{operation, project_ref?, organization_id?, input?, dry_run?}`.

Read operations execute normally. Writes default to preview. A preview reports operation, target and permission admission without calling a mutation handler. It does not validate every setting, reserve a revision, simulate SQL, create a backup or guarantee later success. Execution needs `dry_run: false` and rechecks authorization and the normal management handler's validation.

For redirect URLs, read `auth.settings.read`, preserve the settings you need, then preview `auth.settings.update` with `input` containing the intended sign-in settings. Include `base_revision` from the read response to reject a stale edit. Execute the same operation with `dry_run: false`, then read settings and application state back. Supported settings include `site_url`, `redirect_urls`, signup, providers and session limits. The existing management validation and asynchronous application workflow still apply.

The registry also describes supported SMTP, SMS, mail templates, SSO, project/environment names, Data API, scheduled-jobs, Realtime, function settings/secrets, keys, logs and metrics operations. Unknown operations and arbitrary management URLs are rejected. Discover the current registry rather than inventing operation names.

### Capability catalog

| Capability | Authority |
| --- | --- |
| `projects.read` | List and inspect projects and environments |
| `projects.write` | Create projects/environments and change their names |
| `organization.read` | Read the allowed organization and member information |
| `organization.write` | Rename an allowed organization |
| `auth.read` | Read supported project Auth settings with secrets masked |
| `auth.write` | Change project sign-in security, redirect URLs, provider and mail settings |
| `auth.delete` | Remove an SSO provider, explicit dangerous grant |
| `database.read` | Read connection/Data API state, security advisors and run read-only SQL where the owner role permits |
| `database.configure` | Change supported Data API and scheduled-jobs configuration |
| `database.write` | Run writable SQL/migrations and enable direct database access, explicit dangerous grant |
| `functions.read` | List functions, read code and secret names where the owner role permits |
| `functions.write` | Deploy executable code, configure function service and set secrets, explicit dangerous grant |
| `functions.delete` | Delete functions, explicit dangerous grant |
| `storage.read` | Read only the Storage information supported by the installed API |
| `keys.read` | Read publishable keys and nonsecret key metadata |
| `keys.write` | Create API keys, including secret keys, explicit dangerous grant |
| `keys.delete` | Revoke API keys, explicit dangerous grant |
| `logs.read` | Read allowed logs, metrics and audit events |
| `realtime.read`, `realtime.write` | Read/change supported Realtime settings |

The settings-administration preset grants supported nondestructive operations but can still affect app availability or security, for example through Auth or Data API changes. It is not a guarantee that an agent will make good decisions. SQL, arbitrary code and secret API keys permit destructive data effects; no text filter can promise otherwise. Grant them only when required, preferably on an isolated environment first.

Connection is manual with a bearer token. This update does not add OAuth login or hosted ChatGPT/claude.ai connector registration.

## How it is kept safe

- **SQL.** It runs as the environment's developer login, the same login as the [database connection](database-access.md): full access to that one database, never a superuser and never another environment. It may create schemas of its own, never under a name the platform uses ([database access](database-access.md)). The first SQL call that may change something turns database access on if it was off, then asks to try again a minute later; a call that only reads never turns it on. For an environment whose access was on before 0.5.0, turn access off and on once in the console, so it gets the migration history schema.
- **Read-only tokens.** For a read-only token, SQL runs read only on the server, whatever the agent asks. It runs as one statement inside a read-only transaction that is always rolled back, so no data can change and `COMMIT; ...` cannot get out of it ([evidence](../evidence/agent-sql-checks-2026-10-17.json)). It still runs as the developer login, so one statement can do what that login may do outside data: cancel this environment's own database sessions, or hold a lock until the 130 second deadline. Read-only tokens of owners and admins can run SQL; viewers cannot.
- **Changes.** Every change goes through the console's own routes, with the same role checks and the same audit log, where it appears under your name.
- **Ending a token.** New calls stop at once when you revoke it, when it expires, when your password is reset on the server ([operator setup](operator-setup.md#a-forgotten-password)), or when the installation's MFA policy changes; a query already running finishes or meets its deadline. Signing out of the console does not end a token, and neither does a console session that expires and signs you out on its own; before 0.5.13 both did, so a token made earlier may already have stopped and needs replacing. A membership that is removed or lowered applies at the next call.
- **Audit.** A migration or SQL that may change data is recorded in the activity log with the token's id and a digest of the query, never the query itself.
- **What a token cannot do.** It cannot create, list or revoke access tokens, change account security or installation policy, or delete/purge projects or environments. Supported project Auth settings are available with `auth.write`; this is distinct from management-account sign-in security.
- **Rate limit.** Each token is limited to 300 requests a minute.

Agent output is not a review: read what an agent changed before you rely on it, as with any automated change. Give each agent its own token, so you can revoke one alone.

## The Supabase-compatible API

The same token works with the parts of Supabase's Management API that tools use, at `https://console.example.com/v1/...`:

```bash
curl -H "Authorization: Bearer $SBARBASE_ACCESS_TOKEN" https://console.example.com/v1/projects
```

| Supabase API | On Sbarbase |
| --- | --- |
| `GET /v1/organizations`, `/v1/organizations/{slug}` | Your organizations |
| `GET /v1/projects`, `/v1/projects/{ref}`, `POST /v1/projects` | Each environment is a Supabase "project": `ref` is 20 letters, `api_url` its address here; `POST` creates a project with its production environment |
| `GET`, `POST /v1/projects/{ref}/api-keys`, `DELETE .../api-keys/{id}` | [API keys](api-keys.md); a publishable key's `api_key` is readable, a secret key's only when it is created |
| `POST /v1/projects/{ref}/database/query` | SQL: `{query, parameters?, read_only?}` |
| `GET`, `POST /v1/projects/{ref}/database/migrations` | Migration history in `supabase_migrations.schema_migrations`, as the Supabase CLI keeps it |
| `GET /v1/projects/{ref}/functions`, `.../functions/{slug}`, `.../functions/{slug}/body`, `POST .../functions/deploy?slug=`, `DELETE .../functions/{slug}` | [Edge Functions](edge-functions.md) |
| `GET`, `POST`, `DELETE /v1/projects/{ref}/secrets` | Function secrets, listed by name only |
| `GET /v1/projects/{ref}/analytics/endpoints/logs`, `/advisors/security`, `/advisors/performance` | Logs and advisors |
| `GET`, `PATCH /v1/projects/{ref}/postgrest` | The exposed schemas, extra search path and row limit ([Data API](data-api.md)) |

Anything else answers `404` or `501` with a message.

### The official Supabase MCP server on your machine

Supabase's MCP server can also run on your machine and point at this server:

```bash
bunx @supabase/mcp-server-supabase@latest --api-url https://console.example.com --access-token sbp_...
```

It works, with differences: its `get_project_url` names a `supabase` domain instead of this server, and it lists a ClickHouse log query tool and health advisors that this server does not answer. The hosted endpoint above gives the right address and only the tools that work, so prefer it.

### The Supabase CLI

The Supabase CLI talks to this server through a profile file:

```yaml
name: sbarbase
api_url: https://console.example.com
dashboard_url: https://console.example.com
project_host: console.example.com
```

```bash
export SUPABASE_ACCESS_TOKEN=sbp_...
supabase --profile ./sbarbase-profile.yaml projects list
supabase --profile ./sbarbase-profile.yaml functions deploy hello --project-ref <ref> --use-api
supabase --profile ./sbarbase-profile.yaml secrets set STRIPE_KEY=... --project-ref <ref>
```

These three were run against a real server. `functions deploy` needs `--use-api`: without it the CLI bundles with Docker and uploads a format this server does not read. Commands that connect to the database by the project's host name, such as `db push --linked`, do not work; see [database access](database-access.md) for migrations.

## The Sbarbase skill for Claude Code

The repository has a skill that tells Claude Code what "deploy to Sbarbase" means: the order of the steps, where to write the URL and key, and what to check afterwards. Copy it once:

```bash
mkdir -p ~/.claude/skills/sbarbase && curl -fsSL https://raw.githubusercontent.com/M7MMAD-OMAR/sbarbase/main/deploy/agent-skill/sbarbase/SKILL.md -o ~/.claude/skills/sbarbase/SKILL.md
```

## Limits

- Hosted connectors on claude.ai and ChatGPT need OAuth, which Sbarbase does not offer yet. Use clients that accept a header: Claude Code, Cursor, VS Code, Codex, Gemini CLI, and the Claude and OpenAI APIs.
- One SQL call runs for at most 120 seconds and answers at most 5 MiB.
- An agent's changes through the console's routes are recorded under your name; SQL and migrations also carry the token's id.
- A migration applied by an agent gets the time it was applied as its version, as on Supabase, not the timestamp in the repository's file name. The history therefore does not match the repository's file names; it records what was applied and when.


## Discover capabilities before changing a project

Public source 0.12.2 provides `GET /v1/capabilities` with a personal access token, or MCP `resources/list` followed by `resources/read` for `sbarbase://capabilities`. They return the same versioned installation policy, not installed extension state, live service health or token permissions. Every subsequent operation still checks authorization.

These additions and newer SDKs were excluded from the owner's private console revision based on 0.11.2. An older installation may answer 404 or report an unknown MCP resource. Check its [installed revision](../reference/status.md) and use the project/API operations supported there. Missing discovery does not mean that every API operation is unavailable.

Environments are independent databases, not Git branches. Hosted billing, free ClickHouse queries, GraphQL and full hosted health-advisor coverage are not provided. Agents must not bypass the `pg_stat_statements` restriction or grant shared-server roles to read other environments' statistics.

The public source's PGMQ adapter requires a declared manual migration. Do not activate it based only on a Studio listing or capability metadata. The operator must first qualify migration, backup and recovery, then inspect `pg_extension` in the intended environment. A source-code rollback does not reverse data-ownership changes. See [upgrades](upgrades.md) and [Studio](studio.md).

Native SMTP support depends on the source revision and pinned Studio image; see [Studio](studio.md#smtp-settings-in-original-studio). Public v0.12.2 lacks this repair. The adapter accepts SMTP fields only, and does not make unrelated hosted auth settings writable. Agents can keep using the supported Sbarbase settings interface; verify application state and an actual mail submission separately.
