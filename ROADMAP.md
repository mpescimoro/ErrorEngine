# Roadmap

Planned work, in order. Released versions are in [CHANGELOG.md](CHANGELOG.md).

## Next

- [ ] A row limit per notification, the other rows in a CSV attachment, and one on the rows read: past it, a warning and nothing resolved in that check; the rows in the plain-text part of the emails too, which today has only a summary
- [ ] Migrations of ErrorEngine's database, numbered and run once in order, not only new columns: the indexes and the error identity need them
- [ ] Errors list and logs split into pages, with indexes
- [ ] Make all pages usable on phone screens.
- [ ] Error states: Ignored (out of the errors list, in a menu of its own) and Assigned (someone is on it); both stop the reminders, and an error that resolves and comes back is a normal error again
- [ ] **Pause notifications** for planned work, from the Dashboard and the API: for a set time, up to 3 days, with a bar on every page while it lasts; the queries keep running and each error records that it was not sent; at the end, what is still open is sent, unless it was notified before the pause
- [ ] Routing rules for channels too; the rule tester in the query form (the API has it already)
- [ ] Slack integration
- [ ] Run now and Test do not hold a server thread
- [ ] Regular check times across days and daylight saving changes: today every 7 hours gives gaps of 7, 7, 7 and 3 hours, a weekday window from 8 with **First run at** 9 starts at 9 on Monday and at 8 on the other days, and on the night the clock goes back a query every 15 minutes goes 75 minutes without running
- [ ] Error identity, changed once with a migration of the stored hashes: values containing `|` kept apart, NULL told apart from an empty value, the same number recognized in whatever form a driver returns it, two columns with the same name no longer reduced to one, key fields edited without notifying the open errors again; the same value types from every driver, numbers as numbers and dates in ISO 8601 (the webhook JSON changes)

## Before public launch

- [ ] Setup in the browser at the first start: the password and the encryption key generated and written to `.env`, without commands; an example monitor on a bundled SQLite database
- [ ] Database drivers installed when a connection needs them, without a restart (SQLite stays built in); IBM i also needs Java on the machine
- [ ] Docker image
- [ ] Every dependency pinned to a version, the indirect ones too, so that two installations are the same

## After public launch

- Daily summary of the open errors
- Settings editable in **Settings** instead of `.env`
- Error history with search and export
- Several users with the same rights, and a log of who did what
- API: every read documented, dates in one format, a read-only token
- A Content-Security-Policy header, once the pages no longer have inline scripts

### If asked

- Google Chat, ntfy, signed webhooks
- A notice when an error is resolved
- Reminders that go to another recipient after some hours
- Basic auth and a request body in the form of HTTP sources (the source reads them already)

## Development

- Tests of the pages in a real browser; the drivers tested against real databases (PostgreSQL, MySQL, SQL Server) in CI
- Project guidelines and policies: security reporting (SECURITY.md), contributing (CONTRIBUTE.md) and feature requests (REQUESTS.md).