Deno.serve(()=>{console.log('hello from talk');console.error('error line from talk');return new Response('talk');});
