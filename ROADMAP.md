# ErrorEngine — Roadmap

> Last updated: February 2025

---

## Released

**v2.1.2** — Test suite & bug fixes

- Fix validate_key_fields missing return value, is_not_empty operator, api_query_status 404
- Test suite expanded from 85 to 153 tests (models, monitor_service, cleanup, routing)
- Remove old  `routes.py`

**v2.1.1** — Bug fixes & code quality
- Fix `MonitoredQuery.__repr__` indentation (was unreachable)
- Add missing `__repr__` to `DatabaseConnection`
- Replace deprecated `datetime.utcnow()` with centralized `get_utc_now()`
- Split `routes.py` into `routes/web.py` and `routes/api.py`
- Fix missing `logger` import in API routes
- Remove unused imports

**v2.1** — Multi-database, HTTP sources, notification channels, tags, statistics page

**v2.0** — Conditional routing, reminders, time windows, cleanup automation

**v1.0** — Initial release

---

## Planned

### v2.2 — English Codebase & i18n

- [ ] Translate all comments, docstrings, log messages, and UI to English
- [ ] Rename Italian variables and flash messages
- [ ] Add Flask-Babel with string extraction to `.po` catalogs
- [ ] Language selector in UI (English default, Italian as first translation)
- [ ] Move Italian docs to `docs/it/`

### v2.3 — Security & Authentication

- [ ] Encrypt database passwords and channel secrets at rest (Fernet)
- [ ] Master key mechanism (env variable or first-run setup)
- [ ] User login with Flask-Login and first-run admin setup
- [ ] API key authentication for REST endpoints
- [ ] CSRF protection, rate limiting, security headers
- [ ] Audit log for configuration changes

### v2.4 — Web-Based Configuration

- [ ] Move `.env` settings to DB with Settings UI page
- [ ] Keep `.env` as optional override (env vars > DB > defaults)
- [ ] Auto-detect installed database drivers with install/uninstall from UI
- [ ] First-run setup wizard (admin account → SMTP → timezone)

### v2.5 — Docker & Deployment

- [ ] Official Dockerfile and docker-compose.yml
- [ ] Health check endpoint with DB and scheduler status
- [ ] Startup migration system for schema changes
- [ ] Deployment docs (Docker, systemd, Windows Service)

### v3.0 — Future Ideas

- Real-time dashboard (WebSocket/SSE)
- Configuration export/import (JSON/YAML)
- Error grouping, trends, and spike detection
- Role-based access control
- Plugin system for custom channels and sources
- Prometheus/Grafana metrics endpoint

---

## Contributing

Contributions welcome — open an issue on
[GitHub](https://github.com/mpescimoro/errorengine) to discuss before submitting a PR.


ErrorEngine — Roadmap

Last updated: February 2025