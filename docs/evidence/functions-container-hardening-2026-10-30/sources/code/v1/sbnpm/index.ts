import { createClient } from 'npm:@supabase/supabase-js@2';
Deno.serve(() => {
  const c = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_ANON_KEY')!);
  return Response.json({ client: typeof c.from, auth: typeof c.auth.getUser });
});
