// (b) a secret passed through secrets.json and SUPABASE_URL; the container's own variables must not show.
Deno.serve(() => Response.json({
  MY_SECRET: Deno.env.get('MY_SECRET') ?? null,
  SUPABASE_EVIL: Deno.env.get('SUPABASE_EVIL') ?? null,
  SUPABASE_URL: Deno.env.get('SUPABASE_URL') ?? null,
  has_anon: (Deno.env.get('SUPABASE_ANON_KEY') ?? '').length > 0,
  has_service_role: (Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '').length > 0,
  SB_JWT_SECRET: Deno.env.get('SB_JWT_SECRET') ?? null,
  SB_AUTH_URL: Deno.env.get('SB_AUTH_URL') ?? null,
  all_keys: Object.keys(Deno.env.toObject()).sort(),
}));
