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

import { Usage } from './usage.ts';

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
// `deny_net` is defence in depth only. It takes literal addresses (a CIDR range makes worker creation fail). In the
// measured table (deny_net_forms in the evidence) the listed spellings and many unlisted ones of the same address are
// blocked; the bare decimal integer, the NAT64 form and every host name that resolves to it pass. 127.0.0.1 and the gateway
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

/** The key the event worker signs with: not the JWT secret itself but an HMAC of it under a fixed label, computed the same way in
 * event-worker/index.ts. Auth signs its tokens with the JWT secret and a function holds the service role key, so a function could have
 * Auth mint a token with any role claim; it cannot make one that verifies under this derived key. */
let derivedKey: string | undefined;
async function eventSecret(): Promise<string> {
  if (derivedKey !== undefined) return derivedKey;
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(JWT_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const bytes = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode('sbarbase event worker v1')));
  derivedKey = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return derivedKey;
}

/** The claims of an unexpired HS256 token signed with this environment's JWT secret (or `secret`), or null. */
async function jwtClaims(authorization: string | null, secret: string = JWT_SECRET): Promise<Record<string, unknown> | null> {
  const token = authorization?.match(/^Bearer\s+(\S+)$/i)?.[1];
  const parts = token?.split('.');
  if (!token || parts?.length !== 3 || !secret || !JWT_SECRET) return null;
  try {
    const header = JSON.parse(atob(parts[0].replace(/-/g, '+').replace(/_/g, '/')));
    if (header.alg !== 'HS256') return null;
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const signature = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(parts[0] + '.' + parts[1])));
    if (base64url(signature) !== parts[2]) return null;
    const claims = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof claims.exp !== 'number' || claims.exp > Date.now() / 1000 ? claims : null;
  } catch { return null; }
}
async function validJwt(authorization: string | null): Promise<boolean> {
  return (await jwtClaims(authorization)) !== null;
}

/** What the event worker has reported about each function (event/index.ts). Memory only: it starts empty with the container. */
const usage = new Usage();

/** `POST /_sb_events`: a batch from the event worker, signed as role sb_events with the derived key above; no function can. A batch that is
 * refused with a 4xx is dropped by the worker, so the limit is generous (1 MiB) and the worker sends at most 256 KiB. */
async function events(request: Request): Promise<Response> {
  if (request.method !== 'POST') return json(405, 'Method not allowed');
  const claims = await jwtClaims(request.headers.get('authorization'), await eventSecret());
  if (!claims || claims.role !== 'sb_events') return json(401, 'Invalid JWT');
  if (Number(request.headers.get('content-length')) > 1024 * 1024) return json(413, 'Too large');
  try {
    const body = JSON.parse((await request.text()).slice(0, 1024 * 1024));
    usage.add(Array.isArray(body) ? body.slice(0, 500) : []);
  } catch { return json(400, 'Invalid batch'); }
  return new Response(null, { status: 204 });
}

/** `GET /_sb_stats/<name>`: one function's buckets and lines, for the control plane, which presents the service role token. */
async function stats(request: Request, name: string): Promise<Response> {
  if (request.method !== 'GET') return json(405, 'Method not allowed');
  const claims = await jwtClaims(request.headers.get('authorization'));
  if (!claims || claims.role !== 'service_role') return json(401, 'Invalid JWT');
  if (!NAME.test(name)) return json(400, 'Invalid function');
  const item = usage.of(name);
  return Response.json(item ? { known: true, ...item } : { known: false, since: Date.now(), buckets: [], lines: [] });
}

/** `POST /_sb_boot/<name>/<version>`: starts a worker on a version folder that has been written and is not live yet, so a deploy
 * can be refused before the manifest switches to it. It reports what the runtime reports at worker creation: a syntax error, an
 * import that cannot be resolved, a missing entry point. It does not run the function and cannot see a top level throw or a missing
 * Deno.serve. The name starts with an underscore, so the gateway never sends it; a caller inside this container holds the same
 * service role key the control plane uses, so it can ask for a boot of a folder of this environment and nothing else. */
let booting = 0;
async function boot(request: Request, name: string, version: string): Promise<Response> {
  if (request.method !== 'POST') return json(405, 'Method not allowed');
  const claims = await jwtClaims(request.headers.get('authorization'));
  if (!claims || claims.role !== 'service_role') return json(401, 'Invalid JWT');
  if (!NAME.test(name) || !/^[a-z0-9-]{1,64}$/.test(version)) return json(400, 'Invalid function');
  // At most two at once: each creates a worker of up to 150 MiB in a 384 MiB container, and code holding the key can call this in a loop.
  if (booting >= 2) return new Response(JSON.stringify({ message: 'Busy' }), { status: 429, headers: { 'content-type': 'application/json', 'retry-after': '1' } });
  booting++;
  try {
    await EdgeRuntime.userWorkers.create({
      servicePath: `${FUNCTIONS}/${version}/${name}`,
      memoryLimitMb: 150,
      workerTimeoutMs: 15_000,
      cpuTimeSoftLimitMs: 5_000,
      cpuTimeHardLimitMs: 10_000,
      noModuleCache: false,
      envVars: [],
      forceCreate: true,
      tmpFsConfig: { quota: TMP_QUOTA_BYTES },
      permissions: PERMISSIONS,
    });
    return Response.json({ ok: true });
  } catch (error) {
    return new Response(JSON.stringify({ ok: false, message: error instanceof Error ? error.message.slice(0, 1000) : String(error).slice(0, 1000) }),
      { status: 422, headers: { 'content-type': 'application/json' } });
  } finally {
    booting--;
  }
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
  const staged = url.pathname.match(/^\/_sb_boot\/([^/]+)\/([^/]+)$/);
  if (staged) return await boot(request, staged[1]!, staged[2]!);
  if (url.pathname === '/_sb_events') return await events(request);
  const stat = url.pathname.match(/^\/_sb_stats\/([^/]+)$/);
  if (stat) return await stats(request, stat[1]!);
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
