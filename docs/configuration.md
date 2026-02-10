# Configuration

## Environment Variables

Copy `.env.example` to `.env` and configure:

```env
# Application
SECRET_KEY=change-this-in-production
TIMEZONE=Europe/Rome

# Email (SMTP) - optional
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

## Database Drivers

By default, only SQLite is available. Edit `requirements.txt` and uncomment the drivers you need:

```txt
# oracledb>=2.0.0        # Oracle
# psycopg2-binary>=2.9.0 # PostgreSQL  
# pymysql>=1.1.0         # MySQL / MariaDB
# pymssql>=2.2.0         # SQL Server
# JPype1==1.5.1          # IBM i (AS/400) - requires Java
```

Then restart the application.

## Supported Drivers

| Database | Python Library | Notes |
|----------|----------------|-------|
| SQLite | (built-in) | No installation required |
| Oracle | `oracledb` | Thin mode, no Oracle Client needed |
| PostgreSQL | `psycopg2-binary` | |
| MySQL/MariaDB | `pymysql` | |
| SQL Server | `pymssql` | |
| IBM i (AS/400) | `JPype1` + JT400 | Requires Java 8+, see [ibmi-driver.md](ibmi-driver.md) |

The database type dropdown only shows drivers with installed libraries.

## Database Connections

Connections are managed through the web UI:

1. Go to **Connessioni DB** → **Nuova Connessione**
2. Select database type
3. Enter connection details
4. Click **Testa Connessione** to verify
5. Save

Each query references a connection, allowing you to monitor multiple databases from one instance.

## Creating a Query

1. Go to **Consultazioni** → **Nuova Consultazione**
2. Select data source (database or HTTP)
3. Write your SQL query
4. Click **Testa Query** to verify results
5. Set **key fields** (columns that uniquely identify each error)
6. Add recipients and/or notification channels
7. Configure schedule (interval, time window, active days)
8. Save

## Notification Channels

Go to **Canali Notifica** → **Nuovo Canale**:

| Type | Configuration |
|------|---------------|
| **Webhook** | URL, HTTP method, optional headers |
| **Telegram** | Bot token + Chat ID |
| **Teams** | Incoming Webhook URL |

Assign channels to queries in the query edit form.

## Maintenance

### Automatic Cleanup

Runs daily at 03:00:
- Query logs: 30 days (configurable)
- Email logs: 90 days (configurable)
- Resolved errors: 60 days (configurable)

### Manual Cleanup

**Impostazioni** (Settings) → **Esegui Cleanup Manuale**

## Security Notes

- Use read-only database users when possible
- Protect the UI behind VPN or reverse proxy in production
- Change `SECRET_KEY` before deploying
- Database passwords are stored in plain text — secure your instance directory
