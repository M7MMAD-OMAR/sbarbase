[العربية](realtime.ar.md)

# Realtime

Realtime lets clients listen to database changes, and send broadcast and presence messages to each other, with `supabase.channel()`, as on Supabase. Each environment turns it on for itself.

## Turn it on

1. Open the environment in the console, then **Services**.
2. Open **Realtime** in the side list and turn its switch on. It shows **Starting…**, then **On**, usually within a minute. The [Services page](services.md) shows what it holds in memory and when it was last used.

Every member can read the page; only owners and admins can switch Realtime. If the server does not have the memory or the database connections for another Realtime, the section says so and nothing changes; see [choosing a server](choosing-a-server.md).

## Modes: Off, On, On demand

Realtime is **Off** for a new environment. **On** runs it all the time. **On demand** (only where an installation operator has allowed it, see [Services](services.md)) starts it on the first connection and stops it after a quiet period with no open connection. It is not the default for Realtime because its start measured 12 to 15 seconds on the machine it was tried on, longer than supabase-js waits for a channel to join; an app that needs live updates the moment a person opens it should keep Realtime **On**. A sleeping Realtime never keeps a replication slot, so it holds no WAL. [Why was my first request slow?](services.md) explains the delay.

## Use it

Nothing changes in your application: point supabase-js at the environment's address with its publishable key, as for everything else.

```js
const channel = supabase.channel('room-one')
  .on('broadcast', { event: 'hello' }, (message) => console.log(message.payload))
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'messages' }, (change) => console.log(change.new))
  .on('presence', { event: 'sync' }, () => console.log(channel.presenceState()))
  .subscribe()
```

- **Database changes:** every table in the `public` schema is published, including tables you create later. The `supabase_realtime` publication belongs to the environment's developer login, as it belongs to `postgres` on Supabase, so a migration run through [direct database access](database-access.md) can add tables of its own schemas one by one (`alter publication supabase_realtime add table app.items`). Row level security decides who receives a change: a client gets a row only if it could read it with a `select`.
- **Broadcast and presence:** between clients on the same channel, and from a server through `POST <address>/realtime/v1/api/broadcast`.
- **Private channels:** policies on `realtime.messages` work as on Supabase.

## How it runs

Each environment that turns Realtime on gets its own small Realtime service (the original upstream image, pinned). It uses up to 320 MiB of memory and stops when you turn Realtime off. It signs in to that environment's database only, with its own login.

When Realtime first starts, and after an upgrade to a newer Realtime, its login is given administrator rights for the few seconds Realtime needs to create its own schema, as upstream Realtime expects. The rights are taken away, and every session that had them is closed, before the start finishes.

The socket and the broadcast API go through the same gateway as REST: a wrong or revoked key is refused before anything reaches Realtime. Realtime's own management API is never reachable from outside.

CI turns Realtime on for an environment on a clean machine with every change, has two supabase-js clients exchange a broadcast, share presence and receive a row inserted through REST, checks that a wrong key is refused, and turns it off again ([evidence](../evidence/docker-realtime-checks.json)).

## Limits

- Realtime works through a TLS proxy only if the proxy passes WebSocket connections. The reference proxy (`deploy/console-tls-proxy.ts`) does.
- Changes are published for the `public` schema, and for tables of other schemas only once a migration adds them to the publication.
- Turning Realtime off drops open connections; clients reconnect once it is on again.
