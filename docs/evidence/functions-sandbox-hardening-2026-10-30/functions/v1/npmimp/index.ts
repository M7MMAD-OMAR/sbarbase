// (a) npm: import that serves a response.
import { Hono } from 'npm:hono@4';
const app = new Hono();
app.get('/*', (c) => c.json({ hono: true, path: c.req.path }));
Deno.serve(app.fetch);
