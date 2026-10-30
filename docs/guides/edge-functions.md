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

## Deploy with the Supabase CLI or an agent

With a personal access token (`sbp_...`), the Supabase CLI deploys to this server through a profile file, `supabase --profile ./sbarbase-profile.yaml functions deploy <name> --project-ref <ref> --use-api`, and sets secrets with `secrets set`. An AI agent connected to the server's `/mcp` endpoint deploys with its `deploy_edge_function` tool. [AI agents](agents.md) has the profile, the token and the steps.

## The Edge Functions page

Owners and admins open **Edge Functions** in the environment's page. It has two pages, laid out like the Supabase cloud's: **Functions** and **Secrets**.

**Functions** lists every function with its address (with a copy button), when it was created and last deployed, and how many times it was deployed. Search by name, sort by name, created or updated, and refresh. The switch at the top shows whether the environment's runtime is Off, Applying, On or Not applied, and turns it on or off. **Add function** offers the deploy command for a project folder, which stays the way for several files, or a form that deploys one `index.ts` and suits a webhook or a first try.

Open a function to see its tabs. **Download** and **Test** are in the header of every tab.

- **Overview.** Pick 15 min, 1 hour, 3 hours or 1 day: total invocations, the 5xx rate and the 4xx rate with a chart, errors since the last deploy, and the average and longest response time. The numbers are counted in memory by Sbarbase's gateway, a day of minutes per function, and start again when Sbarbase restarts; the page says since when it has been counting. The time is until the function starts to answer, not until a streamed answer ends. CPU time and memory per function are not collected yet. Calls you send from **Test** count too.
- **Logs.** The calls to this function that the gateway counted in the time range you choose (15 min to 1 day, 1 hour at first), from the last 200 it holds: time, method, path, status and time taken. The query string is never kept, and a part of the path that looks like a token is hidden. Filter by text, show errors only, or turn on **Live**. Below, the lines the runtime wrote for the whole environment: the runtime does not say which function wrote a line, so they are not filtered by function.
- **Code.** The files of the running version as a tree, with shared code under `../_shared`, in a read only viewer. A file over 256 KiB, or past 2 MiB in all, is not shown; **Download** has everything.
- **Settings.** Whether **Verify JWT** is on (it is set when you deploy), how to call the function from cURL, JavaScript, Swift, Flutter and Python with a placeholder key (**Show publishable key** puts this environment's own in the example), the deploy command, and **Delete function**, which asks you to type the function's name.
- **Test.** Sends one request from your browser with this environment's publishable key: method, body, headers and query parameters. It shows the status, the time and the body. A browser lets the page read only the response headers the gateway exposes, so the header list is short.
- **Download.** A ZIP laid out like a project: `<name>/index.ts` and `_shared/...`.

**Secrets** adds or replaces one secret (a value may have several lines), lists the custom secrets with a digest and when each was set, deletes any of them, and lists the default secrets. A value is never shown again. The digest is an HMAC-SHA256 with a key that belongs to this environment, so it shows whether a value changed, the same value in another environment gives another digest, and a short secret cannot be guessed from it without that key. It is not the plain SHA256 that the Supabase cloud may show. A secret saved before this page existed shows its time as unknown.

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

Add your own on the **Secrets** page (for example `STRIPE_SECRET_KEY`) and read them with `Deno.env.get('STRIPE_SECRET_KEY')`. A value is never shown again after you save it.

## Who may call a function

- By default a function needs a valid JWT: the publishable key (supabase-js sends it), a [secret key](api-keys.md) from a server, which the function receives as a `service_role` token, or a signed-in user's token. A wrong key is refused before the function runs.
- A function deployed with **Require a valid JWT** off (`verify_jwt = false`) also accepts calls with no key at all, for a payment provider's or another service's webhook. It then checks the call itself, for example with the provider's signature header, which reaches it unchanged.

## How it runs

- One runtime per environment, holding at most 384 MiB and half a CPU; each function runs in its own worker of at most 150 MiB, stopped after 150 seconds. A worker can write temporary files under `/tmp`, but `/tmp` is one 32 MiB space shared by all functions of the environment; a write past it fails, and you should not count on files surviving the call. It cannot read other files, the runtime's own or another function's.
- The runtime container is locked down: no extra Linux capabilities, no privilege gain, a read-only root file system and an unprivileged user. Function calls count in **Usage** like the other services.
- A function sees only its own environment's code and secrets. The runtime has its own internal network that holds only this environment's Auth and REST; other environments' services are not on it. Files go through Sbarbase, which always asks the shared Storage for this environment's files, whatever the function's request says. This separation has not been checked on a running installation yet ([details](../engineering/FUNCTIONS-NETWORK-ISOLATION.md)). A function can still open connections to the internet, and, until the host firewall rules in [server deployment](server-deployment.md#limit-what-edge-functions-can-reach) are applied, to the host and to private networks. Those rules are generated for you, reviewed by you and applied by you; applying them on a real server has not been tried yet.
- Calls to one function are on its **Logs** tab; the runtime's own output for the whole environment is also under **Usage**, **Logs**, source **Edge Functions**, next to the other services.
- Functions reach Storage on the host's port 25433. A host firewall that drops incoming traffic by default (ufw does) needs the rule in [server deployment](server-deployment.md#firewall-two-routes-from-containers-to-the-host).
- Studio's Edge Functions page lists each function's deployed code ([Studio](studio.md)).
- **Turn off Edge Functions** stops the runtime and keeps the code; turning it on or deploying again starts it.

CI deploys a functions folder with the command above on a clean machine with every change, and calls the functions through the gateway with supabase-js, including one that uses supabase-js from npm with the service role ([evidence](../evidence/docker-functions-checks.json)).

## Limits

- An environment holds at most 100 functions.
- Per function numbers are kept by one Sbarbase process in memory (a day of minutes, at most 40,000 minute counts per environment, the oldest dropped first), so a restart empties them, and a second Sbarbase process would count its own. Deleting an environment does not delete its function secrets or digest files yet; the secret files already behave this way.
- The console shows and downloads code but does not edit it yet; write a function there with one file, or deploy a project folder.
- Code is text files, up to 500 files and 10 MiB per deploy. Deployed code lives in `.lab/upstream/functions/` and is not in the environment backups yet: keep your project folder.
- `SUPABASE_DB_URL` is not given; reach the database through `SUPABASE_URL`.
- `supabase functions deploy` works only with `--use-api` and a Sbarbase profile ([AI agents](agents.md#the-supabase-cli)); without `--use-api` the CLI bundles with Docker and uploads a format this server does not read.
- A request or response body goes through the gateway up to the upload limit (`SBARBASE_UPLOAD_LIMIT_MB`).
- At most 4 calls to functions run at once in an environment; the next is answered `429` with `retry-after: 1` until one finishes. REST and Auth for the same environment keep their own share. A function with **Require a valid JWT** off accepts calls with no key, so anyone who can reach the address can fill those 4 places for that environment. There is no limit per caller yet.
- The runtime has 384 MiB for all workers. Four busy workers of up to 150 MiB each do not fit, and if the container runs out of memory it stops and stays stopped, with every function of the environment, until the next deploy or until you turn Edge Functions off and on. Memory use under several busy functions at once has not been measured.
- The locked down runtime was tried on one Linux workstation with rootful Docker. Not tested: npm packages with native addons (the `noexec` temporary spaces may stop them from loading), rootless Docker, a remapped user namespace, SELinux enforcing, a host that allows the container swap, and other systems.
- The 32 MiB of `/tmp` is shared by every function of the environment and counts against the runtime's memory. The cache for `npm:`, `jsr:` and URL imports is a separate 32 MiB space (supabase-js, zod and ms together used 16 MiB); if your imports exceed it, later imports fail until the runtime container is recreated.
- Blocking a function from the metadata address is only a first layer: it covers that address and most of its spellings, but the bare decimal form, the NAT64 form and any host name that resolves to it get past it. The real boundary is the host firewall rules above.
