// Stands in for Auth, REST and Storage: answers every path with a small JSON body. The main service's /_sb proxy
// replaces the path with what follows /v1, so /rest/v1/things reaches /things on SB_REST_URL, i.e. this function.
Deno.serve((req: Request) => Response.json({ stub: true, path: new URL(req.url).pathname, method: req.method }));
