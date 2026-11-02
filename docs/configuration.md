# Configuration

Settings live in `.env` in the project folder: copy `.env.example`, which has the same list with short comments. Restart ErrorEngine after a change.

## Settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | — | **Required.** Random string that signs the session cookie |
| `ERRORENGINE_PASSWORD_HASH` | — | **Required.** Hash of the password, see [Sign-in](#sign-in) |
| `ERRORENGINE_USER` | `admin` | User name |
| `ERRORENGINE_SESSION_HOURS` | `12` | Hours of inactivity before the session expires |
| `ERRORENGINE_API_TOKEN` | — | Token for scripts that call the [API](api-reference.md) |
| `ERRORENGINE_HOST` | `0.0.0.0` | Address the server listens on |
| `ERRORENGINE_PORT` | `5000` | Port the server listens on |
| `TIMEZONE` | `UTC` | Time zone of dates, schedules and statistics, e.g. `Europe/Rome` |
| `MAIL_SERVER` | `smtp.office365.com` | SMTP server |
| `MAIL_PORT` | `587` | SMTP port |
| `MAIL_USE_TLS` | `true` | `true`: STARTTLS (usually port 587); `false`: SSL (usually port 465) |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | — | SMTP account |
| `MAIL_DEFAULT_SENDER` | — | Sender of the notifications |
| `MAIL_TIMEOUT_SECONDS` | `30` | Maximum wait for the SMTP server |
| `MAIL_CA_FILE` | — | CA, or certificate, of an internal SMTP server (PEM). The certificate is always checked |
| `DB_CONNECT_TIMEOUT_SECONDS` | `15` | Maximum wait to connect to a monitored database |
| `DB_QUERY_TIMEOUT_SECONDS` | `300` | Maximum duration of a query |
| `HTTP_TIMEOUT_SECONDS` | `30` | Maximum wait for HTTP sources, webhooks, Telegram and Teams |
| `SCHEDULER_MAX_WORKERS` | `4` | Queries run in parallel |
| `NOTIFY_RETRY_HOURS` | `24` | Hours a failed notification is retried, with longer and longer waits; then it is marked as not delivered |
| `MONITOR_FAILURE_THRESHOLD` | `3` | Alert after this many failed runs in a row (`0`: no alert) |
| `ERRORENGINE_ADMIN_EMAILS` | — | Who gets that alert, comma-separated; if empty, the query's recipients |
| `LOG_RETENTION_DAYS` | `30` | Days run logs are kept |
| `EMAIL_LOG_RETENTION_DAYS` | `90` | Days email logs are kept |
| `RESOLVED_ERRORS_RETENTION_DAYS` | `60` | Days resolved errors are kept |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Sign-in

ErrorEngine has a single user. Generate the hash of the password with the Python of the virtual environment (on Windows `venv\Scripts\python.exe`) and paste it in `.env`:

```bash
venv/bin/python -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
```

`SECRET_KEY` must be a random value, for example from `venv/bin/python -c "import secrets; print(secrets.token_hex(32))"`. Until both are set, nobody can sign in, and the sign-in page says what is missing.

Scripts call the API with `Authorization: Bearer <ERRORENGINE_API_TOKEN>`. Only `/api/health` needs no login.

## Database drivers

`requirements.txt` installs every driver; you can remove the lines you don't need. The connection form only offers the drivers that are installed.

| Database | Library | Notes |
|----------|---------|-------|
| SQLite | built in | Full path of the file |
| Oracle | `oracledb` | Thin mode: no Oracle Client needed |
| PostgreSQL | `psycopg2-binary` | |
| MySQL / MariaDB | `PyMySQL` | |
| SQL Server | `pymssql` | |
| IBM i (AS/400) | `JPype1` + JT400 | Needs Java 8+ and `lib/jt400.jar`, see [IBM i driver](ibmi-driver.md) |
| AS/400 (DB2) | `ibm_db` | DRDA: needs a DB2 Connect license |

Use read-only database users: ErrorEngine runs the query you write, and the form only warns before saving one that changes data.

## Running in production

`start.bat` and `start.sh` create the virtual environment, install the dependencies and run `serve.py`: the Waitress server, in a single process, so the scheduler starts once. `python app.py` is Flask's development server and listens on this computer only.

- **Log:** `logs/errorengine.log` (a new file every 10 MB, the last 5 kept), and the console when there is one. **Settings** shows where it is and how much space it takes.
- **Windows service or scheduled task:** run `C:\path\to\ErrorEngine\venv\Scripts\python.exe C:\path\to\ErrorEngine\serve.py` with full paths; it moves to its own folder and reads `.env` by itself.
- **Network access:** put it behind a reverse proxy with HTTPS (IIS, nginx) if users reach it from other computers.
- **External monitoring:** `GET /api/health` answers 200 (`healthy`, or `degraded` when some queries cannot read their source) or 503 when the internal database or the scheduler is not working.

## Notification channels

**Notification channels** → **New channel**, then tick the channel in the query form.

| Type | Settings |
|------|----------|
| Webhook | URL, POST or PUT, optional headers (JSON) |
| Telegram | Bot token and chat ID |
| Teams | URL of a Workflows webhook: in Teams open Workflows, template "Send webhook alerts to a channel", choose team and channel, save and copy the URL. Office 365 connectors stopped working in May 2026: replace their URLs |

A webhook receives JSON: `event` (`errors_detected`, `monitor_failed`, `monitor_recovered`), `timestamp` (UTC), `query` (`id`, `name`, `description`) and, for errors, `errors_count` and `errors` (up to 50 rows); for the other events, `title` and `message`.

Routing rules apply to email only: channels receive every error.

## Maintenance

Every day at 03:00 (server time) ErrorEngine deletes the run logs, email logs and resolved errors older than the retention settings. **Settings** → **Run cleanup now** deletes all of them at once; active errors are kept.

**Backup:** stop ErrorEngine and copy `instance/errorengine.db`, with `errorengine.db-wal` and `errorengine.db-shm` if present, and `.env`.

## Security

- Keep ErrorEngine on the internal network or behind a VPN, with HTTPS.
- Database passwords, tokens and channel URLs are stored in plain text in the internal database (encryption is planned for v2.4): protect the `instance/` folder and `.env`.
- Use read-only database users.
