# Getting Started

A practical guide to your first monitor. We'll set up ErrorEngine to monitor itself — detecting queries that haven't run in 24 hours.

## 1. Create a Database Connection

Go to **Connessioni DB** → **Nuova Connessione** (DB Connections → New Connection)

| Field | Value |
|-------|-------|
| Nome | ErrorEngine SQLite |
| Tipo | SQLite |
| Path | `C:\path\to\ErrorEngine\instance\errorengine.db` |

Use the **absolute path** to the database file. Click **Testa Connessione** (Test Connection) to verify, then save.

## 2. Create a Query

Go to **Consultazioni** → **Nuova Consultazione** (Queries → New Query)

| Field | Value |
|-------|-------|
| Nome | Monitor Scheduler |
| Connessione | ErrorEngine SQLite |
| Intervallo | 720 minutes (12 hours) |

**SQL Query:**
```sql
SELECT id, name, last_check_at, total_errors_found 
FROM monitored_queries 
WHERE is_active = 1 
  AND last_check_at < datetime('now', '-24 hours')
```

This returns active queries that haven't run in the last 24 hours.

**Key Fields:** `id`

Key fields identify unique errors. If the same `id` appears in consecutive runs, it's treated as the same error (not a new one).

## 3. Test

Click **Testa Query** (Test Query). If your scheduler is working correctly, you should see 0 rows. If queries are stuck, they'll appear here.

## 4. Save and Enable

Add your email in the recipients field (or leave empty for now). Save the query. It will run automatically based on your schedule.

---

## Notes

### Absolute Paths for SQLite

SQLite requires absolute paths:
- Windows: `C:\Projects\ErrorEngine\instance\errorengine.db`
- Linux: `/home/user/ErrorEngine/instance/errorengine.db`

### Schema.Table for Oracle

Oracle queries require the schema prefix:

```sql
SELECT * FROM MYSCHEMA.ORDERS WHERE STATUS = 'ERROR'
```

### Choosing Key Fields

Pick columns that uniquely identify each error:

| Error Type | Key Fields |
|------------|------------|
| Order errors | `ORDER_ID` |
| Log entries | `LOG_ID` or `TIMESTAMP, MESSAGE` |
| Inventory issues | `WAREHOUSE, PRODUCT_CODE` |

If two rows have the same key field values, ErrorEngine treats them as the same error.

### What Makes a Good Monitor Query

- Returns rows only when there's a problem
- Each row = one distinct error
- Includes enough columns for context in notifications
- Uses `WHERE` to filter to actionable issues only
