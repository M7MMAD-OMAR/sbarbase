[العربية](agents.ar.md)

# AI agents and the API

An AI agent, such as Claude Code, Cursor or Codex, can work on this server the way it works on Supabase. From a project's folder, tell it "deploy this app to Sbarbase", and it can:
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
4. Choose read only if you only want the agent to look. A read-only token runs SQL read only and cannot change anything.
5. Choose when it expires, then press **Create token**. Copy it: it is shown only once.

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

The tools are Supabase's own, from the official MCP server:

| Group | Tools |
| --- | --- |
| Account | `list_organizations`, `get_organization`, `list_projects`, `get_project`, `create_project` (with `get_cost` and `confirm_cost` first; the cost is always zero here, and the region it asks for is ignored) |
| Database | `list_tables`, `list_extensions`, `list_migrations`, `apply_migration`, `execute_sql` |
| Edge Functions | `list_edge_functions`, `get_edge_function`, `deploy_edge_function` |
| Development | `get_project_url` (this server's address), `get_publishable_keys` (with a token that can write, it makes one named `default` when the project has none it can show) |
| Debugging | `get_logs` (requests, Auth, Storage, Realtime and Edge Functions logs), `get_advisors` (security: public tables without row level security) |

A read-only token gets only the tools that read.

What is not there yet:
- TypeScript type generation. Use `supabase gen types typescript --db-url` with the [database connection string](database-access.md).
- Branching, pausing and deleting projects. Delete a project in the console.
- Storage settings.

## How it is kept safe

- **SQL.** It runs as the environment's developer login, the same login as the [database connection](database-access.md): full access to that one database, never a superuser and never another environment, and it cannot create schemas. The first SQL call that may change something turns database access on if it was off, then asks to try again a minute later; a call that only reads never turns it on. For an environment whose access was on before 0.5.0, turn access off and on once in the console, so it gets the migration history schema.
- **Read-only tokens.** For a read-only token, SQL runs read only on the server, whatever the agent asks. It runs as one statement inside a read-only transaction that is always rolled back, so no data can change and `COMMIT; ...` cannot get out of it ([evidence](../evidence/agent-sql-checks-2026-10-17.json)). It still runs as the developer login, so one statement can do what that login may do outside data: cancel this environment's own database sessions, or hold a lock until the 130 second deadline. Read-only tokens of owners and admins can run SQL; viewers cannot.
- **Changes.** Every change goes through the console's own routes, with the same role checks and the same audit log, where it appears under your name.
- **Ending a token.** New calls stop at once when you revoke it, when it expires, when your password is reset on the server ([operator setup](operator-setup.md#a-forgotten-password)), or when the installation's MFA policy changes; a query already running finishes or meets its deadline. A membership that is removed or lowered applies at the next call.
- **Audit.** A migration or SQL that may change data is recorded in the activity log with the token's id and a digest of the query, never the query itself.
- **What a token cannot do.** It cannot create, list or revoke tokens, and it cannot change members, sign-in settings or the server.
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

Anything else answers `404` or `501` with a message.

### The official Supabase MCP server on your machine

Supabase's MCP server can also run on your machine and point at this server:

```bash
npx -y @supabase/mcp-server-supabase@latest --api-url https://console.example.com --access-token sbp_...
```

It works, with differences: its `get_project_url` names a `supabase` domain instead of this server, and it lists a ClickHouse log query tool and health advisors that this server does not answer. The hosted endpoint above gives the right address and only the tools that work, so prefer it.

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
