# Getting Started

Your first monitor: ErrorEngine watching itself, to find active queries that have not run for 24 hours. You need ErrorEngine running and the sign-in set up, see the [Quick start](../README.md#quick-start).

## 1. Create a database connection

Go to **DB connections** → **New connection**

| Field | Value |
|-------|-------|
| Connection name | ErrorEngine SQLite |
| Database type | SQLite |
| File path | `C:\path\to\ErrorEngine\instance\errorengine.db` |

Use the **full path** of the file (on Linux, e.g. `/home/user/ErrorEngine/instance/errorengine.db`). Click **Test connection**, then save.

## 2. Create a query

Go to **Queries** → **New query**

| Field | Value |
|-------|-------|
| Query name | Stuck queries |
| Database connection | ErrorEngine SQLite |
| Check interval | 12 hours |

**SQL query:**
```sql
SELECT id, name, last_check_at
FROM monitored_queries
WHERE is_active = 1
  AND last_check_at < datetime('now', '-24 hours')
```

**Key fields:** `id`

Each row is one error. Key fields identify it: if the same `id` comes back at the next run, it is the same error and is not notified again. When the row disappears, the error is resolved.

## 3. Test

Click **Test query**. If the scheduler works, the query returns no rows.

## 4. Add recipients and save

Enter your email in **Recipients**, or choose a notification channel, then save. The query runs by itself at every interval; **Run now** runs it immediately.

---

## Tips

### Choosing key fields

Pick the columns that identify each problem:

| Errors | Key fields |
|--------|------------|
| Orders | `ORDER_ID` |
| Log entries | `LOG_ID`, or `TIMESTAMP, MESSAGE` |
| Stock | `WAREHOUSE, PRODUCT_CODE` |

Rows with the same values in the key fields count as the same error.

### A good monitor query

- Returns rows only when there is a problem, one row per problem
- Has the columns that help whoever gets the notification
- Reads data only: use a read-only database user

### Oracle

Prefix tables with the schema: `SELECT * FROM MYSCHEMA.ORDERS WHERE STATUS = 'ERROR'`.
