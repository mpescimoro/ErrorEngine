# FAQ

## How it works

### How are errors detected?

ErrorEngine runs your query on a schedule. Each row it returns is an active error, identified by the key fields. A new row is notified once (then with reminders, if you turn them on); when the row no longer comes back, the error is resolved by itself.

### Is it real-time?

No, it checks on a schedule: from every minute to every 24 hours.

### Can I monitor several databases?

Yes: create one connection per database and choose it in each query.

### Do I need an email server?

No. Leave `MAIL_SERVER` empty in `.env` and choose notification channels (Teams, Telegram, webhook) in the queries. Email also works with any SMTP server, an internal relay or Microsoft 365, see [Email](configuration.md#email).

### What if a query cannot read its data?

The query shows **Failing** in the lists and the scheduler tries again every minute. After `MONITOR_FAILURE_THRESHOLD` failed runs in a row (3 by default) an alert goes to `ERRORENGINE_ADMIN_EMAILS` (or to the query's recipients) and to the query's channels; a second message says when it works again.

---

## Troubleshooting

### Nobody can sign in

The sign-in page says what is missing: `ERRORENGINE_PASSWORD_HASH`, or a `SECRET_KEY` that is not the example one. See [Configuration](configuration.md#sign-in) and restart ErrorEngine.

### Emails are not sent

1. **Settings** shows how emails go out; **Send test email** tries it and shows the error
2. Check the email settings in `.env`, see [Email](configuration.md#email)
3. "certificate verify failed": the SMTP server certificate is checked. Put in `MAIL_SERVER` the name the certificate is issued to, not an IP address; for an internal server with its own certificate, set `MAIL_CA_FILE` to its CA (PEM file)
4. Microsoft 365: SMTP with a password is turned off by default from the end of December 2026. Send through Microsoft Graph instead, see [Microsoft 365](configuration.md#microsoft-365)
5. Gmail: use an app password, which needs 2-Step Verification
6. Look at `logs/errorengine.log` and at the errors page: notifications that fail are tried again, with longer and longer waits, for `NOTIFY_RETRY_HOURS` (24 by default); then the error shows **Not delivered**

### Teams messages stopped arriving

Office 365 connectors stopped working in May 2026. In Teams open Workflows, create a flow from the template "Send webhook alerts to a channel" and put its URL in the channel, see [Notification channels](configuration.md#notification-channels).

### Dates or times are wrong

Set `TIMEZONE` (e.g. `Europe/Rome`) in `.env` and restart.

### The query runs but finds no errors

1. **Test query** in the query form shows what the query returns
2. Check the **key fields**: they must be columns of the query
3. Check that the query is active and inside its time window and days

---

## Data

### How do I back up?

Stop ErrorEngine and copy `instance/errorengine.db` (with the `-wal` and `-shm` files if present) and `.env`.

### Where are the logs?

- **Logs** in the menu: every run of every query
- `logs/errorengine.log`: the server log, a new file every 10 MB with the last 5 kept; **Settings** shows where it is and how much space it takes

### Can I export the errors?

Through the API: `GET /api/errors`, see [API](api-reference.md).

---

## Security

### Can I expose it to the Internet?

Better not. It has a single-user login, but keep it on the internal network or behind a VPN, with HTTPS.

### Are database passwords encrypted?

Not yet (planned for v2.5): they are stored in plain text in the internal database. Protect the `instance/` folder.

### What permissions does the database user need?

Read-only is enough, and recommended: ErrorEngine runs the query you write.
