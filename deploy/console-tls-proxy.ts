// Reference TLS termination for the console, using only Bun and a certificate.
//
// Usage:
//   bun deploy/console-tls-proxy.ts --cert PATH --key PATH \
//       [--https-port 8443] [--http-port 8080] [--upstream http://127.0.0.1:PORT] \
//       [--public-host console.example.com] [--max-body BYTES]
//
// It terminates HTTPS in front of the loopback console, redirects plain HTTP to
// HTTPS, and adds the transport security headers. It refuses to start unless the
// certificate and key are usable private files and the upstream is loopback.
// Request bodies, query strings, cookies and authorization headers are never
// logged: a line carries method, path, status and nothing else.
//
// An operator may use nginx, Caddy or the platform proxy instead; this exists so a
// deployment has one configuration that is exercised by the test suite.

import {statSync} from 'node:fs';

type Options = {
  cert: string;
  key: string;
  httpsPort: number;
  httpPort: number;
  upstream: string;
  publicHost: string;
  maxBody: number;
};

// Bodies up to this size are read whole before they are forwarded.
const BUFFERED = 1024 * 1024;
// The largest upload Sbarbase accepts (SBARBASE_UPLOAD_LIMIT_MB, 50 by default), plus a MiB for
// the multipart framing around the file.
const MAX_BODY_DEFAULT = (Number(process.env.SBARBASE_UPLOAD_LIMIT_MB || 50) + 1) * 1024 * 1024;
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
assertRegular(options.cert, 'certificate');
assertPrivate(options.key, 'key');
const upstream = await resolveUpstream(options.upstream);
// Whatever the source, the upstream must be loopback: the console is never exposed.
assertLoopbackUpstream(upstream);

// An environment's Realtime socket. The console listener checks the key and the environment;
// this proxy only carries the socket across TLS.
const REALTIME_SOCKET = /^\/[a-z][a-z0-9_]{1,30}\/realtime\/v1\/websocket$/;
type Bridge = {target: string; upstream?: WebSocket; queue: (string | Uint8Array<ArrayBuffer>)[]};
const closeCode = (code: number) => (code === 1000 || (code >= 3000 && code <= 4999) ? code : 1011);

const secure = Bun.serve<Bridge>({
  port: options.httpsPort,
  hostname: '127.0.0.1',
  tls: {cert: Bun.file(options.cert), key: Bun.file(options.key)},
  websocket: {
    open(socket) {
      const upstreamSocket = new WebSocket(socket.data.target);
      upstreamSocket.binaryType = 'arraybuffer';
      socket.data.upstream = upstreamSocket;
      upstreamSocket.onopen = () => {
        for (const message of socket.data.queue) upstreamSocket.send(message);
        socket.data.queue = [];
      };
      upstreamSocket.onmessage = event => socket.send(typeof event.data === 'string' ? event.data : new Uint8Array(event.data));
      upstreamSocket.onclose = event => socket.close(closeCode(event.code), event.reason);
      upstreamSocket.onerror = () => socket.close(1011, 'Upstream unavailable');
    },
    message(socket, message) {
      const upstreamSocket = socket.data.upstream;
      if (upstreamSocket?.readyState === WebSocket.OPEN) upstreamSocket.send(message);
      else if (socket.data.queue.length < 64) socket.data.queue.push(typeof message === 'string' ? message : new Uint8Array(message));
      else socket.close(1013, 'Upstream not ready');
    },
    close(socket) {
      socket.data.upstream?.close();
    },
  },
  async fetch(request, server) {
    const url = new URL(request.url);
    if (request.headers.get('upgrade')?.toLowerCase() === 'websocket' && REALTIME_SOCKET.test(url.pathname)) {
      const target = upstream.replace(/^http:/, 'ws:') + url.pathname + url.search;
      if (server.upgrade(request, {data: {target, queue: []}})) {
        console.log('WEBSOCKET ' + url.pathname);
        return undefined;
      }
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
    try {
      const read = await readBoundedBody(request, options.maxBody);
      exceeded = read.exceeded;
      if (read.tooLarge) return tooLarge();
      const body = read.body;
      const target = new URL(upstream + url.pathname + url.search);
      const forwarded = await fetch(target, {
        method: request.method,
        // Hop by hop and framing headers are never forwarded, and the client's own
        // forwarding headers are replaced by ours: a spoofed Host or X-Forwarded-*
        // must not reach the console.
        headers: {
          ...Object.fromEntries([...request.headers].filter(([name]) => !STRIP_HEADERS.has(name.toLowerCase()))),
          ...headers,
        },
        body,
        redirect: 'manual',
        ...(body instanceof ReadableStream ? {duplex: 'half'} : {}),
      } as RequestInit);
      const output = new Headers(forwarded.headers);
      // The body is re-framed, so the upstream's framing headers go too.
      for (const name of ['content-length', 'transfer-encoding']) output.delete(name);
      for (const [name, value] of Object.entries(headers)) output.set(name, value);
      response = new Response(forwarded.body, {status: forwarded.status, headers: output});
    } catch (error) {
      if (exceeded?.()) return tooLarge();
      response = new Response('Upstream unavailable', {status: 502, headers});
    }
    console.log(request.method + ' ' + url.pathname + ' ' + String(response.status));
    return response;
  },
});

const redirect = Bun.serve({
  port: options.httpPort,
  hostname: '127.0.0.1',
  fetch(request) {
    const url = new URL(request.url);
    // Only the configured public host is used: an attacker supplied Host header
    // must never become the redirect target.
    console.log('REDIRECT ' + url.pathname);
    return new Response(null, {status: 308, headers: {location: 'https://' + options.publicHost + url.pathname + url.search}});
  },
});

console.log('TLS termination: https://127.0.0.1:' + secure.port + ' -> ' + upstream);
console.log('Plain HTTP on 127.0.0.1:' + redirect.port + ' redirects to HTTPS');

const stop = () => {
  secure.stop(true);
  redirect.stop(true);
  process.exit(0);
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);