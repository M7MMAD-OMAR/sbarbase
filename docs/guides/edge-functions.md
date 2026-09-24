[العربية](edge-functions.ar.md)

# Edge Functions

Edge Functions run your own Deno code next to an environment, called with `supabase.functions.invoke()` at `<address>/functions/v1/<name>`, as on Supabase. Each environment has its own runtime, the original upstream edge-runtime, and it starts with the first deploy.

## Deploy from a Supabase project

If you already have functions in a Supabase project, deploy the whole `supabase/functions` folder in one command from a checkout of Sbarbase:

```bash
SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts https://console.example.com <environment id> ../my-app/supabase/functions
```

- It asks for your operator password (or reads `SBARBASE_PASSWORD`) and never prints it.
- Every folder with an `index.ts` becomes a function; name some after the folder to deploy only those.
- `_shared` is deployed beside each function, so `import { corsHeaders } from '../_shared/cors.ts'` works unchanged.
- `verify_jwt = false` under `[functions.<name>]` in `supabase/config.toml` is honoured, as with the Supabase CLI.
- `npm:`, `jsr:` and URL imports work: the runtime reaches the internet.

The environment id is on the environment's page in the console. A redeploy takes effect at once, with no restart; a request already running finishes on the version it started with.

## Write one in the console

On the environment's page, under **Edge Functions**, **Write a function** takes a name and an `index.ts` and deploys it. It suits small functions; a project folder suits the rest.

## Call a function

```js
const { data, error } = await supabase.functions.invoke('hello-world', { body: { name: 'Sbarbase' } })
```

Every function gets these variables, like on Supabase:

| Variable | What it is |
|---|---|
| `SUPABASE_URL` | This environment's API, for `createClient()` inside the function |
| `SUPABASE_ANON_KEY` | Acts as a visitor; row level security applies |
| `SUPABASE_SERVICE_ROLE_KEY` | Acts as the service; bypasses row level security. Keep it inside functions |

Add your own under **Secrets** (for example `STRIPE_SECRET_KEY`) and read them with `Deno.env.get('STRIPE_SECRET_KEY')`. A value is never shown again after you save it.

## Who may call a function

- By default a function needs a valid JWT: the publishable key (supabase-js sends it) or a signed-in user's token. A wrong key is refused before the function runs.
- A function deployed with **Require a valid JWT** off (`verify_jwt = false`) also accepts calls with no key at all, for a payment provider's or another service's webhook. It then checks the call itself, for example with the provider's signature header, which reaches it unchanged.

## How it runs

- One runtime per environment, holding at most 384 MiB and half a CPU; each function runs in its own worker of at most 150 MiB, stopped after 150 seconds.
- A function sees only its own environment's code, secrets and services. The runtime reaches the environment's Auth, REST and Storage directly on the internal network.
- Logs are under **Logs**, source **Edge Functions**, next to the other services.
- **Turn off Edge Functions** stops the runtime and keeps the code; turning it on or deploying again starts it.

CI deploys a functions folder with the command above on a clean machine with every change, and calls the functions through the gateway with supabase-js, including one that uses supabase-js from npm with the service role ([evidence](../evidence/docker-functions-checks.json)).

## Limits

- Code is text files, up to 500 files and 10 MiB per deploy. Deployed code lives in `.lab/upstream/functions/` and is not in the environment backups yet: keep your project folder.
- `SUPABASE_DB_URL` is not given; reach the database through `SUPABASE_URL`.
- The Supabase CLI's own `supabase functions deploy` talks to Supabase's platform API and does not work here; use the command above.
- A request or response body goes through the gateway up to the upload limit (`SBARBASE_UPLOAD_LIMIT_MB`).
