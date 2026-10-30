// (a) jsr: import of supabase-js, used against SUPABASE_URL (the main service's /_sb proxy to the stub REST).
import { createClient } from 'jsr:@supabase/supabase-js@2';
Deno.serve(async () => {
  const client = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!);
  const { data, error } = await client.from('things').select('*');
  return Response.json({ createClient: typeof createClient, data, error: error?.message ?? null });
});
