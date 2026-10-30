// Holds a 130 MiB buffer for 3 s (the worker limit is 150 MiB), so concurrent calls overlap.
Deno.serve(async () => {
  const a = new Uint8Array(130 * 1024 * 1024);
  a.fill(7);
  await new Promise((r) => setTimeout(r, 3000));
  return Response.json({ bytes: a.length });
});
