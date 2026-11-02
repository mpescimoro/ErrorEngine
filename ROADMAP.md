# Roadmap

Planned work, in order. Released versions are in [CHANGELOG.md](CHANGELOG.md).

## v2.3 — Email and Teams

- [ ] OAuth 2.0 for Microsoft 365, needed by end of December 2026 (SMTP basic auth turned off by default)
- [ ] SMTP relay without authentication
- [ ] Link to the error page in notifications
- [ ] Webhooks, Teams and Telegram tell reminders from new errors; a stable error id in webhooks
- [ ] Test email from the query form
- [ ] One SMTP connection per check

## v2.4 — Forms and data

- [ ] Server-side validation and CSRF protection on every form; after a failed save the form keeps what was typed
- [ ] Security headers, `Secure` cookie option, client address behind a reverse proxy, login rate limiting that does not hold a server thread
- [ ] Passwords and tokens encrypted at rest, never sent back to the browser
- [ ] API tokens with limited permissions (read-only for monitors); SQL test and cleanup only from a signed-in session
- [ ] Pages refreshing in the background no longer keep the session alive
- [ ] Opening the query form no longer runs the query; Run now and Test do not hold a server thread
- [ ] Saving a query keeps what the form does not show: disabled channels and connection, routing aggregation, HTTP headers
- [ ] Disabled connections stop their queries
- [ ] HTTP sources: switching from database to HTTP, name of the API key header
- [ ] Oracle connections without an explicit port
- [ ] AS/400 (ibm_db) passwords containing `;`
- [ ] Same value types from every driver (numbers, CHAR without trailing spaces)
- [ ] Missing key fields reported instead of merging rows
- [ ] Time windows across daylight saving changes
- [ ] Unknown placeholders in the email subject no longer block sending
- [ ] Monitoring queries read-only on every driver

## v2.5 — Alert handling

- [ ] Acknowledge and snooze errors, maintenance windows
- [ ] Thresholds: notify after N minutes or N rows, resolve after N absences
- [ ] Optional resolution notice, severity per query or rule
- [ ] Silent first run, row limit per notification with CSV attachment
- [ ] A failing query retried with longer and longer waits, not every minute
- [ ] Routing rules for channels too, rule tester in the UI
- [ ] Heartbeat to an external monitor

## Later

- Error history with search, filters and export; Errors list and logs split into pages, statistics computed in the database
- Configuration export/import, backup of the internal database
- Users, roles and audit log
- HTTP sources: Basic and OAuth2 auth, API key in the query string, request body, pagination, proxy
- PostgreSQL behind PgBouncer (query timeout without startup options)
- More channels: Slack, Google Chat, ntfy; signed webhooks
- Docker image
- Database migrations with Alembic; a key-field hash that keeps values containing `|` apart
- More tests for HTTP sources, channels and web pages
