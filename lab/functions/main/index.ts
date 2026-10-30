// The main service of one environment's Edge Functions, run by the upstream edge-runtime
// (docs/engineering/EDGE-FUNCTIONS.md). Sbarbase's gateway checks the API key and sends
// `/<function>/...` here; this finds the function's current version, checks its JWT when
// the function asks for that, and runs it in its own worker with the environment's
// variables and secrets, as Supabase does.
//
// `/_sb/<auth|rest|storage>/v1/...` is the address functions reach the environment's own
// API at (SUPABASE_URL). Auth and REST are that environment's services on its own network,
// the only internal one this container joins. Storage is shared and takes its tenant from a
// header, which code in this container could set to anything, so it is reached through the
// host listener at the network's gateway, which sets the tenant itself. The services check
// every token themselves. The gateway never sends a name that starts with an underscore, so
// this route is not reachable from outside.

const FUNCTIONS = Deno.env.get('SB_FUNCTIONS_DIR') ?? '/home/deno/functions';
const SECRETS = Deno.env.get('SB_SECRETS_FILE') ?? '/run/sbarbase/secrets.json';
const SELF = Deno.env.get('SB_SELF_URL') ?? 'http://127.0.0.1:9000';
const JWT_SECRET = Deno.env.get('SB_JWT_SECRET') ?? '';
const UPSTREAMS: Record<string, string | undefined> = {
  auth: Deno.env.get('SB_AUTH_URL'),
  rest: Deno.env.get('SB_REST_URL'),
  storage: Deno.env.get('SB_STORAGE_URL'),
};
const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const HOP = new Set(['host', 'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'content-length',
  'x-forwarded-host', 'x-forwarded-for', 'x-forwarded-proto']);

// What each function's worker may do: the pinned runtime's per worker `permissions` (snake case keys). A key left out
// is DENIED, not defaulted (without `allow_import` a jsr: import fails at boot), so every allow list is given. `[]`
// for net, env and import means everything: npm:, jsr: and https: imports, outbound calls, SUPABASE_URL and the
// worker's own variables. Egress is not limited here; its boundary is the host firewall (lab/functions_egress.py).
// `deny_net` is defence in depth only. It takes literal addresses (a CIDR range makes worker creation fail), blocks
// just the spellings listed, and a host name that resolves to a listed address passes it. 127.0.0.1 and the gateway
// stay allowed: SUPABASE_URL is this service, and Storage is the listener at the gateway.
// Files: isolation between functions and from the container comes from the runtime's virtual filesystem; limiting
// `allow_read` and `allow_write` to /tmp is a second layer. The /tmp quota is per worker, not per container, so N
// workers can hold N times 64 MiB. The evidence is in docs/evidence/functions-sandbox-hardening-2026-10-30.
const TMP_QUOTA_BYTES = 64 * 1024 * 1024;
const PERMISSIONS = {
  allow_net: [] as string[],
  allow_env: [] as string[],
  allow_read: ['/tmp'],
  allow_write: ['/tmp'],
  allow_import: [] as string[],
  deny_net: ['169.254.169.254', '[::ffff:169.254.169.254]', '[fd00:ec2::254]'],
};

type Entry = { version: string; verify_jwt: boolean; entrypoint?: string };

function json(status: number, message: string) {
  return new Response(JSON.stringify({ message }), { status, headers: { 'content-type': 'application/json' } });
}

async function readJson<T>(path: string, fallback: T): Promise<T> {
  try { return JSON.parse(await Deno.readTextFile(path)) as T; } catch { return fallback; }
}

function base64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** True for an unexpired HS256 token signed with this environment's JWT secret. */
async function validJwt(authorization: string | null): Promise<boolean> {
  const token = authorization?.match(/^Bearer\s+(\S+)$/i)?.[1];
  const parts = token?.split('.');
  if (!token || parts?.length !== 3 || !JWT_SECRET) return false;
  try {
    const header = JSON.parse(atob(parts[0].replace(/-/g, '+').replace(/_/g, '/')));
    if (header.alg !== 'HS256') return false;
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(JWT_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const signature = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(parts[0] + '.' + parts[1])));
    if (base64url(signature) !== parts[2]) return false;
    const claims = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof claims.exp !== 'number' || claims.exp > Date.now() / 1000;
  } catch { return false; }
}

async function proxy(request: Request, url: URL): Promise<Response> {
  const match = url.pathname.match(/^\/_sb\/(auth|rest|storage)\/v1(\/.*)?$/);
  const upstream = match && UPSTREAMS[match[1]];
  if (!match || !upstream) return json(404, 'Unknown route');
  const target = new URL(upstream);
  target.pathname = match[2] || '/';
  target.search = url.search;
  const headers = new Headers();
  for (const [name, value] of request.headers) if (!HOP.has(name.toLowerCase())) headers.set(name, value);
  return await fetch(target, { method: request.method, headers, body: request.body, redirect: 'manual' });
}

Deno.serve(async (request: Request) => {
  const url = new URL(request.url);
  if (url.pathname.startsWith('/_sb/')) return await proxy(request, url);
  const name = url.pathname.split('/')[1] ?? '';
  if (!NAME.test(name)) return json(404, 'Function not found');
  const manifest = await readJson<{ functions?: Record<string, Entry> }>(`${FUNCTIONS}/functions.json`, {});
  const entry = manifest.functions?.[name];
  if (!entry || !/^[a-z0-9-]{1,64}$/.test(entry.version)) return json(404, 'Function not found');
  if (entry.verify_jwt && !(await validJwt(request.headers.get('authorization')))) return json(401, 'Invalid JWT');
  const secrets = await readJson<Record<string, string>>(SECRETS, {});
  const envVars: [string, string][] = [
    ...Object.entries(secrets).filter(([key, value]) => typeof value === 'string' && !key.startsWith('SUPABASE_')),
    ['SUPABASE_URL', SELF + '/_sb'],
    ['SUPABASE_ANON_KEY', Deno.env.get('SUPABASE_ANON_KEY') ?? ''],
    ['SUPABASE_SERVICE_ROLE_KEY', Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''],
  ];
  try {
    const worker = await EdgeRuntime.userWorkers.create({
      servicePath: `${FUNCTIONS}/${entry.version}/${name}`,
      memoryLimitMb: 150,
      workerTimeoutMs: 150_000,
      cpuTimeSoftLimitMs: 10_000,
      cpuTimeHardLimitMs: 20_000,
      noModuleCache: false,
      envVars,
      forceCreate: false,
      tmpFsConfig: { quota: TMP_QUOTA_BYTES },
      permissions: PERMISSIONS,
    });
    return await worker.fetch(request);
  } catch (error) {
    console.error(`function ${name} failed:`, error instanceof Error ? error.message : String(error));
    return json(500, 'Function failed; see the Edge Functions log');
  }
});
