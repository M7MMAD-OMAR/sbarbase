[العربية](logs-and-metrics.ar.md)

# Logs and metrics

Each environment's page in the console shows how it is used and what its services said, so you can see a failing request without logging in to the server.

## Usage

Every member of the client sees **Usage** on the environment's page:

- **Requests** through the API in the last hour, and how many were client errors (4xx) or server errors (5xx).
- **Response time**, the median and the 95th percentile, measured at the gateway.
- A chart of requests per minute, with errors in red, and the count per service (Auth, REST, Storage, Realtime).
- **Memory and processor** use of the environment's own Auth, REST, Realtime and Edge Functions services right now.

The gateway counts requests in memory. A restart of Sbarbase starts the counts again, and nothing older than an hour is kept.

## Logs

Owners and admins also see **Logs**. Choose a source:

- **Requests:** the last 500 requests through the API: time, method, service, path, status and time taken. The query string is never kept, so filters, tokens and keys in it are never shown.
- **Auth, REST, Storage, Realtime, Edge Functions:** the last 200 lines the original service wrote, as it wrote them. Storage is shared by every environment, so only lines naming this environment are shown.

**Errors only** keeps failed requests, or lines that look like errors or warnings.

Before a line leaves the server, keys, tokens, passwords, signed JWTs, OAuth codes and database passwords are replaced with `[redacted]`. Service logs can still name users' email addresses and your tables, which is why viewers do not see them.

CI uses an environment through supabase-js on a clean machine with every change, then reads its usage and every log source and checks that no key or token appears in any answer ([evidence](../evidence/docker-observe-checks.json)).

## From a script

The same data is available through the management API with an operator token:

```bash
curl -H "Authorization: Bearer $TOKEN" "$CONSOLE/management/v1/environments/$ID/metrics"
curl -H "Authorization: Bearer $TOKEN" "$CONSOLE/management/v1/environments/$ID/logs?source=auth&lines=100&errors=1"
```

See the [API reference](../reference/api.md) for the fields.

## Limits

- Request totals and the request log live in memory and start empty after a restart. For longer history, keep `docker logs` output with your own log collector.
- The PostgreSQL log is not shown: the database server is shared by every environment.
- There are no alerts on error rates yet. The notice for an environment that keeps hitting its request limit ([operator notifications](../reference/configuration.md#operator-notifications)) is the only alert.
