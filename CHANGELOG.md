# Changelog

## 2.2.0 — 2026-11-02

**Added**
- Login (single user) and API token for scripts
- Production server (`serve.py`, Waitress) with rotating log file
- Recipients of each notification shown in the error list
- Settings show the server log: where it is, how much space it takes and how it rotates
- Pages refresh when errors change; query filter on the Errors page
- New database columns added automatically at startup
- Failed notifications retried until every recipient and channel gets them, with waits from 1 minute up to 1 hour; after `NOTIFY_RETRY_HOURS` (24 by default) the error shows "Not delivered". A channel that refuses for good (HTTP 4xx other than 408 and 429) is not retried
- Alert when a query keeps failing, and when it recovers
- Connection and query timeouts for every driver, SMTP and HTTP timeouts
- Queries run in parallel: a slow source no longer blocks the others
- Health check with scheduler status; failing queries and the version only with login or API token
- Time windows past midnight (e.g. 22:00–06:00); a warning when a query is saved with no day selected (it runs every day)
- CI on GitHub: tests on Python 3.11, 3.12 and 3.13, flake8 and a dependency audit on every push

**Security**
- Email templates rendered in a sandbox: no access to Python objects, configuration or files
- Scheduler REST API (Flask-APScheduler) turned off
- `start.bat` and `start.sh` run `serve.py`, also on Linux (gunicorn removed: each worker would start its own scheduler); `python app.py` is for development only and listens on 127.0.0.1
- Names and server messages can no longer run code in delete confirmations and channel tests
- SMTP server certificate and name checked (they were not); `MAIL_CA_FILE` for an internal CA
- Test query: statements that change data (DELETE, DROP...) show the same warning as saving, and the server runs them only once confirmed
- Tokens and keys in URLs hidden from error messages, logs and alerts (e.g. `apikey=`, Telegram bot token)
- Dependencies updated: the old pinned versions had 43 known vulnerabilities (Werkzeug, urllib3, requests, Flask, python-dotenv)

**Changed**
- Everything in English: interface, emails, notifications, messages, logs, code comments and tests
- Python 3.11 to 3.13 supported (3.10 has reached end of life)
- Test and development tools moved to `requirements-dev.txt`
- Fonts and icons served by the app: the interface works without Internet access
- New logo, an engine warning light, also used as favicon
- Dashboard: problems first (failing queries, then the ones with most errors); failed emails shown under "Emails sent today"
- Clearer actions: on/off switch in the lists, Activate/Deactivate button with a label in the query detail
- Statistics, "Errors today" and "Emails sent today" follow the configured time zone; the chart includes today
- README and docs rewritten: sign-in setup, every setting, production server, backup; new screenshots
- Teams channels through Workflows: guide in the channel list and in the docs (Office 365 connectors stopped working in May 2026)
- Unused code and imports removed

**Fixed**
- AS/400 (ibm_db): every query failed when closing the connection
- IBM i: DECIMAL values converted to numbers, also integers beyond 19 digits (they came out wrong); queries starting together no longer fail while starting Java
- Time windows: the last slot of the window was skipped
- HTTP sources: the API key typed in the form was never sent; a response without the JSON path became an error (and resolved the real ones), now the source is reported as failing
- Case-insensitive regex conditions changed meaning (`\D` became `\d`); an invalid regex is refused when the rule is saved
- Deleting a query with email history failed on databases that enforce foreign keys
- Teams (Workflows answers 202), Telegram messages with `<` or `&`, webhooks with decimal values
- Internal database locked while notifications were sent (SQLite now in WAL mode)
- Query detail showed "API" as source type for database queries
- Saving a query with an empty field stored the text "None": an empty email template became an email with "None" as body
- Query detail stopped counting active errors at 50; tags with a space after the comma; an empty tag badge
- Tag filter: "prod" also showed "preprod"
- Write-query warning: "I know what I'm doing" did not save, a second click was needed
- Editing a routing rule turned it back on; priority limited to 100 in the form and 1000 on the server
- Settings showed a hard-coded version and database
- Layout: card titles pushed to the right, "Actions" headers not aligned, a tick inside switches, grey warning buttons
- `LOG_LEVEL` in `.env` was ignored

## 2.1.2 — 2026-02-11

- Fix `validate_key_fields` return value, `is_not_empty` operator, `api_query_status` 404
- Test suite expanded from 85 to 153 tests
- Old `routes.py` removed

## 2.1.1 — 2026-02-10

- IBM i (AS/400) driver via JT400 JDBC
- Schedules anchored to a reference time instead of the last run
- Fix `MonitoredQuery.__repr__` indentation (was unreachable)
- Add missing `__repr__` to `DatabaseConnection`
- Replace deprecated `datetime.utcnow()` with centralized `get_utc_now()`
- Split `routes.py` into `routes/web.py` and `routes/api.py`
- Fix missing `logger` import in API routes
- Remove unused imports

## 2.1 — 2026-02-03

Multi-database, HTTP sources, notification channels, tags, statistics page.

## 2.0

Conditional routing, reminders, time windows, cleanup automation.

## 1.0

Initial release.
