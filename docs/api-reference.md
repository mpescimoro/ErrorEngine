# API Reference

All endpoints return JSON and need a login: the browser session, or for scripts the token set in `ERRORENGINE_API_TOKEN` (see [Configuration](configuration.md#sign-in)). Only `/api/health` is open, for external monitors. A failure is JSON too, with its HTTP status: `{"error": "not_found", "message": "..."}`.

With the token, scripts can read and send the POST requests in the table below, which run and test what is saved and resolve errors. The other requests that change something are made by the pages, to change the configuration, try SQL or a server typed in a form, send test emails or clean up: they need the browser session, and the token gets 403.

With the browser session, the requests that change something also need the CSRF token of the page, in the `X-CSRF-Token` header: the pages of ErrorEngine send it. With the API token it is not needed.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server status: 200 when working (`busy` when a query has waited more than a minute for a free thread), 503 when the internal database or the scheduler is down. Without login only the status; with login or token also version and counts |
| GET | `/api/queries` | List queries |
| GET | `/api/queries/{id}/status` | Status of a query |
| POST | `/api/queries/{id}/run` | Run a query now |
| POST | `/api/queries/{id}/test` | Read the source of a query without recording errors or sending notifications: columns, row count and the first 5 rows |
| POST | `/api/queries/{id}/routing/test` | Recipients the routing rules choose for the active errors of the query (up to 10), or for the ones in the body: `{"errors": [{"FIELD": "value"}]}` |
| GET | `/api/errors` | Active errors (`?query_id=` for one query) |
| POST | `/api/errors/{id}/resolve` | Mark an error as resolved; one already resolved keeps its time |
| GET | `/api/stats/overview?days=7` | Error statistics (`&query_id=` for one query); `days` from 1 to 365 |
| GET | `/api/stats/timeline?days=14` | Errors per day, today included (`&query_id=` for one query); `days` from 1 to 365 |
| GET | `/api/scheduler/next` | Next scheduled check |
| GET | `/api/connections` | Active database connections |
| POST | `/api/connections/{id}/test` | Test a connection, as saved |
| GET | `/api/channels` | Active notification channels |
| POST | `/api/channels/{id}/test` | Send a test message |

## Examples

```bash
TOKEN=your-api-token

# Active errors
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/errors

# Run query 1 now
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/queries/1/run

# What the source of query 1 returns now
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/queries/1/test

# Who the routing rules of query 1 notify for a critical error
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"errors": [{"SEVERITY": "CRITICAL"}]}' http://localhost:5000/api/queries/1/routing/test

# Statistics of the last 7 days
curl -H "Authorization: Bearer $TOKEN" "http://localhost:5000/api/stats/overview?days=7"

# Health check (no token)
curl http://localhost:5000/api/health
```
