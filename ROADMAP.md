# Roadmap

Planned work, in order. Released versions are in [CHANGELOG.md](CHANGELOG.md).

## Next

- [ ] A row limit per notification, the other rows in a CSV attachment; the rows in the plain-text part of the emails too, which today has only a summary
- [ ] Error states: Ignored (out of the errors list, in a menu of its own) and Assigned (someone is on it); both stop the reminders, and an error that resolves and comes back is a normal error again
- [ ] Errors list and logs split into pages, with indexes
- [ ] Routing rules for channels too; the rule tester in the query form (the API has it already)
- [ ] Slack integration
- [ ] Run now and Test do not hold a server thread
- [ ] Time windows across daylight saving changes: on the night the clock goes back, a query every 15 minutes goes 75 minutes without running
- [ ] Make all pages usable on phone screens.
- [ ] Error identity, changed once with a migration of the stored hashes: values containing `|` kept apart, the same number recognized in whatever form a driver returns it, key fields edited without notifying the open errors again
- [ ] API: failures answered in JSON (a 404 is an HTML page today), a limit on `days`

## Before public launch

- [ ] Setup in the browser at the first start: the password and the encryption key generated and written to `.env`, without commands; an example monitor on a bundled SQLite database
- [ ] Database drivers installed when a connection needs them, without a restart (SQLite stays built in); IBM i also needs Java on the machine
- [ ] Docker image

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