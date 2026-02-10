# FAQ

## General

### How does error detection work?

ErrorEngine runs your query periodically. Each row returned represents an active error. When a row disappears from results, the error is automatically marked as resolved.

### Is it real-time?

No. ErrorEngine is polling-based. Minimum interval is 1 minute.

### Can I monitor multiple databases at once?

Yes. Create multiple connections and assign each query to its own connection.

---

## Setup

### Why Oracle thin mode?

No Oracle Client installation needed. Just `pip install oracledb` and connect directly. This makes deployment much simpler.

### What's the difference between start.sh and manual installation?

The start scripts (`start.sh` / `start.bat`) automatically:
- Create a virtual environment
- Install dependencies
- Start the server

Manual installation gives you more control but requires more steps.

---

## Troubleshooting

### Emails not sending?

1. Check SMTP credentials in `.env`
2. Office 365 may require app passwords if MFA is enabled
3. Check the **Log** page for error messages
4. Try the test email feature in **Impostazioni** (Settings)

### Times are wrong?

Set `TIMEZONE=Europe/Rome` (or your timezone) in `.env` and restart.

### Query runs but no errors detected?

1. Click **Testa Query** (Test Query) to verify the query returns rows
2. Check that **key fields** are set correctly
3. Verify the query is active and within its schedule window

### Duplicate notifications?

This was a bug in versions before v2.1. Update to the latest version which includes atomic locking to prevent concurrent executions.

---

## Data

### How do I back up?

Copy `instance/errorengine.db`. This SQLite file contains all configuration and error history.

### Where are the logs?

Query execution logs and email logs are viewable in the web UI under **Log**. They're stored in the SQLite database.

### Can I export error data?

Use the API endpoints to fetch data programmatically:
```bash
curl http://localhost:5000/api/errors
```

---

## Security

### Should I expose this to the internet?

Not recommended without protection. Use a VPN or reverse proxy with authentication. ErrorEngine doesn't have built-in user authentication.

### Are database passwords encrypted?

Currently stored in plain text in SQLite. For production, protect the server and database file.

### What permissions does the DB user need?

Read-only access is sufficient and recommended. ErrorEngine only runs SELECT queries.
