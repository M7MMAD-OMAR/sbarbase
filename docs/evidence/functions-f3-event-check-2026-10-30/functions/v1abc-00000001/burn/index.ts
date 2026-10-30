Deno.serve(()=>{let x=0;for(let i=0;i<40_000_000;i++)x+=i%7;return new Response('burn '+x);});
