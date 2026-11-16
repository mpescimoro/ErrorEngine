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

### What if a notification cannot be delivered?

It is tried again after 1, 5, 15 and 60 minutes, only towards the recipients and channels that did not get it: 5 attempts in all (`NOTIFY_ATTEMPTS`). Then the error stays among the active ones and shows **Not delivered**, to whom and why; the errors page lists them with its filter, and the dashboard counts them.

An address the mail server refuses counts as not reached too, with the server's reason, and gets the same attempts, also when the server takes the other addresses of the email. ErrorEngine sees only the refusals given while it sends: a refusal that comes back later, as an email to the sender, does not reach it. That is always the case with Microsoft 365, and with mail servers that check the addresses after taking the email: look in the sender's mailbox.

### What does Resolve do?

It closes the error by hand. If the query still returns that row, at the next check it comes back as a new error and is notified again: resolve an error when its cause is fixed. Errors the query no longer returns are resolved by themselves.

---

## Troubleshooting

### ErrorEngine does not start

Look at the console or at `logs/errorengine.log`: without `SECRET_KEY` in `.env` it stops right away and says so. See [Configuration](configuration.md#sign-in).

### Nobody can sign in

The sign-in page says what is missing: `ERRORENGINE_PASSWORD_HASH`, or a `SECRET_KEY` that is not the example one. See [Configuration](configuration.md#sign-in) and restart ErrorEngine.

### Emails are not sent

1. **Settings** shows how emails go out; **Send test email** tries it and shows the error
2. Check the email settings in `.env`, see [Email](configuration.md#email)
3. "certificate verify failed": the SMTP server certificate is checked. Put in `MAIL_SERVER` the name the certificate is issued to, not an IP address; for an internal server with its own certificate, set `MAIL_CA_FILE` to its CA (PEM file)
4. Microsoft 365: SMTP with a password is turned off by default from the end of December 2026. Send through Microsoft Graph instead, see [Microsoft 365](configuration.md#microsoft-365)
5. Gmail: use an app password, which needs 2-Step Verification
6. Look at the errors page and at `logs/errorengine.log`, one line per delivery attempt: notifications that fail are tried again after 1, 5, 15 and 60 minutes; then the error shows **Not delivered**

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

The active ones, through the API: `GET /api/errors`, see [API](api-reference.md). Exporting the resolved ones is planned, see the [roadmap](../ROADMAP.md).

---

## Security

### Can I expose it to the Internet?

Better not. It has a single-user login, but keep it on the internal network or behind a VPN, with HTTPS.

### Are database passwords encrypted?

Not yet (planned for v2.5): they are stored in plain text in the internal database. Protect the `instance/` folder.

### What permissions does the database user need?

Read-only is enough, and recommended: ErrorEngine runs the query you write.
