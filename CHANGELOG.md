# Changelog

## 2.5.0

**Upgrading from 2.4.2**
- Before the first start, make a backup of the database: it is the way back to 2.4.2, and it holds the passwords in clear, so keep it away from the instance and delete it once 2.5.0 works. The first start encrypts them and rebuilds the database file: it needs free space of about twice the database (see Saved passwords and tokens in docs/configuration.md)
- Add `ERRORENGINE_ENCRYPTION_KEY` to `.env`, and save a copy of it apart: without it ErrorEngine does not start, and the log gives the command that generates it
- Remove `SECRET_KEY` from `.env`: it is no longer used
- Sign in again after the upgrade, and after every restart
- Scripts with the API token: reads, and the actions of the API reference; the other requests get 403
- HTTP sources and webhooks that answer with a redirect fail, and name the address to put in their URL
- On Linux and macOS the files ErrorEngine creates from now on, logs included, can be read only by its account: give access to whoever else reads them
- A connection with a query that changes data gets **Allow statements that change data**, and the log names it: check that it is meant

**Added**
- `ERRORENGINE_HTTPS`: behind a reverse proxy with HTTPS, the session cookie travels only over HTTPS, and the sign-in page says so when it is opened with `http://`. HSTS is up to the proxy: it covers every port of the host name
- `ERRORENGINE_TRUSTED_PROXY`: the address of the reverse proxy. From it, the client address (in the log and for the limit on wrong passwords), the host and `https` come from its `X-Forwarded-*` headers; from anyone else the same headers are dropped
- Connections: **Allow statements that change data**, off for a new connection. Without it, a query with INSERT, UPDATE, DELETE, a procedure (EXEC, CALL) and the like is refused when saved, tested or run, **Run now** included; with it, each save asks to confirm, on the server. The check reads the text of the query: it stops mistakes, not someone set on getting around it, and the guarantee is still a database user that can only read. On upgrade, a connection allows them if one of its queries already uses them, and the log says which
- Passwords and tokens saved in the database are encrypted with `ERRORENGINE_ENCRYPTION_KEY`, now required, kept in `.env` with the other secrets of the instance: the passwords of the connections and every text of the settings of HTTP sources and channels, except what their forms show (address, method, type of authentication, names). Without the key ErrorEngine does not start, and the log gives the command that generates it. At the first start those saved in clear are encrypted and the database file is rebuilt without them: make a backup before, which holds them in clear. With the wrong key ErrorEngine starts, and the log names what it cannot read. Several keys separated by commas change the key: the first encrypts, all of them read
- `DATABASE_URL`, already read, is documented: the database can be a SQLite file in another folder or on another disk of the same machine, apart from the instance
- HTTP sources: the API key can go in the query string (**API key in**), sent as a parameter of the request and saved encrypted, not written in the URL

**Changed**
- The forms no longer show the saved passwords, tokens, channel addresses and header values: left empty they stay, for the same server only, `(saved)` keeps a header, and Authentication **None** removes the token of an HTTP source. A form that comes back with a problem asks to type them again
- HTTP sources and webhooks no longer follow redirects, which would take the saved token and headers to another server: a redirect is an error that names the address to put in the URL
- The API token can read, and send only the requests of the API reference that change something: run or test a query, test its routing, a connection or a channel, resolve an error. Enabling a query, routing rules, SQL or a server typed in a form, test emails and the cleanup need a signed-in session: the token gets 403, and the log says so
- On Linux and macOS the files ErrorEngine creates, database and logs included, can be read only by the account that runs it; the log names `.env` and the database files when other users can read them
- `ERRORENGINE_API_TOKEN`, when it is set, needs at least 32 characters: a shorter one is refused, and the log says so at startup. The command in the docs generates 64
- Deprecated SQLAlchemy calls replaced; the tests now fail on any warning, those of Python 3.13 about connections left open included
- The check for statements that change data reads the whole query, no longer its first word only: `WITH ... DELETE`, `SELECT ... INTO` and a second statement after `;` count too, `SET NOCOUNT` and `SET TRANSACTION ISOLATION LEVEL` before a SELECT do not
- **Sign out** ends the session in every browser, copies of the cookie included, and a restart of ErrorEngine ends the open sessions: after one, sign in again
- `SECRET_KEY` is no longer used: the key that signs the session cookie is new at every start, as a restart ends the sessions anyway. If it is still in `.env`, the log says so at startup
- Unused code removed: styles no page used, the global names of the page scripts, `black` and `isort` from the development requirements
- Code comments trimmed to the reasons behind the code; tests that repeated the same cases merged, one helper for the queries the tests create

**Fixed**
- Cross-site request forgery (CSRF): a page of the same host on another port, or of another host of the same domain, could make a signed-in browser change or delete queries, connections, channels and logs. The forms and the requests of the pages that change something now carry a token of the session, checked by the server; a request the browser marks as coming from another site is refused even with it. Scripts with the API token do not need it
- Clickjacking: another site could show ErrorEngine in a frame and lead the user to click its buttons. The responses now forbid frames, tell the browser not to guess the type of a file and do not give the page address to other sites. `.js` and `.css` keep their type even where the Windows registry says `text/plain`
- The responses no longer name the web server (`Server: waitress`)
- After a wrong password, the request waited a second holding one of the threads of the server, and the attempts had no limit. Now, after 5 wrong passwords in a row from the same address, even sent together, that address waits a minute before the next try, and no thread waits. Behind a reverse proxy, set `ERRORENGINE_TRUSTED_PROXY`: otherwise every attempt seems to come from the proxy, and the wait applies to everyone
- A user name with a line break, typed at sign-in, could add a line of its own to the log
- A query, connection or channel form that cannot be saved comes back as it was sent, passwords, tokens and webhook addresses excepted, and says what to fix. Until now the server saved key fields made of commas only, an interval of 0 or below, days that are not 1 to 7, a channel without its address, and answered with an error page to a connection or channel without a name, a port that is not a number, headers that are not a JSON object or a channel type it does not know
- Routing rules through the API: an operator sent as a list or a regular expression sent as a number no longer give an error page; a number is kept as text, as the page sends it
- A query name already in use is said so, instead of an error with the details in the log
- Days saved as something other than numbers no longer break the dashboard, the list of queries and the form
- A page left open kept the session alive for ever: what it asks by itself every 30 seconds, and its own reloads, counted as activity. Now only what the user does counts, opening a page or changing something, for the hours of `ERRORENGINE_SESSION_HOURS`

## 2.4.2

**Added**
- Statistics of one query: chosen in the menu at the top or with a click on its row in **Details by query**, the cards and **Errors over time** show only that query, while **Top queries** and the table keep all of them; **Full statistics** on the query page opens on that query. `/api/stats/overview` and `/api/stats/timeline` take `query_id`
- In the API reference: `POST /api/queries/{id}/test`, which reads the source of a query without recording errors, and `POST /api/queries/{id}/routing/test`, the recipients the routing rules choose for the active errors or for the ones sent

**Changed**
- `MAIL_USE_TLS` is no longer read: `MAIL_SECURITY` replaced it in 2.3.0. With `MAIL_USE_TLS=false` and no `MAIL_PORT` or a port other than 465, or `true` with port 465, email now goes out with the other encryption: the warning at startup says so and names the `MAIL_SECURITY` that keeps it as it was. `NOTIFY_RETRY_HOURS` gets a warning too
- API: `/api/tags` and `/api/routing/operators` removed, no page called them. The tags of the queries and the operators of the routing rules work as before
- Unused code removed: two date functions of the page scripts, a fallback for Python versions older than the supported ones
- Tests that repeated the same setup merged, duplicates removed

**Fixed**
- A second copy of ErrorEngine started on the same port unlocked the queries the first one was running and started a second scheduler. Now it takes the port before anything else: it stops at once, and the log asks whether ErrorEngine is already running
- SIGTERM (`kill`, `systemctl stop`, `docker stop`) ended ErrorEngine on the spot: now it stops as with Ctrl+C, after the queries still running
- A setting that takes a number but held something else stopped ErrorEngine with a Python traceback, `ERRORENGINE_THREADS` and `ERRORENGINE_PORT` only after the scheduler had started: now it stops before starting anything, with a line that names the setting
- `POST /api/queries/{id}/routing/test` failed without a body, or with errors in another shape: now without a body it uses the active errors, and another shape gets the one expected

## 2.4.1

**Added**
- `ERRORENGINE_THREADS` in `.env`: the requests the web server handles at the same time (4, as before)

**Changed**
- AS/400 (DB2): the default port is 446, the DRDA port of IBM i, instead of 50000, the one of DB2 on Linux and Windows
- The docs say what `NOTIFY_ATTEMPTS` above 5 does: the further attempts come every 60 minutes
- Unused code removed: a dashboard script that no page loaded, two Python functions nothing called

**Fixed**
- A connection saved without a port did not connect with Oracle and AS/400 (DB2): it now uses the driver's default, as the test in the form already did, and the form shows the AS/400 one
- A routing condition with quotes in its value was cut short when the rule was opened and saved again
- An inline email template that starts and ends with Jinja code (e.g. `{% if errors %}…{% endif %}`) was taken for a template name and replaced by the default one
- Emails sent through SMTP had no `Date` and `Message-ID` headers: some mail servers do not add them, and spam filters count it against the sender
- Errors while saving or deleting a query showed the database error on screen, SQL and values included: now the details go only in the log
- A `TIMEZONE` that does not exist became UTC without a word: now the log says so at startup
- Resolving an error already resolved moved its resolution time
- An HTTP source that returns plain values instead of objects failed with a Python error: now it says what it found
- The API took routing rules with an operator that does not exist, whose condition never matched, and failed on rules sent in the wrong shape

## 2.4.0

**Added**
- **Send sample email** in the query form: the email as the form is now, saved or not, with the rows of **Test query** or the latest errors of the query, to the addresses typed. A template that cannot be used is reported there
- Webhooks: the `errors_reminder` event for reminders and `error_ids`, the id of each error: the same in reminders and retries, and the one `POST /api/errors/{id}/resolve` takes. Telegram and Teams tell reminders from new errors
- Query form: **Emails** for routing (one per recipient, or one per error), the API key header and the headers of HTTP sources
- Errors whose notification was given up show to whom and why, on the errors page and on the query page; a filter on the errors page lists them, and the dashboard counts them

**Changed**
- Failed notifications: 5 attempts, the first and then after 1, 5, 15 and 60 minutes, sent by the scheduler also between the checks of the query and outside its time window. `NOTIFY_ATTEMPTS` replaces `NOTIFY_RETRY_HOURS`, which kept trying for 24 hours (about 30 attempts): if it is still in `.env`, a warning at startup says so
- An email subject with something between braces that is not a placeholder (e.g. `{query}`) goes out with it as written, and saving the query points it out
- One SMTP connection for all the emails of a check
- The log has one line per delivery attempt, whatever the number of errors: what went out, what is still pending and when it is tried again, what was given up, and why
- A disabled database connection stops its queries: they are skipped, not failing, and the lists say "Connection disabled"
- Opening the query form no longer runs the query: the fields offered by routing come from the latest errors, or from **Test query**
- Webhook `timestamp` in UTC with a `Z`, to the second (`2026-11-13T17:00:00Z`)
- Telegram and Teams: "New errors" or "Errors still active", and "Time" for the time of the message
- The query form says when email is off

**Fixed**
- Failed notifications were sent again only at the next check of the query, not after 1 to 60 minutes as the docs said: a query checked once a day tried again the next day, and gave up if that attempt failed too
- An address the mail server refused, while it took the others, counted as reached: now it gets the attempts of a failed notification, and the error shows the server's reason. With Microsoft 365 a refusal comes back later as an email to the sender, which ErrorEngine does not see
- An unknown placeholder in the email subject blocked every email of the query
- Saving a query dropped what the form did not show: a disabled connection or channel, the routing aggregation, the HTTP headers and the API key header name
- An HTTP query that had been a database query kept reading the database
- Key fields missing from the results merged every row into a single error: now the check fails and names the missing fields; the same with no key fields at all (e.g. `,`)
- A query left locked by a check cut short (ErrorEngine stopped while it ran) was skipped for about 15 minutes after a restart: now it is unlocked at startup
- The email log showed the subject of failed emails with its placeholders
- Texts that said something else: routing with no rules (errors go to the default recipients, or to nobody), Resolve (a row the query still returns comes back as a new error and is notified again), Run now (it said "Done" also when the notifications had not gone out), reminders (they go out at the checks of the query), automatic cleanup (server time), sign-in without `SECRET_KEY` (ErrorEngine does not start), export of the errors (only the active ones)

## 2.3.0

**Added**
- Microsoft 365 through Microsoft Graph: an application registered in Entra ID sends the emails, with no mailbox password and no SMTP AUTH (Microsoft turns SMTP with a password off by default at the end of December 2026)
- SMTP relay without login: leave `MAIL_USERNAME` empty; `MAIL_SECURITY=none` for an internal relay without encryption (never with a password)
- Email can be turned off: with `MAIL_SERVER` empty, notifications go only to the channels
- Settings show how emails go out: method, server, sign-in and sender

**Changed**
- `MAIL_SERVER` no longer defaults to `smtp.office365.com`: a `.env` without it now has email off, as Settings and the startup log say
- `MAIL_SECURITY` (`starttls`, `ssl`, `none`) replaces `MAIL_USE_TLS`, which is still read; without `MAIL_PORT` the port follows it (587, 465, 25); `MAIL_DEFAULT_SENDER` defaults to `MAIL_USERNAME`
- `.env.example` keeps only the settings most installations need; the full list is in `docs/configuration.md`
- Code comments trimmed to the reasons behind the code; tests grouped by feature, duplicates removed
- Unused validation functions removed (no form called them)

**Fixed**
- SMTP connections left open when the sign-in or STARTTLS failed

## 2.2.0

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
- Channels: a Teams Workflows answer (202) counted as a failure; Telegram messages with `<` or `&` and webhooks with decimal values not sent
- Internal database locked while notifications were sent (SQLite now in WAL mode)
- Query detail showed "API" as source type for database queries
- Saving a query with an empty field stored the text "None": an empty email template became an email with "None" as body
- Query detail: active errors counted only up to 50, tag links broken by a space after the comma, an empty tag badge
- Tag filter: "prod" also showed "preprod"
- Write-query warning: "I know what I'm doing" did not save, a second click was needed
- Editing a routing rule turned it back on; the form allowed priorities up to 100, the server up to 1000
- Settings showed a hard-coded version and database
- Layout: card titles pushed to the right, "Actions" headers not aligned, a tick inside switches, grey warning buttons
- `LOG_LEVEL` in `.env` was ignored

## 2.1.2

- Fix `validate_key_fields` return value, `is_not_empty` operator, `api_query_status` 404
- Test suite expanded from 85 to 153 tests
- Old `routes.py` removed

## 2.1.1

- IBM i (AS/400) driver via JT400 JDBC
- Schedules anchored to a reference time instead of the last run
- Fix `MonitoredQuery.__repr__` indentation (was unreachable)
- Add missing `__repr__` to `DatabaseConnection`
- Replace deprecated `datetime.utcnow()` with centralized `get_utc_now()`
- Split `routes.py` into `routes/web.py` and `routes/api.py`
- Fix missing `logger` import in API routes
- Remove unused imports

## 2.1

Multi-database, HTTP sources, notification channels, tags, statistics page.

## 2.0

Conditional routing, reminders, time windows, cleanup automation.

## 1.0

Initial release.
