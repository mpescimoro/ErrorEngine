<img src="static/img/logo.svg" align="right" width="60">

# ErrorEngine

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=for-the-badge)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey?style=for-the-badge)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](https://opensource.org/licenses/MIT)

A lightweight, self-hosted error monitoring system with multi-database support, conditional routing, and multi-channel notifications. Originally built for Sage X3 ERP, works with any SQL database or HTTP data source.

![ErrorEngine Dashboard](docs/screenshot-dashboard.png)

---

## Why ErrorEngine?

- Self-hosted alternative to Sentry / Datadog for SQL & ERP systems
- Works directly on databases (scheduled queries - **read-only** please, no agents or SDKs)
- Built for ERP and legacy systems (Oracle, Sage X3, AS400 etc.)
- Full control over data, routing, and retention

### How it works (in short)

ErrorEngine periodically runs user-defined queries against databases or HTTP sources.
Each returned row represents an active error.
Errors are tracked over time until they disappear from query results, at which point they are automatically resolved.
**NOTE**: ErrorEngine is polling-based, not real-time.

---

## Features

### Multi-Database Support
Connect to any combination of databases simultaneously:
- **Oracle** (thin mode — no Oracle Client needed)
- **PostgreSQL**
- **MySQL / MariaDB**
- **Microsoft SQL Server**
- **SQLite**
- **AS/400 (DB2)** (yeah, I know)

### HTTP/REST Data Sources
Monitor REST APIs and HTTP endpoints alongside your databases.

### Smart Error Detection
- Define SQL queries to detect error conditions
- Configurable key fields for unique error identification
- Automatic resolution when errors disappear from query results
- Occurrence counting and first-seen tracking

### Conditional Routing
Route notifications to different recipients based on error content:
```
If WAREHOUSE contains "EU"  →  warehouse-eu@company.com
If PRIORITY equals "HIGH"   →  urgent@company.com, manager@company.com
Default                      →  support@company.com
```

16 operators available: `equals`, `not_equals`, `contains`, `not_contains`, `startswith`, `endswith`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `is_empty`, `is_not_empty`, `regex`, `not_regex`

AND/OR logic, priority ordering, stop-on-match rules.

### Multi-Channel Notifications
- **Email** — SMTP / Exchange / Office 365 with customizable HTML templates
- **Webhook** — JSON payload to any URL (n8n, Make, Zapier, custom)
- **Telegram** — Instant messages via Bot API
- **Microsoft Teams** — Channel messages via Incoming Webhook

### Scheduling & Time Windows
- Per-query interval configuration (minutes)
- Weekday selection (Mon–Sun)
- Time window (e.g., 08:00–18:00 only)
- Reminder system for unresolved errors

### Statistics & Analytics
- Real-time dashboard with error counts and trends
- Dedicated statistics page with timeline charts
- Per-query breakdown with resolution times
- Filterable by time period (7/14/30/90 days)

### Tags & Organization
- Tag queries for logical grouping
- Filter and search by tags
- Visible in statistics and lists

### Automatic Maintenance
- Configurable log retention (query logs, email logs)
- Auto-cleanup of resolved errors
- Manual cleanup option in settings

---

## Screenshots

| Dashboard | Statistiche | Consultazione |
|-----------|-------------|---------------|
| ![Dashboard](docs/screenshot-dashboard.png) | ![Stats](docs/screenshot-stats.png) | ![Query](docs/screenshot-query.png) |

---

## Quick Start

### Requirements
- Python 3.10+
- Database access (read-only user recommended)
- SMTP server for email notifications (optional)
- Database Drivers
**NOTE**: Only install the drivers for the databases you intend to use.
In requirements.txt, lines for Oracle, PostgreSQL, MySQL, SQL Server, and AS/400 are commented out by default.
Remove the # to install the driver(s) you need.

### Installation

```bash
git clone https://github.com/mpescimoro/ErrorEngine.git
cd ErrorEngine
pip install -r requirements.txt
cp .env.example .env    # edit with your settings
python app.py
```

Open `http://localhost:5000` in your browser.

### Docker (optional)

```bash
docker build -t errorengine .
docker run -d -p 5000:5000 --env-file .env errorengine
```

---

## Configuration

### Environment Variables (`.env`)

```env
# Database
Configure DB connections through the web UI (Connessioni DB)
Remember to remove the '#' from the lines for the databases you want to install in requirements.txt

# Application
SECRET_KEY=change-this-in-production
TIMEZONE=Europe/Rome

# Email (SMTP)
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=sender@domain.com
MAIL_PASSWORD=your_password
MAIL_DEFAULT_SENDER=noreply@domain.com

# Retention (days)
LOG_RETENTION_DAYS=30
EMAIL_LOG_RETENTION_DAYS=90
RESOLVED_ERRORS_RETENTION_DAYS=60
```

### Database Connections

Database connections are managed through the web UI:

1. Go to **Connessioni DB** → **Nuova Connessione**
2. Select database type (Oracle, PostgreSQL, MySQL, SQL Server, SQLite)
3. Enter connection details
4. Click **Testa Connessione** to verify
5. Save

Each query references a specific database connection, allowing you to monitor multiple databases from a single instance.

---

## Usage

### Creating a Query

1. Go to **Consultazioni** → **Nuova Consultazione**
2. Select the data source (database connection or HTTP)
3. Write your SQL query (e.g., `SELECT * FROM orders WHERE status = 'ERROR'`)
4. Click **Testa Query** to verify it returns expected results
5. Set **key fields** (columns that uniquely identify each error)
6. Add email recipients and/or notification channels
7. Configure schedule (interval, days, time window)
8. Save

### Setting Up Routing Rules

1. Open a query → **Modifica** → scroll to **Routing**
2. Enable routing
3. Add rules with conditions:
   - Field name (from your query columns)
   - Operator (equals, contains, regex, etc.)
   - Value to match
4. Set recipients for each rule
5. Use priority and stop-on-match for complex logic

### Notification Channels

Go to **Canali Notifica** → **Nuovo Canale**:

| Type | Configuration |
|------|--------------|
| **Webhook** | URL, HTTP method, optional headers |
| **Telegram** | Bot token + Chat ID |
| **Teams** | Incoming Webhook URL |

Then assign channels to queries in the query configuration.

---

## API

All endpoints return JSON.

```bash
# Health check
curl http://localhost:5000/api/health

# List queries
curl http://localhost:5000/api/queries

# Run a query now
curl -X POST http://localhost:5000/api/queries/1/run

# List active errors
curl http://localhost:5000/api/errors

# Resolve an error
curl -X POST http://localhost:5000/api/errors/1/resolve

# Statistics (last 30 days)
curl http://localhost:5000/api/stats/overview?days=30

# Error timeline
curl http://localhost:5000/api/stats/timeline?days=14

# Next scheduled check
curl http://localhost:5000/api/scheduler/next

# Connections
curl http://localhost:5000/api/connections

# Channels
curl http://localhost:5000/api/channels
```

---

## Email Templates

Custom HTML templates for email notifications. Place them in `templates/email/`.

| Query Setting | Behavior |
|---------------|----------|
| *(empty)* | Uses `default.html` |
| `{template_name}` | Uses `template_name.html` |
| Raw `<html>...` | Uses inline HTML directly |

Templates receive the full error context as Jinja2 variables.

---

## Maintenance

### Automatic Cleanup
Runs daily at 03:00:
- Query execution logs: 30 days (configurable)
- Email sending logs: 90 days (configurable)
- Resolved errors: 60 days (configurable)

### Manual Cleanup
**Impostazioni** → **Esegui Cleanup Manuale** removes all logs and resolved errors immediately. Active errors are never touched.

---

## Project Structure

```
ErrorEngine/
├── app.py                  # Application entry point
├── config.py               # Configuration management
├── models.py               # SQLAlchemy models
├── routes.py               # API & page routes
├── scheduler.py            # APScheduler setup
├── monitor_service.py      # Error detection logic
├── email_service.py        # SMTP email sending
├── notification_service.py # Multi-channel dispatch
├── routing_service.py      # Conditional routing engine
├── cleanup_service.py      # Log retention & cleanup
├── data_sources.py         # HTTP / REST data source adapter
├── validators.py           # Input validation
├── db_drivers/             # Database driver abstraction
│   ├── base.py             # Base driver interface
│   ├── oracle.py
│   ├── postgres.py
│   ├── mysql.py
│   ├── sqlserver.py
│   └── sqlite.py
├── templates/              # Jinja2 HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── stats.html
│   ├── query_detail.html
│   ├── query_form.html
│   ├── ...
│   └── email/
│       └── default.html
├── static/
│   ├── css/app.css
│   └── js/app.js
├── tests/
│   ├── test_api.py
│   ├── test_routing_service.py
│   └── test_validators.py
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

---

## Security Notes

- Use read-only DB users whenever possible
- Protect the web UI behind VPN / reverse proxy in production
- Rotate SMTP and webhook credentials regularly
- SECRET_KEY must be changed in production

---

## FAQ

**Why Oracle thin mode?**
No Oracle Client installation needed. Just `pip install oracledb` and connect directly.

**Can I monitor multiple databases at once?**
Yes. Create multiple connections and assign each query to its own connection.

**Emails not sending?**
Check SMTP credentials in `.env`. Office 365 may require app passwords if MFA is enabled.

**Times are wrong?**
Set `TIMEZONE=Europe/Rome` (or your timezone) in `.env`.

**How do I back up?**
The SQLite database is stored as `instance/errorengine.db`. Copy this file to back up all configuration and error history.

---

## Changelog

### v2.1
- Multi-database support (Oracle, PostgreSQL, MySQL, SQL Server, SQLite)
- HTTP/REST data sources
- Notification channels (Webhook, Telegram, Microsoft Teams)
- Tags for query organization
- Dedicated statistics page with charts
- Redesigned pages
- Sidebar reorganization

### v2.0
- Conditional routing engine (16 operators)
- Per-recipient email aggregation
- Error reminders
- Time windows and weekday scheduling
- Cleanup automation

### v1.0
- Initial release
- Oracle monitoring, email notifications
- Basic dashboard

---

MIT License — [mpescimoro](https://github.com/mpescimoro) — `<°))><`
