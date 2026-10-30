Deno.serve(() => Response.json({ hello: 'world', secret: Deno.env.get('MY_SECRET') ?? null }));
