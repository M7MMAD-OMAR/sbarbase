import { x } from './missing.ts';
Deno.serve(() => new Response(String(x)));
