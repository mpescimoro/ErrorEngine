# Configuration

Settings live in `.env` in the project folder: copy `.env.example`, which has the ones most installations need. All of them are listed below, with their defaults. Restart ErrorEngine after a change.

## Settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | — | **Required.** Random string of at least 32 characters that signs the session cookie |
| `ERRORENGINE_PASSWORD_HASH` | — | **Required.** Hash of the password, see [Sign-in](#sign-in) |
| `ERRORENGINE_USER` | `admin` | User name |
| `ERRORENGINE_SESSION_HOURS` | `12` | Hours without doing anything before the session expires: what a page asks by itself, every 30 seconds, does not count |
| `ERRORENGINE_API_TOKEN` | — | Token of at least 32 characters for scripts that call the [API](api-reference.md) |
| `ERRORENGINE_HOST` | `0.0.0.0` | Address the server listens on |
| `ERRORENGINE_PORT` | `5000` | Port the server listens on |
| `ERRORENGINE_THREADS` | `4` | Requests the server handles at the same time: raise it if pages wait while **Run now** or **Test query** run slow queries |
| `ERRORENGINE_HTTPS` | `false` | `true` when users open ErrorEngine with `https://`, through a reverse proxy: the session cookie travels only over HTTPS. With `true`, the sign-in works only from `https://` addresses and from `localhost` or `127.0.0.1`; elsewhere the sign-in page says so. HSTS is up to the proxy, see [Running in production](#running-in-production) |
| `ERRORENGINE_TRUSTED_PROXY` | — | Address of the reverse proxy, e.g. `127.0.0.1`. From it alone, the client address, the host and `https` come from the `X-Forwarded-For`, `X-Forwarded-Host` and `X-Forwarded-Proto` headers; from anyone else they are dropped. `*` trusts every address: only if nothing but the proxy can reach ErrorEngine |
| `TIMEZONE` | `UTC` | Time zone of dates, schedules and statistics, e.g. `Europe/Rome`. A name that does not exist means UTC, and the log says so at startup |
| `MAIL_SERVER` | — | SMTP server. Empty: email is off and notifications go only to the channels, see [Email](#email) |
| `MAIL_SECURITY` | `starttls` | `starttls`, `ssl` (the default on port 465) or `none`, only for a relay without login on the internal network |
| `MAIL_PORT` | `587` | SMTP port; `465` with `ssl`, `25` with `none` |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | — | SMTP account. Without a user name there is no login (relay) |
| `MAIL_DEFAULT_SENDER` | `MAIL_USERNAME` | Address the emails are sent from |
| `M365_TENANT_ID`, `M365_CLIENT_ID` | — | Microsoft 365 through Graph: directory (tenant) ID and application (client) ID, see [Microsoft 365](#microsoft-365) |
| `M365_CLIENT_SECRET` | — | Client secret of that application |
| `MAIL_TIMEOUT_SECONDS` | `30` | Maximum wait for the email server |
| `MAIL_CA_FILE` | — | CA, or certificate, of an internal SMTP server (PEM). The certificate is always checked |
| `DB_CONNECT_TIMEOUT_SECONDS` | `15` | Maximum wait to connect to a monitored database |
| `DB_QUERY_TIMEOUT_SECONDS` | `300` | Maximum duration of a query |
| `HTTP_TIMEOUT_SECONDS` | `30` | Maximum wait for HTTP sources, webhooks, Telegram and Teams |
| `SCHEDULER_MAX_WORKERS` | `4` | Queries run in parallel |
| `NOTIFY_ATTEMPTS` | `5` | Attempts to deliver a notification: the first, then after 1, 5, 15 and 60 minutes, and every 60 minutes beyond the fifth (with `10`, the last comes about 6 hours 20 minutes after the first). Then the error stays active and shows **Not delivered**; `1`: no retries |
| `MONITOR_FAILURE_THRESHOLD` | `3` | Alert after this many failed runs in a row (`0`: no alert) |
| `ERRORENGINE_ADMIN_EMAILS` | — | Who gets that alert, comma-separated; if empty, the query's recipients, with routing on also its default recipients |
| `LOG_RETENTION_DAYS` | `30` | Days run logs are kept |
| `EMAIL_LOG_RETENTION_DAYS` | `90` | Days email logs are kept |
| `RESOLVED_ERRORS_RETENTION_DAYS` | `60` | Days resolved errors are kept |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

A setting that takes a number but holds something else stops ErrorEngine at startup, and the log names it.

Settings of older versions are no longer read: `MAIL_USE_TLS` (now `MAIL_SECURITY`) and `NOTIFY_RETRY_HOURS` (now `NOTIFY_ATTEMPTS`). If they are still in `.env`, the log says so at startup. `MAIL_USE_TLS=true` meant STARTTLS and `false` SSL, whatever the port: the log names the `MAIL_SECURITY` that keeps it so, and says whether email goes out differently until it is set (`false` without `MAIL_PORT` or with a port other than 465, `true` with port 465).

## Sign-in

ErrorEngine has a single user. Generate the hash of the password with the Python of the virtual environment (on Windows `venv\Scripts\python.exe`) and paste it in `.env`:

```bash
venv/bin/python -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
```

`SECRET_KEY` must be a random value of at least 32 characters, for example from `venv/bin/python -c "import secrets; print(secrets.token_hex(32))"`, which gives 64. Without `SECRET_KEY`, ErrorEngine does not start, and the log says why. With the example key, a shorter one, or without the password hash, it checks the queries but nobody can sign in, and the sign-in page says what is missing.

A session ends after `ERRORENGINE_SESSION_HOURS` without opening a page or changing something; a page left open does not keep it alive. **Sign out** ends it in every browser, and so does a restart of ErrorEngine: after one, sign in again.

After 5 wrong passwords in a row from the same address, that address waits a minute before the next try. Behind a reverse proxy, set `ERRORENGINE_TRUSTED_PROXY`: otherwise every attempt seems to come from the proxy, and the wait applies to everyone.

Scripts call the API with `Authorization: Bearer <ERRORENGINE_API_TOKEN>`, a token of at least 32 characters, generated as `SECRET_KEY`: a shorter one is refused, and the log says so at startup. Only `/api/health` needs no login.

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

Graph takes the email without saying which addresses do not exist: those come back later as emails to `MAIL_DEFAULT_SENDER`, while ErrorEngine counts them as reached. Look in that mailbox now and then.

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
| AS/400 (DB2) | `ibm_db` | DRDA: needs a DB2 Connect license. Port 446 unless you set another |

Use read-only database users: ErrorEngine runs the query you write. Without **Allow statements that change data** in the connection, a query with INSERT, UPDATE, DELETE, a procedure with EXEC or CALL, and the like is refused; with it, each save of the query asks to confirm. The check reads the text of the query: it stops mistakes, not someone set on getting around it (a function that writes, called from a SELECT, or quoting particular to one database). A database user that can only read stops both.

## Running in production

`start.bat` and `start.sh` create the virtual environment, install the dependencies and run `serve.py`: the Waitress server, in a single process, so the scheduler starts once. `python app.py` is Flask's development server and listens on this computer only.

- **Log:** `logs/errorengine.log` (a new file every 10 MB, the last 5 kept), and the console when there is one. **Settings** shows where it is and how much space it takes.
- **Windows service or scheduled task:** run `C:\path\to\ErrorEngine\venv\Scripts\python.exe C:\path\to\ErrorEngine\serve.py` with full paths; it moves to its own folder and reads `.env` by itself.
- **Stopping:** Ctrl+C, or SIGTERM on Linux (`kill`, `systemctl stop`, `docker stop`): the queries still running finish first. A second copy started on the same port stops at once, without touching the first one, and the log says why.
- **Network access:** put it behind a reverse proxy with HTTPS (IIS, nginx) if users reach it from other computers, then set `ERRORENGINE_HTTPS=true` and `ERRORENGINE_TRUSTED_PROXY` to the address of the proxy. The proxy must send the original address, host and protocol; with nginx:

  ```nginx
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
  ```

  Then sign in: in the log, `Login: admin from …` must show the address of your computer, not the one of the proxy.

  To have browsers use HTTPS only (HSTS), the proxy adds it, with nginx `add_header Strict-Transport-Security "max-age=31536000" always;`. HSTS covers the host name on every port: add it only if nothing else on that name is served with `http://`, or the browsers that opened ErrorEngine refuse those sites until `max-age` runs out. To go back, the proxy sends `max-age=0` over HTTPS for a while.
- **External monitoring:** `GET /api/health` answers 200 (`healthy`, or `degraded` when some queries cannot read their source) or 503 when the internal database or the scheduler is not working.

## Notification channels

**Notification channels** → **New channel**, then tick the channel in the query form.

| Type | Settings |
|------|----------|
| Webhook | URL, POST or PUT, optional headers (JSON) |
| Telegram | Bot token and chat ID |
| Teams | URL of a Workflows webhook: in Teams open Workflows, template "Send webhook alerts to a channel", choose team and channel, save and copy the URL. Office 365 connectors stopped working in May 2026: replace their URLs |

A webhook receives JSON:

| Field | Content |
|-------|---------|
| `event` | `errors_detected`, `errors_reminder` (errors already notified and still active), `monitor_failed`, `monitor_recovered` |
| `timestamp` | When it was sent, in UTC: `2026-11-13T17:00:00Z` |
| `query` | `id`, `name`, `description` |
| `errors_count`, `errors` | For errors: how many, and the rows (up to 50) |
| `error_ids` | For errors: the id of each of those rows, in the same order. An error keeps its id in reminders and retries, and [`POST /api/errors/{id}/resolve`](api-reference.md) takes it; one that is resolved and comes back gets a new id |
| `title`, `message` | For `monitor_` events |

Routing rules apply to email only: channels receive every error.

## Maintenance

Every day at 03:00 (server time) ErrorEngine deletes the run logs, email logs and resolved errors older than the retention settings. **Settings** → **Run cleanup now** deletes all of them at once; active errors are kept.

**Backup:** stop ErrorEngine and copy `instance/errorengine.db`, with `errorengine.db-wal` and `errorengine.db-shm` if present, and `.env`.

## Security

- Keep ErrorEngine on the internal network or behind a VPN, with HTTPS.
- Database passwords, tokens and channel URLs are stored in plain text in the internal database (encryption is planned for v2.5): protect the `instance/` folder and `.env`.
- Use read-only database users.
- The forms and the requests of the pages that change something carry a CSRF token: a page of another site, or of the same host on another port, cannot use the session of whoever is signed in.
- The responses tell the browser not to show ErrorEngine in a frame of another page, not to guess the type of a file and not to give the page address to other sites.
