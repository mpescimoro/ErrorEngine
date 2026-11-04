# Configuration

Settings live in `.env` in the project folder: copy `.env.example`, which has the ones most installations need. All of them are listed below, with their defaults. Restart ErrorEngine after a change.

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
| `MAIL_SERVER` | — | SMTP server. Empty: email is off and notifications go only to the channels, see [Email](#email) |
| `MAIL_SECURITY` | `starttls` | `starttls`, `ssl` (the default on port 465) or `none`, only for a relay without login on the internal network |
| `MAIL_PORT` | `587` | SMTP port; `465` with `ssl`, `25` with `none` |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | — | SMTP account. Without a user name there is no login (relay) |
| `MAIL_DEFAULT_SENDER` | `MAIL_USERNAME` | Address the emails are sent from |
| `M365_TENANT_ID`, `M365_CLIENT_ID` | — | Microsoft 365 through Graph: directory (tenant) ID and application (client) ID, see [Microsoft 365](#microsoft-365) |
| `M365_CLIENT_SECRET` | — | Client secret of that application |
| `MAIL_USE_TLS` | — | Older setting, still read when `MAIL_SECURITY` is not set: `true` means `starttls`, `false` means `ssl` |
| `MAIL_TIMEOUT_SECONDS` | `30` | Maximum wait for the email server |
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

## Email

ErrorEngine sends email through an SMTP server or through Microsoft 365. With neither configured, email is off: notifications go only to the channels (Teams, Telegram, webhook). **Settings** shows how emails go out, and **Send test email** tries it.

### SMTP server

`MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME` and `MAIL_PASSWORD` work with any provider: your own mail server, Gmail (it needs an app password, which needs 2-Step Verification) or services such as Amazon SES, Brevo or Mailgun. The connection uses STARTTLS (port 587), or SSL when `MAIL_PORT` is 465 or `MAIL_SECURITY=ssl`; the server certificate is always checked (`MAIL_CA_FILE` for an internal CA).

### Relay without login

Leave `MAIL_USERNAME` empty and set `MAIL_DEFAULT_SENDER`: ErrorEngine connects without signing in. That is what an internal relay expects, or a Microsoft 365 connector that recognizes your server by its IP address (`MAIL_SERVER` is then your domain's MX endpoint, `…mail.protection.outlook.com`, with `MAIL_PORT=25`). If the relay does not support encryption, `MAIL_SECURITY=none` sends in clear text: use it only on the internal network. With a user name set, ErrorEngine never sends the password unencrypted.

### Microsoft 365

Microsoft turns off SMTP with a password by default at the end of December 2026. ErrorEngine can send through Microsoft Graph instead, with an application registered in Microsoft Entra ID: no mailbox password, and SMTP AUTH can stay off. In the Microsoft Entra admin center:

1. *App registrations* → *New registration*: any name, accounts in this organizational directory only
2. *API permissions* → *Add a permission* → *Microsoft Graph* → *Application permissions* → `Mail.Send`, then *Grant admin consent*
3. *Certificates & secrets* → *New client secret*, and copy its value

Then in `.env`:

```
M365_TENANT_ID=<Directory (tenant) ID, from the app's Overview>
M365_CLIENT_ID=<Application (client) ID>
M365_CLIENT_SECRET=<the secret's value>
MAIL_DEFAULT_SENDER=alerts@yourcompany.com
```

With these set, ErrorEngine sends through Graph from `MAIL_DEFAULT_SENDER` and does not use the SMTP settings. `Mail.Send` allows sending as any mailbox of the organization: limit the application to the sender mailbox in Exchange Online (RBAC for Applications). The secret expires: put a new one in `.env` before its expiry date.

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
- Database passwords, tokens and channel URLs are stored in plain text in the internal database (encryption is planned for v2.5): protect the `instance/` folder and `.env`.
- Use read-only database users.
