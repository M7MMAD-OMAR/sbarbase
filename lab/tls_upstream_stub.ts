// Stub console upstream for the TLS termination check.
//
// Serves the real built console page and its assets through the same static layer
// the console uses, answers one management-like route, and echoes the forwarded
// protocol header. It binds an ephemeral loopback port and prints it as JSON so the
// check can point the proxy at it. No container, no secret, no fixed port.

import {uiStatic} from './ui-static';

const server = Bun.serve({
  hostname: '127.0.0.1',
  port: 0,
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === '/management/auth/v1/settings') {
      return new Response(JSON.stringify({marker: 'management-reachable-through-tls'}), {
        headers: {'content-type': 'application/json'},
      });
    }
    if (url.pathname === '/echo-forwarded') {
      return new Response(request.headers.get('x-forwarded-proto') ?? 'none', {
        headers: {'content-type': 'text/plain'},
      });
    }
    if (url.pathname === '/echo-host') {
      return new Response(request.headers.get('x-forwarded-host') ?? 'none', {
        headers: {'content-type': 'text/plain'},
      });
    }
    return (await uiStatic(request)) ?? new Response('not found', {status: 404, headers: {'content-type': 'text/plain'}});
  },
});

console.log(JSON.stringify({port: server.port}));