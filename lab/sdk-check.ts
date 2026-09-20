import { createClient } from '@supabase/supabase-js';

const endpoints = await Bun.file('.lab/endpoints.json').json() as Record<string, {auth:string;rest:string}>;
// Test-only local router. Production routing/key validation is a separate gate.
const router = Bun.serve({
  hostname: '127.0.0.1', port: 0,
  async fetch(req) {
    const url = new URL(req.url);
    const match = url.pathname.match(/^\/(a_prod|a_stage|b_prod)\/(auth|rest)\/v1(\/.*)?$/);
    if (!match) return new Response('Not found', {status:404});
    const [, env, service, path] = match;
    const target = endpoints[env!]?.[service as 'auth'|'rest'];
    if (!target) return new Response('Not found', {status:404});
    const headers = new Headers(req.headers);
    headers.delete('host');
    return fetch(target+(path || '/')+url.search, {
      method:req.method, headers,
      body:req.method === 'GET' || req.method === 'HEAD' ? undefined : await req.arrayBuffer(),
      redirect:'manual',
    });
  }
});
const results: {check:string;passed:boolean}[] = [];
function check(name:string, ok:boolean) {
  results.push({check:name,passed:ok});
  if (!ok) throw new Error(name);
}
try {
  const suffix = crypto.randomUUID();
  for (const env of Object.keys(endpoints)) {
    const client = createClient(`http://127.0.0.1:${router.port}/${env}`, 'test-router-key', {
      auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}
    });
    const {data:signup,error:signupError} = await client.auth.signUp({email:`sdk-${suffix}@example.com`,password:`Local-${suffix}`});
    check(`${env} SDK signup`,!signupError && !!signup.session);
    const {data:refreshed,error:refreshError} = await client.auth.refreshSession();
    check(`${env} SDK refresh`,!refreshError && !!refreshed.session);
    const {data:insert,error:insertError} = await client.from('lab_items').insert({owner_id:signup.user!.id,value:suffix}).select().single();
    check(`${env} SDK insert/select`,!insertError && insert?.value===suffix);
    const {error:updateError} = await client.from('lab_items').update({value:suffix+'-updated'}).eq('id',insert.id);
    check(`${env} SDK update`,!updateError);
    const {data:items,error:readError} = await client.from('lab_items').select().eq('id',insert.id);
    check(`${env} SDK read`,!readError && items?.length===1 && items[0].value===suffix+'-updated');
    const {error:deleteError} = await client.from('lab_items').delete().eq('id',insert.id);
    const {data:after} = await client.from('lab_items').select().eq('id',insert.id);
    check(`${env} SDK delete`,!deleteError && after?.length===0);
    const {error:logoutError}=await client.auth.signOut();
    check(`${env} SDK logout`,!logoutError);
  }
  console.log(`${results.length} Supabase SDK checks passed through test-only router.`);
} finally {
  await Bun.write('.lab/sdk-verification.json',JSON.stringify({sdk:'2.116.0',scope:'test router, no API key gateway validation',checks:results},null,2));
  router.stop(true);
}
