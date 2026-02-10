# API Reference

All endpoints return JSON.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server status |
| GET | `/api/queries` | List queries |
| GET | `/api/queries/{id}` | Query details |
| POST | `/api/queries/{id}/run` | Run query now |
| GET | `/api/errors` | List active errors |
| POST | `/api/errors/{id}/resolve` | Mark resolved |
| GET | `/api/stats/overview?days=30` | Error statistics |
| GET | `/api/stats/timeline?days=14` | Daily counts |
| GET | `/api/scheduler/next` | Next scheduled check |
| GET | `/api/connections` | List DB connections |
| POST | `/api/connections/{id}/test` | Test connection |
| GET | `/api/channels` | List notification channels |
| POST | `/api/channels/{id}/test` | Test channel |

## Example

```bash
# Get all active errors
curl http://localhost:5000/api/errors

# Run a query immediately
curl -X POST http://localhost:5000/api/queries/1/run

# Get statistics for last 7 days
curl http://localhost:5000/api/stats/overview?days=7
```
