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

const MAX_BODY_DEFAULT = 1024 * 1024;
const HOP_BY_HOP = ['host', 'connection', 'upgrade', 'keep-alive', 'te', 'trailer', 'transfer-encoding', 'proxy-authorization', 'proxy-authenticate'];
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

function securityHeaders(request: Request, publicHost: string): HeadersInit {
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
assertRegular(options.cert, 'certificate');
assertPrivate(options.key, 'key');
assertLoopbackUpstream(options.upstream);
const upstream = await resolveUpstream(options.upstream);

const secure = Bun.serve({
  port: options.httpsPort,
  hostname: '127.0.0.1',
  tls: {cert: Bun.file(options.cert), key: Bun.file(options.key)},
  async fetch(request) {
    const url = new URL(request.url);
    const headers = securityHeaders(request, options.publicHost);
    let status = 502;
    const declared = Number(request.headers.get('content-length') ?? '0');
    if (!['GET', 'HEAD'].includes(request.method) && declared > options.maxBody) {
      console.log(request.method + ' ' + url.pathname + ' 413');
      return new Response('Request body too large', {status: 413, headers});
    }
    let response: Response;
    try {
      const body = ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer();
      if (body && body.byteLength > options.maxBody) {
        console.log(request.method + ' ' + url.pathname + ' 413');
        return new Response('Request body too large', {status: 413, headers});
      }
      const target = new URL(upstream + url.pathname + url.search);
      const forwarded = await fetch(target, {
        method: request.method,
        // Hop by hop headers are never forwarded; the client's host must not win
        // over the configured public host.
        headers: {
          ...Object.fromEntries([...request.headers].filter(([name]) => !HOP_BY_HOP.includes(name.toLowerCase()))),
          ...Object.fromEntries(Object.entries(headers)),
        },
        body,
        redirect: 'manual',
      });
      status = forwarded.status;
      const output = new Headers(forwarded.headers);
      for (const [name, value] of Object.entries(headers)) output.set(name, value);
      response = new Response(forwarded.body, {status: forwarded.status, headers: output});
    } catch (error) {
      response = new Response('Upstream unavailable', {status: 502, headers});
    }
    console.log(request.method + ' ' + url.pathname + ' ' + String(status));
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