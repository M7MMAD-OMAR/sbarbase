Deno.serve(async () => { await new Promise(r => setTimeout(r, 4000)); return Response.json({slow: 1}); });
