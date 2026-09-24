[العربية](realtime.ar.md)

# Realtime

Realtime lets clients listen to database changes, and send broadcast and presence messages to each other, with `supabase.channel()`, as on Supabase. Each environment turns it on for itself.

## Turn it on

1. Open the environment's page in the console.
2. Under **Realtime**, press **Turn on Realtime**. It shows **Applying…**, then **On**, usually within a minute.

Only owners and admins of the client see this section. If the server does not have the memory or the database connections for another Realtime, the section says so and nothing changes; see [choosing a server](choosing-a-server.md).

## Use it

Nothing changes in your application: point supabase-js at the environment's address with its publishable key, as for everything else.

```js
const channel = supabase.channel('room-one')
  .on('broadcast', { event: 'hello' }, (message) => console.log(message.payload))
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'messages' }, (change) => console.log(change.new))
  .on('presence', { event: 'sync' }, () => console.log(channel.presenceState()))
  .subscribe()
```

- **Database changes:** every table in the `public` schema is published, including tables you create later. Row level security decides who receives a change: a client gets a row only if it could read it with a `select`.
- **Broadcast and presence:** between clients on the same channel, and from a server through `POST <address>/realtime/v1/api/broadcast`.
- **Private channels:** policies on `realtime.messages` work as on Supabase.

## How it runs

Each environment that turns Realtime on gets its own small Realtime service (the original upstream image, pinned). It uses up to 320 MiB of memory and stops when you turn Realtime off. It signs in to that environment's database only, with its own login.

When Realtime first starts, and after an upgrade to a newer Realtime, its login is given administrator rights for the few seconds Realtime needs to create its own schema, as upstream Realtime expects. The rights are taken away, and every session that had them is closed, before the start finishes.

The socket and the broadcast API go through the same gateway as REST: a wrong or revoked key is refused before anything reaches Realtime. Realtime's own management API is never reachable from outside.

## Limits

- Realtime works through a TLS proxy only if the proxy passes WebSocket connections. The reference proxy (`deploy/console-tls-proxy.ts`) does.
- Changes are published for the `public` schema only.
- Turning Realtime off drops open connections; clients reconnect once it is on again.
