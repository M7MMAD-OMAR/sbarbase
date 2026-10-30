// Writes 40 MiB to /tmp (under the 64 MiB per worker quota) and keeps the file; eight copies of this function, each
// its own worker, show whether the quota is per worker or per container.
Deno.serve(async () => {
  const chunk = new Uint8Array(1024 * 1024);
  const file = await Deno.open('/tmp/fill', { write: true, create: true });
  try { for (let i = 0; i < 40; i++) await file.write(chunk); } finally { file.close(); }
  return Response.json({ written_mib: 40 });
});
