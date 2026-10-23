// Reference TLS termination for the console, using only Bun and a certificate.
//
// Usage:
//   bun deploy/console-tls-proxy.ts --cert PATH --key PATH \
//       [--https-port 8443] [--http-port 8080] [--upstream http://127.0.0.1:PORT] \
//       [--public-host console.example.com] [--max-body BYTES] \
//       [--studio-host studio-{id}.example.com] [--retry-window MS]
// Without --studio-host the proxy reads SBARBASE_STUDIO_HOST, the setting the console reads, so one
// systemd drop-in configures both (lab/studio_address.py writes it).
//
// Only the public host, loopback names and, with --studio-host, an environment's Studio address
// (src/control/studio-host.ts) are served; any other host is refused with 421, so a wildcard
// DNS record pointing other names here never shows the console under them. A Studio address is
// passed on with its own Host, which is how the console tells Studio requests apart.
//
// It terminates HTTPS in front of the loopback console, redirects plain HTTP to
// HTTPS, and adds the transport security headers. It refuses to start unless the
// certificate and key are usable private files and the upstream is loopback.
// Request bodies, query strings, cookies and authorization headers are never
// logged: a line carries method, path, status and nothing else.
//
// While the console restarts (an upgrade), a request whose connection is refused never reached
// it, so it is sent again with backoff for up to --retry-window ms (SBARBASE_PROXY_RETRY_MS,
// 15000 by default, 0 turns it off); a Realtime socket's upstream connect likewise. Any other
// failure may have reached the console and is answered 502 as before, and a body larger than
// the buffered MiB is streamed, so it is never sent twice.
//
// An operator may use nginx, Caddy or the platform proxy instead; this exists so a
// deployment has one configuration that is exercised by the test suite.

import {statSync} from 'node:fs';
import {proxyStudioHosts, type StudioHosts} from '../src/control/studio-host';

type Options = {
  cert: string;
  key: string;
  httpsPort: number;
  httpPort: number;
  upstream: string;
  publicHost: string;
  maxBody: number;
  retryWindow: number;
  retrySource: string;
  studio?: StudioHosts;
};

// Bodies up to this size are read whole before they are forwarded.
const BUFFERED = 1024 * 1024;
// The largest upload Sbarbase accepts (SBARBASE_UPLOAD_LIMIT_MB, 50 by default), plus a MiB for
// the multipart framing around the file.
const MAX_BODY_DEFAULT = (Number(process.env.SBARBASE_UPLOAD_LIMIT_MB || 50) + 1) * 1024 * 1024;
// How long a refused upstream connection is retried, and the bound an operator may set.
const RETRY_WINDOW_DEFAULT = 15_000;
const RETRY_WINDOW_MAX = 60_000;
// At most this many requests and Realtime sockets wait for the upstream at once; more get 502 or
// 1011 at once, as before. A waiting request holds at most its buffered MiB.
const WAITING_REQUESTS_MAX = 256;
const WAITING_SOCKETS_MAX = 64;
// A Realtime message, and what a socket may queue while its upstream connects.
const SOCKET_MESSAGE_MAX = 4 * 1024 * 1024;
const SOCKET_QUEUE_BYTES = 1024 * 1024;
// Hop-by-hop headers, plus the framing headers a re-framed body must not carry,
// plus the client-supplied forwarding headers the proxy itself sets.
const STRIP_HEADERS = new Set(['host', 'connection', 'upgrade', 'keep-alive', 'te', 'trailer', 'transfer-encoding', 'content-length',
  'proxy-authorization', 'proxy-authenticate', 'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host', 'forwarded']);
// A host used in a Location header or forwarded to the backend is attacker input
// unless it is validated: no whitespace, no slashes, no scheme, digits only in a port.
const VALID_HOST = /^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$/;

function validatedHost(value: string | null): string | null {
  if (!value) return null;
  return VALID_HOST.test(value) ? value : null;
}

/** What a request's Host is: the console, an environment's Studio, or nothing to serve. */
function classify(host: string | null, publicHost: string, studio?: StudioHosts): {kind: 'console'} | {kind: 'studio'; host: string} | null {
  const value = validatedHost(host);
  if (!value) return null;
  const name = value.replace(/:\d+$/, '').toLowerCase();
  if (name === publicHost.replace(/:\d+$/, '').toLowerCase() || ['127.0.0.1', 'localhost'].includes(name)) return {kind: 'console'};
  if (studio?.runtime(name)) return {kind: 'studio', host: name};
  return null;
}
const misdirected = () => new Response('This address is not served here.', {status: 421, headers: {'content-type': 'text/plain', 'cache-control': 'no-store'}});

const wholeNumber = (value: string) => (/^\d+$/.test(value) ? Number(value) : NaN);

function parseArguments(argv: string[]): Options {
  const values: Record<string, string> = {};
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    if (!name.startsWith('--')) throw new Error('Unexpected argument: ' + name);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) throw new Error('Missing value for ' + name);
    values[name.slice(2)] = value;
    index += 1;
  }
  for (const required of ['cert', 'key', 'public-host']) {
    if (!values[required]) throw new Error('--' + required + ' is required');
  }
  if (values['retry-window'] === '') throw new Error('--retry-window is empty; give a number of ms or leave the flag out');
  if (!VALID_HOST.test(values['public-host'])) {
    throw new Error('--public-host must be a bare host name with an optional port: ' + values['public-host']);
  }
  return {
    cert: values.cert,
    key: values.key,
    httpsPort: Number(values['https-port'] ?? 8443),
    httpPort: Number(values['http-port'] ?? 8080),
    upstream: values.upstream ?? '',
    publicHost: values['public-host'],
    maxBody: Number(values['max-body'] ?? MAX_BODY_DEFAULT),
    // An empty flag is refused below; an empty environment variable means unset.
    retryWindow: wholeNumber(values['retry-window'] ?? (process.env.SBARBASE_PROXY_RETRY_MS || String(RETRY_WINDOW_DEFAULT))),
    retrySource: values['retry-window'] !== undefined ? '--retry-window' : 'SBARBASE_PROXY_RETRY_MS',
    studio: proxyStudioHosts(values['studio-host']),
  };
}

/** Read at most `limit` bytes and refuse as soon as the stream passes it.
 *
 * A Content-Length pre-check alone would let a chunked body buffer in full before
 * the refusal; this stops reading at the cap and cancels the rest. The first MiB is
 * read whole, as every API call is small; a larger body (a file upload) is passed on
 * as it arrives, still failing once it passes the cap, so it is never held in memory.
 */
async function readBoundedBody(request: Request, limit: number):
    Promise<{body: ArrayBuffer | ReadableStream<Uint8Array> | undefined; tooLarge: boolean; exceeded: () => boolean}> {
  let exceeded = false;
  const result = (body: ArrayBuffer | ReadableStream<Uint8Array> | undefined, tooLarge = false) => ({body, tooLarge, exceeded: () => exceeded});
  if (['GET', 'HEAD'].includes(request.method)) return result(undefined);
  if (!request.body) return result(new ArrayBuffer(0));
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    if (!value) continue;
    size += value.byteLength;
    if (size > limit) {
      void reader.cancel().catch(() => {});
      return result(undefined, true);
    }
    chunks.push(value);
    if (size > BUFFERED) {
      // A file upload: send what arrived, then the rest as it comes.
      const stream = new ReadableStream<Uint8Array>({
        start(controller) { for (const chunk of chunks) controller.enqueue(chunk); },
        async pull(controller) {
          const next = await reader.read();
          if (next.done) return controller.close();
          size += next.value.byteLength;
          if (size > limit) {
            exceeded = true;
            void reader.cancel().catch(() => {});
            return controller.error(new Error('Request body too large'));
          }
          controller.enqueue(next.value);
        },
        cancel(reason) { return reader.cancel(reason).catch(() => {}); },
      });
      return result(stream);
    }
  }
  reader.releaseLock();
  const buffer = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result(buffer.buffer);
}


/** The key must not be readable by anyone but its owner; a certificate is public. */
function assertRegular(path: string, label: string): void {
  if (!statSync(path).isFile()) throw new Error(label + ' is not a regular file: ' + path);
}

function assertPrivate(path: string, label: string): void {
  assertRegular(path, label);
  const mode = statSync(path).mode & 0o777;
  if (mode & 0o077) throw new Error(label + ' must not be group or world readable (mode ' + mode.toString(8) + '): ' + path);
}

function assertLoopbackUpstream(upstream: string): void {
  if (!upstream) return;
  const url = new URL(upstream);
  if (url.protocol !== 'http:') throw new Error('Upstream must be plain http on loopback');
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new Error('Upstream must be loopback, refusing to forward to ' + url.hostname);
  }
}

function securityHeaders(request: Request, publicHost: string): Record<string, string> {
  const headers: Record<string, string> = {
    'strict-transport-security': 'max-age=31536000; includeSubDomains',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'x-forwarded-proto': 'https',
    'x-forwarded-host': publicHost,
  };
  const clientHost = validatedHost(request.headers.get('host'));
  if (!clientHost && request.headers.get('host')) headers['x-forwarded-host-invalid'] = 'dropped';
  return headers;
}

/** The next wait before a retry: 100 ms doubling to 1 s, or null once the window would pass. */
function nextDelay(attempt: number, deadline: number): number | null {
  const delay = Math.min(100 * 2 ** attempt, 1000);
  return Date.now() + delay < deadline ? delay : null;
}

/** Wait, waking early when the client goes away; true when it did. */
function pause(ms: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(true);
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', done); resolve(false); }, ms);
    signal.addEventListener('abort', done, {once: true});
  });
}

// Bun's fetch reports a refused connect with this code (1.3.14, measured); nothing was sent then.
// A reset, even one before any response, may follow a delivered request and is never retried.
const refused = (error: unknown) => (error as {code?: string} | null)?.code === 'ConnectionRefused';

let waitingRequests = 0;
let waitingSockets = 0;

/** Fetch, sending again only while the connection is refused, the body is buffered, the client is
 * there, the window lasts and fewer than WAITING_REQUESTS_MAX already wait. `wait` runs once, when
 * the request starts waiting. */
async function forwardWithRetry(target: URL, init: RequestInit, signal: AbortSignal, wait: () => void): Promise<{response: Response; retries: number}> {
  const deadline = Date.now() + options.retryWindow;
  let waiting = false;
  try {
    for (let retries = 0; ; retries += 1) {
      try {
        return {response: await fetch(target, init), retries};
      } catch (error) {
        const delay = nextDelay(retries, deadline);
        if (!refused(error) || init.body instanceof ReadableStream || delay === null
            || (!waiting && waitingRequests >= WAITING_REQUESTS_MAX)) throw Object.assign(new Error('upstream'), {cause: error, retries});
        if (!waiting) {
          waiting = true;
          waitingRequests += 1;
          wait();
        }
        if (await pause(delay, signal)) throw Object.assign(new Error('client gone'), {cause: error, retries, aborted: true});
      }
    }
  } finally {
    if (waiting) waitingRequests -= 1;
  }
}

async function resolveUpstream(declared: string): Promise<string> {
  if (declared) return declared.replace(/\/$/, '');
  const state = Bun.file('.lab/upstream/server.json');
  if (!(await state.exists())) throw new Error('No upstream given and .lab/upstream/server.json is absent');
  const value = await state.json();
  if (typeof value.url !== 'string') throw new Error('server.json carries no console url');
  return value.url.replace(/\/$/, '');
}

const options = parseArguments(process.argv.slice(2));
for (const [label, value] of [['--https-port', options.httpsPort], ['--http-port', options.httpPort]] as const) {
  if (!Number.isInteger(value) || value < 0 || value > 65535) {
    throw new Error(label + ' must be a port number between 0 and 65535, not ' + String(value));
  }
}
if (!Number.isInteger(options.maxBody) || options.maxBody <= 0) {
  throw new Error('--max-body must be a positive number of bytes, not ' + String(options.maxBody));
}
if (!Number.isInteger(options.retryWindow) || options.retryWindow < 0 || options.retryWindow > RETRY_WINDOW_MAX) {
  throw new Error(options.retrySource + ' must be a whole number of ms between 0 and ' + RETRY_WINDOW_MAX + ', not ' + String(options.retryWindow));
}
assertRegular(options.cert, 'certificate');
assertPrivate(options.key, 'key');
const upstream = await resolveUpstream(options.upstream);
// Whatever the source, the upstream must be loopback: the console is never exposed.
assertLoopbackUpstream(upstream);

// An environment's Realtime socket. The console listener checks the key and the environment;
// this proxy only carries the socket across TLS.
const REALTIME_SOCKET = /^\/[a-z][a-z0-9_]{1,30}\/realtime\/v1\/websocket$/;
type Bridge = {target: string; path: string; upstream?: WebSocket; queue: (string | Uint8Array<ArrayBuffer>)[]; queued: number; closed?: boolean};
const closeCode = (code: number) => (code === 1000 || (code >= 3000 && code <= 4999) ? code : 1011);

const secure = Bun.serve<Bridge>({
  port: options.httpsPort,
  hostname: '127.0.0.1',
  tls: {cert: Bun.file(options.cert), key: Bun.file(options.key)},
  websocket: {
    // Bun's own default is 16 MiB; past this Bun drops the connection (the client sees 1006).
    maxPayloadLength: SOCKET_MESSAGE_MAX,
    open(socket) {
      const deadline = Date.now() + options.retryWindow;
      let waiting = false;
      // One line per socket, written once the upstream connect is settled.
      const settle = (outcome: string, retries: number) => {
        if (waiting) waitingSockets -= 1;
        waiting = false;
        console.log('WEBSOCKET ' + socket.data.path + ' ' + outcome + (retries ? ' after ' + retries + ' retries' : ''));
      };
      const connect = (retries: number) => {
        const upstreamSocket = new WebSocket(socket.data.target);
        upstreamSocket.binaryType = 'arraybuffer';
        socket.data.upstream = upstreamSocket;
        let opened = false;
        upstreamSocket.onopen = () => {
          opened = true;
          settle('open', retries);
          for (const message of socket.data.queue) upstreamSocket.send(message);
          socket.data.queue = [];
          socket.data.queued = 0;
        };
        upstreamSocket.onmessage = event => socket.send(typeof event.data === 'string' ? event.data : new Uint8Array(event.data));
        upstreamSocket.onclose = event => {
          if (opened) return socket.close(closeCode(event.code), event.reason);
          if (socket.data.closed) return settle('client gone', retries);
          // Refused before the handshake (Bun reports 1006 'Failed to connect'): nothing reached the console.
          const delay = event.code === 1006 && event.reason === 'Failed to connect' ? nextDelay(retries, deadline) : null;
          if (delay === null || (!waiting && waitingSockets >= WAITING_SOCKETS_MAX)) {
            settle('1011', retries);
            return socket.close(1011, 'Upstream unavailable');
          }
          if (!waiting) {
            waiting = true;
            waitingSockets += 1;
          }
          setTimeout(() => (socket.data.closed ? settle('client gone', retries) : connect(retries + 1)), delay);
        };
      };
      connect(0);
    },
    message(socket, message) {
      const upstreamSocket = socket.data.upstream;
      if (upstreamSocket?.readyState === WebSocket.OPEN) {
        upstreamSocket.send(message);
        return;
      }
      const size = typeof message === 'string' ? Buffer.byteLength(message) : message.byteLength;
      if (socket.data.queue.length >= 64 || socket.data.queued + size > SOCKET_QUEUE_BYTES) return socket.close(1013, 'Upstream not ready');
      socket.data.queued += size;
      socket.data.queue.push(typeof message === 'string' ? message : new Uint8Array(message));
    },
    close(socket) {
      socket.data.closed = true;
      socket.data.upstream?.close();
    },
  },
  async fetch(request, server) {
    const url = new URL(request.url);
    const served = classify(request.headers.get('host'), options.publicHost, options.studio);
    if (!served) {
      console.log('MISDIRECTED ' + url.pathname);
      return misdirected();
    }
    if (served.kind === 'console' && request.headers.get('upgrade')?.toLowerCase() === 'websocket' && REALTIME_SOCKET.test(url.pathname)) {
      const target = upstream.replace(/^http:/, 'ws:') + url.pathname + url.search;
      if (server.upgrade(request, {data: {target, path: url.pathname, queue: [], queued: 0}})) return undefined;
      return new Response('WebSocket upgrade failed', {status: 400});
    }
    const headers = securityHeaders(request, options.publicHost);
    const tooLarge = () => {
      console.log(request.method + ' ' + url.pathname + ' 413');
      return new Response('Request body too large', {status: 413, headers});
    };
    const declared = Number(request.headers.get('content-length') ?? '0');
    if (!['GET', 'HEAD'].includes(request.method) && declared > options.maxBody) return tooLarge();
    let response: Response;
    let exceeded: (() => boolean) | undefined;
    let retries = 0;
    let outcome = '';
    try {
      const read = await readBoundedBody(request, options.maxBody);
      exceeded = read.exceeded;
      if (read.tooLarge) return tooLarge();
      const body = read.body;
      const target = new URL(upstream + url.pathname + url.search);
      const sent = await forwardWithRetry(target, {
        method: request.method,
        // Hop by hop and framing headers are never forwarded, and the client's own
        // forwarding headers are replaced by ours: a spoofed Host or X-Forwarded-*
        // must not reach the console.
        headers: {
          ...Object.fromEntries([...request.headers].filter(([name]) => !STRIP_HEADERS.has(name.toLowerCase()))),
          ...headers,
          // A Studio address goes on as the Host the console routes by; checked above against the pattern.
          ...(served.kind === 'studio' ? {host: served.host, 'x-forwarded-host': served.host} : {}),
        },
        body,
        redirect: 'manual',
        // Keep the console's bytes and content-encoding together; see src/control/studio.ts.
        decompress: false,
        ...(body instanceof ReadableStream ? {duplex: 'half'} : {}),
      } as RequestInit, request.signal,
      // Bun ends a request idle for 10 s; only one waiting for the upstream gets the window and a margin.
      () => server.timeout(request, Math.ceil(options.retryWindow / 1000) + 15));
      const forwarded = sent.response;
      retries = sent.retries;
      const output = new Headers(forwarded.headers);
      // The body is re-framed, so the upstream's framing headers go too.
      for (const name of ['content-length', 'transfer-encoding']) output.delete(name);
      for (const [name, value] of Object.entries(headers)) output.set(name, value);
      response = new Response(forwarded.body, {status: forwarded.status, headers: output});
    } catch (error) {
      if (exceeded?.()) return tooLarge();
      retries = (error as {retries?: number}).retries ?? 0;
      if ((error as {aborted?: boolean}).aborted) outcome = ' client gone';
      response = new Response('Upstream unavailable', {status: 502, headers});
    }
    console.log(request.method + ' ' + url.pathname + ' ' + String(response.status) + outcome + (retries ? ' after ' + retries + ' retries' : ''));
    return response;
  },
});

const redirect = Bun.serve({
  port: options.httpPort,
  hostname: '127.0.0.1',
  fetch(request) {
    const url = new URL(request.url);
    // Only the configured public host, or a Studio address that matches the pattern, is used: an
    // attacker supplied Host header must never become the redirect target.
    const served = classify(request.headers.get('host'), options.publicHost, options.studio);
    if (!served) return misdirected();
    console.log('REDIRECT ' + url.pathname);
    const host = served.kind === 'studio' ? served.host : options.publicHost;
    return new Response(null, {status: 308, headers: {location: 'https://' + host + url.pathname + url.search}});
  },
});

console.log('TLS termination: https://127.0.0.1:' + secure.port + ' -> ' + upstream + ', refused connects retried for ' + options.retryWindow + ' ms');
console.log('Plain HTTP on 127.0.0.1:' + redirect.port + ' redirects to HTTPS');

const stop = () => {
  secure.stop(true);
  redirect.stop(true);
  process.exit(0);
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);