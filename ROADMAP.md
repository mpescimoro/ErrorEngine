# Roadmap

Planned work, in order. Released versions are in [CHANGELOG.md](CHANGELOG.md).

## v2.6 — Data and API

- [ ] Error identity, changed once with a migration that recomputes the open errors: same value types from every driver (numbers, CHAR without trailing spaces), a key-field hash that keeps values containing `|` apart, key fields edited without notifying the open errors again
- [ ] Errors list and logs split into pages, statistics computed in the database, indexes for both
- [ ] A row limit per notification, the other rows in a CSV attachment
- [ ] API: failures answered in JSON too (a 404 is an HTML page today), dates in one format, a limit on `days`; every read documented
- [ ] Run now and Test do not hold a server thread
- [ ] SQLite: foreign keys enforced, the file compacted after the cleanup
- [ ] Time windows across daylight saving changes
- [ ] Setup at the first start: the encryption key and the password hash without commands to copy into `.env`

## v2.7 — Alert handling

- [ ] Acknowledge and snooze errors, maintenance windows
- [ ] Thresholds: notify after N minutes or N rows, resolve after N absences
- [ ] Optional resolution notice, severity per query or rule
- [ ] Silent first run
- [ ] A failing query retried with longer and longer waits, not every minute
- [ ] Routing rules for channels too, rule tester in the UI
- [ ] Heartbeat to an external monitor

## Later

- Retention, alerts on failing queries and notification retries set in **Settings** instead of `.env`
- Error history with search, filters and export
- Pages that fit a phone screen: the errors table widens the page and pushes its filters out of view
- Configuration export/import, backup of the internal database
- Users, roles and audit log; then links to ErrorEngine in the notifications, for recipients who can sign in
- An API token that can only read, for monitors
- Monitoring queries read-only on the database side too, where the driver can open a read-only connection or transaction
- A Content-Security-Policy header, once the pages no longer have inline scripts
- HTTP sources: Basic auth and a request body in the form (the source reads them already), OAuth2, pagination, proxy
- PostgreSQL behind PgBouncer (query timeout without startup options)
- More channels: Slack, Google Chat, ntfy; signed webhooks
- Docker image
- Database migrations with Alembic, before the internal database can be other than SQLite: in databases created before 2.5 the `password` column of the connections is VARCHAR(255), and an encrypted password of 128 characters or more does not fit
- The AS/400 driver (`ibm_db`) installed only where it is used, and passwords containing `;`
- More tests for HTTP sources, channels and web pages; tests of the page scripts in a simulated browser; the drivers tested against real databases (PostgreSQL, MySQL, SQL Server) in CI
