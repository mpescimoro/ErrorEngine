# API Reference

All endpoints return JSON and need a login: the browser session, or for scripts the token set in `ERRORENGINE_API_TOKEN` (see [Configuration](configuration.md#sign-in)). Only `/api/health` is open, for external monitors.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server status: 200 when working, 503 when the internal database or the scheduler is down. Without login only the status; with login or token also version and counts |
| GET | `/api/queries` | List queries |
| GET | `/api/queries/{id}/status` | Status of a query |
| POST | `/api/queries/{id}/run` | Run a query now |
| GET | `/api/errors` | Active errors (`?query_id=` for one query) |
| POST | `/api/errors/{id}/resolve` | Mark an error as resolved |
| GET | `/api/stats/overview?days=7` | Error statistics |
| GET | `/api/stats/timeline?days=14` | Errors per day, today included |
| GET | `/api/scheduler/next` | Next scheduled check |
| GET | `/api/connections` | Active database connections |
| POST | `/api/connections/{id}/test` | Test a connection |
| GET | `/api/channels` | Active notification channels |
| POST | `/api/channels/{id}/test` | Send a test message |

## Examples

```bash
TOKEN=your-api-token

# Active errors
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/errors

# Run query 1 now
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/queries/1/run

# Statistics of the last 7 days
curl -H "Authorization: Bearer $TOKEN" "http://localhost:5000/api/stats/overview?days=7"

# Health check (no token)
curl http://localhost:5000/api/health
```
