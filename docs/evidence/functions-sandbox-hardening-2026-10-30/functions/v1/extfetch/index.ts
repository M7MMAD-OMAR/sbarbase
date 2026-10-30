// (c) outbound HTTPS.
Deno.serve(async () => {
  const response = await fetch('https://example.com', { signal: AbortSignal.timeout(8000) });
  return Response.json({ status: response.status, length: (await response.text()).length });
});
