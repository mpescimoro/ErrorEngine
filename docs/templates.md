# Templates

## Email Templates

Place HTML files in `templates/email/`. Configure per-query in the email template field:

| Field Value | Uses |
|-------------|------|
| *(empty)* | `default.html` |
| `{custom}` | `custom.html` |
| `<html>...` | Inline HTML |

### Available Variables

| Variable | Description |
|----------|-------------|
| `query_name` | Query name |
| `query_description` | Query description |
| `check_time` | Execution timestamp |
| `error_count` | Number of errors |
| `errors` | List of error dictionaries |
| `columns` | Column names from query |
| `email_type` | `new_errors` or `reminder` |

### Date Formatting

```jinja
{{ check_time|localtime('%d/%m/%Y %H:%M') }}
```

## Query Templates

Place SQL files in `templates/queries/`. Useful for reusable query patterns across multiple monitors.

Example `templates/queries/oracle_locks.sql`:
```sql
SELECT sid, serial#, username, machine, program
FROM v$session 
WHERE blocking_session IS NOT NULL
```
