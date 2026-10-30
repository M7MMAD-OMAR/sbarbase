/** Starting points for a new function. Each is a complete project: files by name, shared files under `../_shared/`. */
export type Template={id:string;label:string;hint:string;verify_jwt:boolean;files:Record<string,string>};

export const TEMPLATES:Template[]=[
 {id:'hello',label:'Hello world',hint:'Answers with a greeting. Needs a valid key.',verify_jwt:true,files:{'index.ts':`Deno.serve(async (request) => {
  const { name } = await request.json().catch(() => ({}))
  return Response.json({ message: \`Hello \${name ?? 'world'}\` })
})
`}},
 {id:'webhook',label:'Webhook',hint:'Checks a shared secret in a header. Callable without a key.',verify_jwt:false,files:{'index.ts':`// Set WEBHOOK_SECRET on the Secrets page, and send the same value in the x-webhook-secret header.
Deno.serve(async (request) => {
  if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 })
  const expected = Deno.env.get('WEBHOOK_SECRET')
  if (!expected || request.headers.get('x-webhook-secret') !== expected) {
    return new Response('Unauthorized', { status: 401 })
  }
  const event = await request.json().catch(() => null)
  console.log('webhook received', event?.type ?? 'unknown')
  return Response.json({ received: true })
})
`}},
 {id:'supabase-js',label:'Read with supabase-js',hint:'Calls this environment with the caller\'s own token, so row level security applies.',verify_jwt:true,files:{'index.ts':`import { createClient } from 'jsr:@supabase/supabase-js@2'

Deno.serve(async (request) => {
  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
    { global: { headers: { Authorization: request.headers.get('Authorization') ?? '' } } },
  )
  const { data, error } = await supabase.from('your_table').select('*').limit(10)
  if (error) return Response.json({ error: error.message }, { status: 400 })
  return Response.json({ data })
})
`}},
 {id:'cors',label:'Browser calls with CORS',hint:'Answers the browser\'s preflight; the headers live in shared code.',verify_jwt:true,files:{'index.ts':`import { corsHeaders } from '../_shared/cors.ts'

Deno.serve(async (request) => {
  if (request.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders })
  const { name } = await request.json().catch(() => ({}))
  return Response.json({ message: \`Hello \${name ?? 'world'}\` }, { headers: corsHeaders })
})
`,'../_shared/cors.ts':`export const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}
`}},
];
