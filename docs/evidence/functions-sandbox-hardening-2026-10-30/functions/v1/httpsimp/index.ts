// (a) an https: import from a host that is not in Deno's default import allow list.
import { nanoid } from 'https://unpkg.com/nanoid@5.0.4/index.browser.js';
Deno.serve(() => Response.json({ id_length: nanoid().length }));
