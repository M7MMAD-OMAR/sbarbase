// The runtime's event worker (`--event-worker`, docs/engineering/EDGE-FUNCTIONS.md, "Event worker"). It receives an event for every worker
// that boots, logs or shuts down, keeps the few fields the console shows, and sends them in small batches to the main service of this same
// container, signed with the environment's JWT secret so that no function can pass for it: a function worker is given only the variables the main
// service chooses, never SB_JWT_SECRET. If the main service does not answer (it starts after this worker does), the batch waits and is tried again;
// at most 1000 events wait, the oldest are dropped, and a failure here never reaches a function.
import { takeBatch } from './batch.ts';

const JWT_SECRET = Deno.env.get('SB_JWT_SECRET') ?? '';
const TARGET = 'http://127.0.0.1:9000/_sb_events';
const MAX_QUEUE = 1000, BATCH = 200, BATCH_BYTES = 256 * 1024, EVERY_MS = 2000;

const base64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const text = (value: unknown) => base64url(new TextEncoder().encode(JSON.stringify(value)));

// The signing key is derived from the JWT secret (the same HMAC as eventSecret in main/index.ts), not the secret itself: Auth signs with the
// secret and a function can have Auth mint tokens, but it cannot make one that verifies under this key.
async function derived(): Promise<string> {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(JWT_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const bytes = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode('sbarbase event worker v1')));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function token(): Promise<string> {
  const head = text({ alg: 'HS256', typ: 'JWT' }), body = text({ role: 'sb_events', iss: 'sbarbase', exp: Math.floor(Date.now() / 1000) + 120 });
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(await derived()), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return `${head}.${body}.${base64url(new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${head}.${body}`))))}`;
}

// deno-lint-ignore no-explicit-any
function slim(data: any) {
  const event = data?.event ?? {}, memory = event.memory_used ?? {};
  return {
    timestamp: data?.timestamp, event_type: data?.event_type, metadata: { service_path: data?.metadata?.service_path },
    event: { boot_time: event.boot_time, reason: event.reason, cpu_time_used: event.cpu_time_used, level: event.level,
      msg: typeof event.msg === 'string' ? event.msg.slice(0, 2000) : undefined, memory_used: { total: memory.total, heap: memory.heap, external: memory.external } },
  };
}

const queue: unknown[] = [];
let sending = false;
/** Sends the oldest events, at most 200 and at most 256 KiB of JSON (messages with quotes or Arabic letters grow when written as JSON). A network
 * error or a 5xx or 429 keeps the batch for the next try; any other answer drops it, so one batch the main service refuses cannot block the rest. */
async function flush() {
  if (sending || !queue.length || !JWT_SECRET) return;
  sending = true;
  const batch = takeBatch(queue, BATCH, BATCH_BYTES);
  try {
    const response = await fetch(TARGET, { method: 'POST', headers: { authorization: `Bearer ${await token()}`, 'content-type': 'application/json' }, body: JSON.stringify(batch) });
    await response.body?.cancel();
    if (!response.ok && (response.status >= 500 || response.status === 429)) throw new Error(String(response.status));
  } catch {
    queue.unshift(...batch);
    if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
  } finally { sending = false; }
}
setInterval(() => { void flush(); }, EVERY_MS);

// deno-lint-ignore no-explicit-any
const manager = new (globalThis as any).EventManager();
for await (const data of manager) {
  if (!data) continue;
  queue.push(slim(data));
  if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
}
