import ms from 'npm:ms@2.1.3';
import { z } from 'npm:zod@3.23.8';
Deno.serve(() => Response.json({ ms: ms('2 days'), zod: z.string().safeParse('x').success }));
