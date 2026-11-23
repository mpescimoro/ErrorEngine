# Configuration

Settings live in `.env` in the project folder: copy `.env.example`, which has the ones most installations need. All of them are listed below, with their defaults. Restart ErrorEngine after a change.

## Settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `ERRORENGINE_PASSWORD_HASH` | — | **Required.** Hash of the password, see [Sign-in](#sign-in) |
| `ERRORENGINE_ENCRYPTION_KEY` | — | **Required.** Key that encrypts the passwords and tokens saved in the database, see [Saved passwords and tokens](#saved-passwords-and-tokens) |
| `DATABASE_URL` | `instance/errorengine.db` | Where the database is: `sqlite:///` and the full path of the file, on a disk of this machine, see [Saved passwords and tokens](#saved-passwords-and-tokens) |
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

Settings of older versions are no longer read: `MAIL_USE_TLS` (now `MAIL_SECURITY`), `NOTIFY_RETRY_HOURS` (now `NOTIFY_ATTEMPTS`) and `SECRET_KEY` (ErrorEngine makes a new one at every start). If they are still in `.env`, the log says so at startup. `MAIL_USE_TLS=true` meant STARTTLS and `false` SSL, whatever the port: the log names the `MAIL_SECURITY` that keeps it so, and says whether email goes out differently until it is set (`false` without `MAIL_PORT` or with a port other than 465, `true` with port 465).

## Sign-in

ErrorEngine has a single user. Generate the hash of the password with the Python of the virtual environment (on Windows `venv\Scripts\python.exe`) and paste it in `.env`:

```bash
venv/bin/python -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
```

Without the password hash, ErrorEngine checks the queries but nobody can sign in, and the sign-in page says what is missing.

A session ends after `ERRORENGINE_SESSION_HOURS` without opening a page or changing something; a page left open does not keep it alive. **Sign out** ends it in every browser, and so does a restart of ErrorEngine: after one, sign in again.

After 5 wrong passwords in a row from the same address, that address waits a minute before the next try. Behind a reverse proxy, set `ERRORENGINE_TRUSTED_PROXY`: otherwise every attempt seems to come from the proxy, and the wait applies to everyone.

Scripts call the API with `Authorization: Bearer <ERRORENGINE_API_TOKEN>`, a random token of at least 32 characters, for example from `venv/bin/python -c "import secrets; print(secrets.token_hex(32))"`, which gives 64: a shorter one is refused, and the log says so at startup. The token can read, and change only what the [API reference](api-reference.md) lists: running and testing what is saved, resolving errors. Only `/api/health` needs no login.

## Saved passwords and tokens

The passwords of the connections and the secrets of the HTTP sources and of the channels (tokens, keys, headers, the addresses of webhooks and Teams) are saved in the database encrypted with `ERRORENGINE_ENCRYPTION_KEY`. The key is in `.env`, with the other secrets of the instance, such as `MAIL_PASSWORD` and `ERRORENGINE_API_TOKEN`: the database can then be kept apart from the instance, and a copy of it without the key does not reveal them. Generate the key once, with the Python of the virtual environment (on Windows `venv\Scripts\python.exe`), and save a copy of it apart, in a password manager: without it the saved passwords cannot be read.

```bash
venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Without the key ErrorEngine does not start, and the log gives the command.

- **Where the database is.** By default in `instance/`, inside the folder of ErrorEngine: a copy of the folder takes it together with `.env`. To keep it apart, set `DATABASE_URL` to `sqlite:///` followed by the full path of the file, in another folder or on another disk of the same machine, with its own permissions and backups: `sqlite:////var/lib/errorengine/errorengine.db`, `sqlite:///D:/ErrorEngine-data/errorengine.db`. Not on a network share, where SQLite in WAL mode does not work. A database on another machine needs a database server such as PostgreSQL, not tested with ErrorEngine yet.
- **Upgrading from 2.4.2.** First make a backup: it is the way back to 2.4.2, and it holds the passwords in clear, so keep it away from the instance and delete it once this version works. At the first start with the key, the passwords and tokens saved in clear are encrypted, all in one go, the error messages saved before no longer show the address or token of a channel, and the database file is rebuilt without them: that needs free disk space of about twice the database, half of it in the temporary folder of the system. If the rebuild fails, the log says how much space to free, and ErrorEngine tries again at every start. Move the database only after that first start.
- **A key lost or changed by mistake.** ErrorEngine starts anyway: the log names the connections, queries and channels whose passwords it cannot read, and each of them says so when it is used. Put the right key back, or type those passwords and tokens again in their forms.
- **Changing the key.** Put the new one first and the old one after it, separated by a comma (`ERRORENGINE_ENCRYPTION_KEY=new,old`), and start ErrorEngine: it encrypts everything again with the new one, and the log says when the old one can be removed. A backup of the database can be read only with the key of its time: keep an old key as long as the backups made with it.
- **In the forms.** What is saved is not shown. A password, token or address left empty stays as it is, for the same server only: with another host, port or scheme in the address, the form asks to type it again. Authentication **None** removes the token of an HTTP source. The values of the saved headers are shown as `(saved)`: left so they stay, and a line removed removes its header. The URL of an HTTP source is shown: a key goes in **Token/Key**, sent in a header or in the query string (**API key in**), or in the headers, not in the URL.
- **Redirects.** HTTP sources and webhooks do not follow them, as they would take the token and headers to another server: a redirect is an error that names the address to put in the URL.

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
| AS/400 (DB2) | `ibm_db` | DRDA: needs a DB2 Connect license. Port 446 unless you set another. A password containing `;` does not work: use **IBM i (AS/400)** |

Use read-only database users: ErrorEngine runs the query you write. Without **Allow statements that change data** in the connection, a query with INSERT, UPDATE, DELETE, a procedure with EXEC or CALL, and the like is refused; with it, each save of the query asks to confirm. `SET NOCOUNT` and `SET TRANSACTION ISOLATION LEVEL` before a SELECT, as SQL Server queries often start, do not count. The check reads the text of the query: it stops mistakes, not someone set on getting around it (a function that writes, called from a SELECT, or quoting particular to one database). A database user that can only read stops both.

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

**Backup:** stop ErrorEngine and copy the database, `instance/errorengine.db` or the file of `DATABASE_URL`, with its `-wal` and `-shm` files if present; then `.env`, in another copy. The copy of the database alone does not reveal the saved passwords; `.env` holds the key and the other secrets of the instance, so keep its copy where only who manages ErrorEngine can read it.

## Security

- Keep ErrorEngine on the internal network or behind a VPN, with HTTPS.
- Passwords, tokens and channel addresses are saved encrypted: see [Saved passwords and tokens](#saved-passwords-and-tokens).
- On Linux and macOS the files ErrorEngine creates, database and logs included, can be read only by the account that runs it; at every start the log names `.env` and the database files when other users can read them, with the `chmod` that closes them. A program that reads the logs with another account needs to be given access to them.
- On Windows the permissions of the folders decide. A folder created under `C:\` usually lets every user read it and create files in it: someone could add a `.py` file that ErrorEngine runs with its account, or change `.env`. Leave the folder of ErrorEngine to SYSTEM, to the administrators and to the account that runs ErrorEngine only, from a command prompt or PowerShell opened as administrator:

  ```
  icacls C:\ErrorEngine /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "DOMAIN\account:(OI)(CI)M"
  ```

  `*S-1-5-18` is SYSTEM and `*S-1-5-32-544` the administrators, in every language of Windows; `DOMAIN\account` is the account that runs ErrorEngine, not needed when it runs as SYSTEM. The same for the folder of the database, if it is apart. `icacls C:\ErrorEngine` then shows the result.
- Use read-only database users.
- The forms and the requests of the pages that change something carry a CSRF token: a page of another site, or of the same host on another port, cannot use the session of whoever is signed in.
- The responses tell the browser not to show ErrorEngine in a frame of another page, not to guess the type of a file and not to give the page address to other sites.
