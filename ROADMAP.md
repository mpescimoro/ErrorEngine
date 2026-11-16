# Roadmap

Planned work, in order. Released versions are in [CHANGELOG.md](CHANGELOG.md).

## v2.5 — Security

- [ ] Server-side validation and CSRF protection on every form, the `Origin` checked on the requests that change something; after a failed save the form keeps what was typed
- [ ] Security headers, `Secure` cookie option, client address behind a reverse proxy, login rate limiting that does not hold a server thread
- [ ] Passwords and tokens encrypted at rest, never sent back to the browser
- [ ] API tokens with limited permissions (read-only for monitors); SQL test and cleanup only from a signed-in session
- [ ] A minimum length for `SECRET_KEY` and the API token
- [ ] Pages refreshing in the background no longer keep the session alive
- [ ] Monitoring queries read-only on every driver
- [ ] AS/400 (ibm_db) passwords containing `;`

## v2.6 — Data and API

- [ ] Error identity, changed once with a migration that recomputes the open errors: same value types from every driver (numbers, CHAR without trailing spaces), a key-field hash that keeps values containing `|` apart, key fields edited without notifying the open errors again
- [ ] Errors list and logs split into pages, statistics computed in the database, indexes for both
- [ ] API: errors as JSON, dates in one format, a limit on `days`; every endpoint documented
- [ ] Run now and Test do not hold a server thread
- [ ] SQLite: foreign keys enforced, the file compacted after the cleanup
- [ ] Time windows across daylight saving changes

## v2.7 — Alert handling

- [ ] Acknowledge and snooze errors, maintenance windows
- [ ] Thresholds: notify after N minutes or N rows, resolve after N absences
- [ ] Optional resolution notice, severity per query or rule
- [ ] Silent first run, row limit per notification with CSV attachment
- [ ] A failing query retried with longer and longer waits, not every minute
- [ ] Routing rules for channels too, rule tester in the UI
- [ ] Heartbeat to an external monitor

## Later

- Retention, alerts on failing queries and notification retries set in **Settings** instead of `.env`
- Error history with search, filters and export
- Pages that fit a phone screen: the errors table widens the page and pushes its filters out of view
- Configuration export/import, backup of the internal database
- Users, roles and audit log; then links to ErrorEngine in the notifications, for recipients who can sign in
- HTTP sources: Basic and OAuth2 auth, API key in the query string, request body, pagination, proxy
- PostgreSQL behind PgBouncer (query timeout without startup options)
- More channels: Slack, Google Chat, ntfy; signed webhooks
- Docker image
- Database migrations with Alembic
- The AS/400 driver (`ibm_db`) installed only where it is used
- More tests for HTTP sources, channels and web pages; tests of the page scripts in a simulated browser; the drivers tested against real databases (PostgreSQL, MySQL, SQL Server) in CI
