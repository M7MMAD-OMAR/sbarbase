// (d) SUPABASE_URL through the main service's /_sb proxy: loopback to the main service, then the upstream.
Deno.serve(async () => {
  const base = Deno.env.get('SUPABASE_URL')!;
  const out: Record<string, string> = {};
  for (const path of ['/rest/v1/things', '/auth/v1/health', '/storage/v1/bucket']) {
    try {
      const r = await fetch(base + path, { headers: { apikey: Deno.env.get('SUPABASE_ANON_KEY')! }, signal: AbortSignal.timeout(5000) });
      out[path] = 'HTTP ' + r.status + ' ' + (await r.text()).slice(0, 100);
    } catch (e) { out[path] = 'ERROR ' + String(e).slice(0, 160); }
  }
  return Response.json(out);
});
