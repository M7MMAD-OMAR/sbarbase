// The TLS proxy rides over a short console restart: a refused connection is retried within the
// window, anything that may have reached the console is not.
import {afterAll, beforeAll, expect, test} from 'bun:test';
import {generateKeyPairSync, randomBytes, sign} from 'node:crypto';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import net from 'node:net';
import type {Server, Subprocess} from 'bun';

const PROXY = join(import.meta.dir, '..', 'deploy', 'console-tls-proxy.ts');
const insecure = {tls: {rejectUnauthorized: false}} as RequestInit;
let directory = '';
let cert = '';
let key = '';

// A self-signed P-256 certificate built here, so the test needs no openssl binary.
const der = (tag: number, ...parts: Buffer[]) => {
  const body = Buffer.concat(parts);
  const length = body.length < 128 ? Buffer.from([body.length])
    : body.length < 256 ? Buffer.from([0x81, body.length]) : Buffer.from([0x82, body.length >> 8, body.length & 255]);
  return Buffer.concat([Buffer.from([tag]), length, body]);
};
const utcTime = (date: Date) => der(0x17, Buffer.from(date.toISOString().replace(/[-:T]/g, '').slice(2, 14) + 'Z'));

function selfSigned(): {cert: string; key: string} {
  const pair = generateKeyPairSync('ec', {namedCurve: 'P-256'});
  const algorithm = der(0x30, der(0x06, Buffer.from('2a8648ce3d040302', 'hex')));
  const name = der(0x30, der(0x31, der(0x30, der(0x06, Buffer.from('550403', 'hex')), der(0x0c, Buffer.from('127.0.0.1')))));
  const now = Date.now();
  const tbs = der(0x30,
    der(0xa0, der(0x02, Buffer.from([2]))),
    der(0x02, Buffer.concat([Buffer.from([1]), randomBytes(8)])),
    algorithm, name,
    der(0x30, utcTime(new Date(now - 3600_000)), utcTime(new Date(now + 86_400_000))),
    name,
    pair.publicKey.export({type: 'spki', format: 'der'}));
  const signature = sign('sha256', tbs, pair.privateKey);
  const body = der(0x30, tbs, algorithm, der(0x03, Buffer.concat([Buffer.from([0]), signature])));
  const pem = '-----BEGIN CERTIFICATE-----\n' + body.toString('base64').replace(/.{64}/g, '$&\n').replace(/\n?$/, '\n') + '-----END CERTIFICATE-----\n';
  return {cert: pem, key: String(pair.privateKey.export({type: 'pkcs8', format: 'pem'}))};
}

beforeAll(() => {
  directory = mkdtempSync(join(tmpdir(), 'proxy-retry-'));
  cert = join(directory, 'cert.pem');
  key = join(directory, 'key.pem');
  const made = selfSigned();
  writeFileSync(cert, made.cert, {mode: 0o644});
  writeFileSync(key, made.key, {mode: 0o600});
});
afterAll(() => rmSync(directory, {recursive: true, force: true}));

/** A loopback port nothing listens on, bound again later. Another process could take it in
 * between; the window is short and the upstream binds it again within a second. */
function closedPort(): number {
  const probe = Bun.serve({port: 0, hostname: '127.0.0.1', fetch: () => new Response()});
  const port = probe.port as number;
  probe.stop(true);
  return port;
}

type Proxy = {base: string; process: Subprocess; log: string[]};

function proxyCommand(upstreamPort: number, extra: string[]): string[] {
  return ['bun', PROXY, '--cert', cert, '--key', key, '--public-host', '127.0.0.1',
    '--https-port', '0', '--http-port', '0', '--upstream', 'http://127.0.0.1:' + upstreamPort, ...extra];
}

async function startProxy(upstreamPort: number, window: number | null, env: Record<string, string> = {}): Promise<Proxy> {
  const child = Bun.spawn(proxyCommand(upstreamPort, window === null ? [] : ['--retry-window', String(window)]),
    {stdout: 'pipe', stderr: 'inherit', env: {...process.env, ...env}});
  const log: string[] = [];
  const reader = (child.stdout as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let pending = '';
  let seen: (port: string) => void = () => {};
  const portSeen = new Promise<string>(resolve => { seen = resolve; });
  void (async () => {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) return;
      pending += decoder.decode(value);
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';
      for (const line of lines) {
        log.push(line);
        const match = /https:\/\/127\.0\.0\.1:(\d+)/.exec(line);
        if (match?.[1]) seen(match[1]);
      }
    }
  })();
  const port = await Promise.race([portSeen, Bun.sleep(10_000).then(() => null)]);
  if (port === null) {
    child.kill();
    await child.exited;
    throw new Error('proxy did not start: ' + log.join(' | '));
  }
  return {base: 'https://127.0.0.1:' + port, process: child, log};
}

async function stopProxy(proxy: Proxy) {
  proxy.process.kill();
  await proxy.process.exited;
}

const lines = (proxy: Proxy, prefix: string) => proxy.log.filter(line => line.startsWith(prefix));

/** A raw keep-alive upstream that counts POSTs; `onPost` decides what happens after one arrives. */
function rawUpstream(port: number, onPost: (socket: net.Socket, server: net.Server) => void): Promise<{server: net.Server; posts: () => number}> {
  let posts = 0;
  const server = net.createServer(socket => {
    socket.on('error', () => {});
    let buffer = '';
    socket.on('data', data => {
      buffer += data.toString();
      if (!buffer.includes('\r\n\r\n')) return;
      if (buffer.startsWith('GET')) {
        buffer = '';
        socket.write('HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok');
      } else if (buffer.startsWith('POST')) {
        posts += 1;
        onPost(socket, server);
      }
    });
  });
  return new Promise(resolve => server.listen(port, '127.0.0.1', () => resolve({server, posts: () => posts})));
}

test('a request during a closed port succeeds once the upstream starts within the window', async () => {
  const port = closedPort();
  const proxy = await startProxy(port, 5000);
  let upstream: Server<undefined> | undefined;
  try {
    setTimeout(() => { upstream = Bun.serve({port, hostname: '127.0.0.1', fetch: () => new Response('back')}); }, 600);
    const response = await fetch(proxy.base + '/health', insecure);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe('back');
    await Bun.sleep(50);
    expect(lines(proxy, 'GET /health')).toEqual([expect.stringMatching(/^GET \/health 200 after \d+ retries$/)]);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);

test('a wait past Bun\'s 10 s request timeout (it fires at about 12 s) still succeeds', async () => {
  const port = closedPort();
  const proxy = await startProxy(port, 20_000);
  let upstream: Server<undefined> | undefined;
  try {
    setTimeout(() => { upstream = Bun.serve({port, hostname: '127.0.0.1', fetch: () => new Response('late')}); }, 13_000);
    const started = Date.now();
    const response = await fetch(proxy.base + '/health', insecure);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe('late');
    expect(Date.now() - started).toBeGreaterThan(12_500);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 25_000);

test('a small POST during the gap is delivered exactly once with its bytes', async () => {
  const port = closedPort();
  const proxy = await startProxy(port, 5000);
  const received: string[] = [];
  let upstream: Server<undefined> | undefined;
  try {
    setTimeout(() => {
      upstream = Bun.serve({port, hostname: '127.0.0.1', async fetch(request) {
        received.push(request.method + ' ' + await request.text());
        return new Response('stored', {status: 201});
      }});
    }, 500);
    const response = await fetch(proxy.base + '/rest/v1/items', {...insecure, method: 'POST', body: JSON.stringify({name: 'one'})});
    expect(response.status).toBe(201);
    await Bun.sleep(1500);
    expect(received).toEqual(['POST {"name":"one"}']);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);

test('a stale pooled connection to a stopped upstream is retried and delivered once', async () => {
  const port = closedPort();
  const received: string[] = [];
  const serve = () => Bun.serve({port, hostname: '127.0.0.1', async fetch(request) {
    if (request.method === 'POST') received.push(await request.text());
    return new Response('ok', {status: request.method === 'POST' ? 201 : 200});
  }});
  let upstream: Server<undefined> | undefined = serve();
  const proxy = await startProxy(port, 5000);
  try {
    expect((await fetch(proxy.base + '/health', insecure)).status).toBe(200);
    upstream.stop(true);
    upstream = undefined;
    setTimeout(() => { upstream = serve(); }, 500);
    const response = await fetch(proxy.base + '/rest/v1/items', {...insecure, method: 'POST', body: 'payload'});
    expect(response.status).toBe(201);
    await Bun.sleep(1500);
    expect(received).toEqual(['payload']);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);

test('a pooled POST the upstream received before it went away is not sent again', async () => {
  const port = closedPort();
  let again: {server: net.Server; posts: () => number} | undefined;
  // The upstream reads the POST, closes and stops listening, then is back 100 ms later: a replay would land.
  const first = await rawUpstream(port, (socket, server) => {
    socket.end();
    server.close(() => setTimeout(async () => { again = await rawUpstream(port, inner => inner.end()); }, 100));
  });
  const proxy = await startProxy(port, 5000);
  try {
    expect((await fetch(proxy.base + '/health', insecure)).status).toBe(200);
    const response = await fetch(proxy.base + '/rest/v1/items', {...insecure, method: 'POST', body: 'payload'});
    expect(response.status).toBe(502);
    await Bun.sleep(1500);
    expect(first.posts() + (again?.posts() ?? 0)).toBe(1);
    expect(again).toBeDefined();
  } finally {
    again?.server.close();
    await stopProxy(proxy);
  }
}, 15_000);

test('a gap longer than the window ends in 502', async () => {
  const proxy = await startProxy(closedPort(), 1500);
  try {
    const started = Date.now();
    const response = await fetch(proxy.base + '/health', insecure);
    expect(response.status).toBe(502);
    expect(Date.now() - started).toBeGreaterThanOrEqual(700);
    expect(Date.now() - started).toBeLessThan(3000);
    await Bun.sleep(50);
    expect(lines(proxy, 'GET /health')).toEqual([expect.stringMatching(/^GET \/health 502 after \d+ retries$/)]);
  } finally {
    await stopProxy(proxy);
  }
}, 15_000);

test('an upstream that resets after receiving a POST is not retried', async () => {
  let connections = 0;
  const server = net.createServer(socket => {
    connections += 1;
    socket.once('data', () => socket.resetAndDestroy());
  }).listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  const proxy = await startProxy((server.address() as net.AddressInfo).port, 5000);
  try {
    const response = await fetch(proxy.base + '/rest/v1/items', {...insecure, method: 'POST', body: 'payload'});
    expect(response.status).toBe(502);
    await Bun.sleep(1500);
    expect(connections).toBe(1);
  } finally {
    server.close();
    await stopProxy(proxy);
  }
}, 15_000);

test('a client that goes away stops the retries', async () => {
  const port = closedPort();
  const proxy = await startProxy(port, 10_000);
  let hits = 0;
  let upstream: Server<undefined> | undefined;
  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 250);
    // Retries would land at about 100, 300, 700 and 1500 ms; the upstream is back at 450.
    setTimeout(() => { upstream = Bun.serve({port, hostname: '127.0.0.1', fetch: () => { hits += 1; return new Response('late'); }}); }, 450);
    await expect(fetch(proxy.base + '/health', {...insecure, signal: controller.signal})).rejects.toThrow();
    await Bun.sleep(2500);
    expect(hits).toBe(0);
    expect(lines(proxy, 'GET /health')).toEqual([expect.stringMatching(/^GET \/health 502 client gone/)]);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);

test('an explicit empty --retry-window is refused at startup', () => {
  const result = Bun.spawnSync(proxyCommand(closedPort(), ['--retry-window', '']), {stdout: 'pipe', stderr: 'pipe'});
  expect(result.exitCode).not.toBe(0);
  expect(result.stderr.toString() + result.stdout.toString()).toContain('--retry-window is empty');
  for (const value of ['-1', '60001', '1.5', 'abc']) {
    const bad = Bun.spawnSync(proxyCommand(closedPort(), ['--retry-window', value]), {stdout: 'pipe', stderr: 'pipe'});
    expect(bad.exitCode).not.toBe(0);
  }
}, 15_000);

test('an empty SBARBASE_PROXY_RETRY_MS means the default window', async () => {
  const proxy = await startProxy(closedPort(), null, {SBARBASE_PROXY_RETRY_MS: ''});
  try {
    expect(lines(proxy, 'TLS termination')).toEqual([expect.stringContaining('retried for 15000 ms')]);
  } finally {
    await stopProxy(proxy);
  }
  const set = await startProxy(closedPort(), null, {SBARBASE_PROXY_RETRY_MS: '2500'});
  try {
    expect(lines(set, 'TLS termination')).toEqual([expect.stringContaining('retried for 2500 ms')]);
  } finally {
    await stopProxy(set);
  }
}, 15_000);

const SOCKET_PATH = '/app_one/realtime/v1/websocket';
const openSocket = (proxy: Proxy) => new WebSocket(proxy.base.replace('https:', 'wss:') + SOCKET_PATH, insecure as never);
const closed = (socket: WebSocket) => new Promise<number>(resolve => { socket.onclose = event => resolve(event.code); });

test('a Realtime WebSocket upgrade during the gap connects once the upstream returns', async () => {
  const port = closedPort();
  const proxy = await startProxy(port, 5000);
  let upstream: Server<undefined> | undefined;
  try {
    setTimeout(() => {
      upstream = Bun.serve({port, hostname: '127.0.0.1',
        fetch(request, server) { return server.upgrade(request, {data: undefined}) ? undefined : new Response('no', {status: 400}); },
        websocket: {message(socket, message) { socket.send('echo ' + String(message)); }},
      });
    }, 600);
    const socket = openSocket(proxy);
    const reply = await new Promise<string>((resolve, reject) => {
      socket.onopen = () => socket.send('hello');
      socket.onmessage = event => resolve(String(event.data));
      socket.onclose = event => reject(new Error('closed ' + event.code));
      setTimeout(() => reject(new Error('no reply')), 8000);
    });
    expect(reply).toBe('echo hello');
    socket.close();
    await Bun.sleep(50);
    expect(lines(proxy, 'WEBSOCKET')).toEqual([expect.stringMatching(/^WEBSOCKET \/app_one\/realtime\/v1\/websocket open after \d+ retries$/)]);
  } finally {
    upstream?.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);

test('a WebSocket upstream that answers or resets the handshake is not retried', async () => {
  let rejected = 0;
  const refusing = Bun.serve({port: 0, hostname: '127.0.0.1', fetch: () => { rejected += 1; return new Response('no', {status: 401}); }});
  let resets = 0;
  const resetting = net.createServer(socket => {
    resets += 1;
    socket.on('error', () => {});
    socket.once('data', () => socket.resetAndDestroy());
  }).listen(0, '127.0.0.1');
  await new Promise(resolve => resetting.once('listening', resolve));
  const first = await startProxy(refusing.port as number, 5000);
  const second = await startProxy((resetting.address() as net.AddressInfo).port, 5000);
  try {
    expect(await closed(openSocket(first))).toBe(1011);
    expect(await closed(openSocket(second))).toBe(1011);
    await Bun.sleep(1500);
    expect(rejected).toBe(1);
    expect(resets).toBe(1);
  } finally {
    refusing.stop(true);
    resetting.close();
    await stopProxy(first);
    await stopProxy(second);
  }
}, 15_000);

test('a socket waiting for its upstream may queue at most a MiB', async () => {
  const proxy = await startProxy(closedPort(), 5000);
  try {
    const socket = openSocket(proxy);
    await new Promise(resolve => { socket.onopen = resolve; });
    const code = closed(socket);
    const chunk = new Uint8Array(300 * 1024);
    for (let index = 0; index < 4; index += 1) socket.send(chunk);
    expect(await code).toBe(1013);
  } finally {
    await stopProxy(proxy);
  }
}, 15_000);

test('an invalid SBARBASE_PROXY_RETRY_MS is refused under its own name', () => {
  const result = Bun.spawnSync(proxyCommand(closedPort(), []), {stdout: 'pipe', stderr: 'pipe', env: {...process.env, SBARBASE_PROXY_RETRY_MS: 'abc'}});
  expect(result.exitCode).not.toBe(0);
  expect(result.stderr.toString() + result.stdout.toString()).toContain('SBARBASE_PROXY_RETRY_MS must be');
}, 15_000);

test('beyond 256 requests waiting for the upstream, the rest get 502 at once', async () => {
  const proxy = await startProxy(closedPort(), 2000);
  try {
    // A separate client, so Bun's own 256 request limit on fetch does not queue the extra ones.
    const script = 'const started = Date.now(); const all = await Promise.all(Array.from({length: 300}, () => fetch('
      + JSON.stringify(proxy.base + '/health') + ', {tls: {rejectUnauthorized: false}}).then(async r => { await r.text(); return [r.status, Date.now() - started]; })));'
      + ' console.log(JSON.stringify(all));';
    const client = Bun.spawn(['bun', '-e', script], {stdout: 'pipe', stderr: 'inherit', env: {...process.env, BUN_CONFIG_MAX_HTTP_REQUESTS: '1024'}});
    const results = JSON.parse(await new Response(client.stdout).text()) as [number, number][];
    await client.exited;
    expect(results.every(([status]) => status === 502)).toBe(true);
    expect(results.filter(([, elapsed]) => elapsed < 1000)).toHaveLength(44);
    expect(results.filter(([, elapsed]) => elapsed >= 1500)).toHaveLength(256);
    await Bun.sleep(50);
    expect(lines(proxy, 'GET /health 502').filter(line => !line.includes('retries'))).toHaveLength(44);
  } finally {
    await stopProxy(proxy);
  }
}, 20_000);

test('beyond 64 sockets waiting for the upstream, the rest close 1011 at once', async () => {
  const proxy = await startProxy(closedPort(), 2000);
  try {
    const started = Date.now();
    const sockets = Array.from({length: 70}, () => openSocket(proxy));
    const ends = await Promise.all(sockets.map(socket => closed(socket).then(code => [code, Date.now() - started])));
    expect(ends.every(([code]) => code === 1011)).toBe(true);
    expect(ends.filter(([, elapsed]) => elapsed! < 1000)).toHaveLength(6);
    expect(ends.filter(([, elapsed]) => elapsed! >= 1500)).toHaveLength(64);
  } finally {
    await stopProxy(proxy);
  }
}, 20_000);

test('a Realtime message above 4 MiB drops the socket and never reaches the upstream', async () => {
  const received: number[] = [];
  let upstreamClosed = false;
  const upstream = Bun.serve({port: 0, hostname: '127.0.0.1',
    fetch(request, server) { return server.upgrade(request, {data: undefined}) ? undefined : new Response('no', {status: 400}); },
    websocket: {
      message(_socket, message) { received.push(typeof message === 'string' ? message.length : message.byteLength); },
      close() { upstreamClosed = true; },
    },
  });
  const proxy = await startProxy(upstream.port as number, 2000);
  try {
    const socket = openSocket(proxy);
    await new Promise(resolve => { socket.onopen = resolve; });
    await Bun.sleep(200);
    const code = closed(socket);
    socket.send(new Uint8Array(4 * 1024 * 1024));
    for (let tries = 0; received.length === 0 && tries < 50; tries += 1) await Bun.sleep(50);
    expect(received).toEqual([4 * 1024 * 1024]);
    socket.send(new Uint8Array(4 * 1024 * 1024 + 1));
    // Bun drops the connection without a close frame ('Received too big message'), so the client sees 1006, not 1009.
    expect(await code).toBe(1006);
    for (let tries = 0; !upstreamClosed && tries < 50; tries += 1) await Bun.sleep(50);
    expect(upstreamClosed).toBe(true);
    expect(received).toEqual([4 * 1024 * 1024]);
  } finally {
    upstream.stop(true);
    await stopProxy(proxy);
  }
}, 15_000);
