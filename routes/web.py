"""
Web routes — HTML pages and form handling.
"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import time
from sqlalchemy import inspect
from db_drivers import get_available_drivers, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, QueryLog, EmailLog,
                    DatabaseConnection, NotificationChannel, CHANNEL_SECRETS, SOURCE_CLEAR)
from encryption import decrypt
from routing_service import get_operators_list
from utils import local_day_start_utc, server_log_info
from email_service import email_service, subject_warning
from validators import write_statement_keyword

import json
from urllib3.util import parse_url

logger = logging.getLogger(__name__)
main_bp = Blueprint('main', __name__)


# --- Data for every template ---

@main_bp.app_context_processor
def inject_global_data():
    summary = ErrorRecord.active_summary()
    return {
        'error_count': summary['active'],
        'errors_signature': summary['signature'],
        'driver_labels': DRIVER_LABELS,
    }


# --- Helpers ---

def parse_time(time_str):
    if not time_str:
        return None
    try:
        parts = time_str.split(':')
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def form_choices(query=None) -> tuple:
    """
    Connections and channels offered by the query form: the active ones, plus those the
    query uses even when disabled (a form that does not show them would drop them).
    """
    connections = DatabaseConnection.query.filter_by(is_active=True).all()
    channels = NotificationChannel.query.filter_by(is_active=True).all()
    if query is not None:
        if query.db_connection is not None and query.db_connection not in connections:
            connections.append(query.db_connection)
        channels += [c for c in query.notification_channels if c not in channels]
    return connections, channels


def json_object(text: str):
    """The JSON object typed in a form field ({} when empty); None when the text is not one."""
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# In the forms in place of a saved header value, which does not go back to the browser: sent back, it keeps it
SAVED = '(saved)'


def masked_headers(headers) -> str:
    """The saved headers as the form shows them: the names, every value (saved)."""
    if not isinstance(headers, dict) or not headers:
        return ''
    return json.dumps({name: SAVED for name in headers}, ensure_ascii=False)


def server_of(url) -> tuple:
    """
    Scheme, host and port of an address as requests reads it to connect: a saved secret goes only to the server
    it was typed for. urlsplit reads some addresses otherwise: http://B\\@A/ is A for it, B for requests.
    """
    try:
        parts = parse_url((url or '').strip())
    except ValueError:   # a port that is not a number, an address that cannot be read
        return '', url, None
    scheme = (parts.scheme or '').lower()
    return scheme, (parts.host or '').lower(), parts.port or {'http': 80, 'https': 443}.get(scheme)


def holds_text(value) -> bool:
    """Whether a saved setting outside the fields in clear holds a text, that is a secret."""
    if isinstance(value, str):
        return value != ''
    if isinstance(value, dict):
        value = list(value.values())
    return isinstance(value, list) and any(holds_text(item) for item in value)


def headers_from_form(text: str, kept, problems: list, example: str, elsewhere=()):
    """
    The headers typed, a JSON object, in which (saved) keeps the value saved under that name, encrypted as it
    is: kept has the saved ones, elsewhere the names of those left behind as the address went to another
    server. Text that is not a JSON object: a problem, and the saved headers.
    """
    kept = kept if isinstance(kept, dict) else {}
    headers = json_object(text)
    if headers is None:
        problems.append(f'Headers must be a JSON object, e.g. {example}')
        return kept
    for name, value in headers.items():
        if value != SAVED:
            continue
        if name in kept:
            headers[name] = kept[name]
        elif name in elsewhere:
            problems.append(f'Header {name}: the address is on another server, type its value again')
        else:
            problems.append(f'Header {name}: no value saved under this name, type it')
    return headers


def http_source_config(form, saved: dict, problems: list) -> dict:
    """
    HTTP settings from the query form, over the saved ones (as saved, secrets encrypted): a setting the
    form does not show (e.g. a timeout) stays as it is. The saved token and header values are not in the
    form: left so they stay, for the same server only; Authentication None removes the token.
    """
    saved = saved or {}
    config = dict(saved)
    for key, field in (('url', 'source_url'), ('method', 'source_method'),
                       ('response_path', 'source_response_path'), ('auth_type', 'source_auth_type'),
                       ('api_key_name', 'source_api_key_name'), ('api_key_in', 'source_api_key_in')):
        if field in form:
            config[key] = form.get(field, '').strip()
    if not config.get('api_key_in'):
        config.pop('api_key_in', None)   # in a header, the default
    moved = server_of(config.get('url')) != server_of(saved.get('url'))
    if moved:
        # The saved secrets go only to the server they were typed for: for another one, typed again
        config = {field: value for field, value in config.items() if field in SOURCE_CLEAR or not holds_text(value)}
    if 'source_auth_type' in form and not config['auth_type']:
        config['auth_token'] = ''
    elif form.get('source_auth_token'):
        config['auth_token'] = form['source_auth_token']
    elif moved and saved.get('auth_token'):
        problems.append('Token/Key: the URL is on another server, type it again')
    if 'source_headers' in form:
        saved_headers = saved.get('headers') if isinstance(saved.get('headers'), dict) else {}
        config['headers'] = headers_from_form(form.get('source_headers', '').strip(), {} if moved else saved_headers,
                                              problems, '{"Accept": "application/json"}',
                                              elsewhere=saved_headers if moved else ())
    return config


def form_number(text, lowest: int, highest: int = None):
    """A whole number typed in a form, within the limits; None otherwise."""
    text = (text or '').strip()
    if not text.lstrip('-').isdigit():
        return None
    value = int(text)
    return value if value >= lowest and (highest is None or value <= highest) else None


def statement_that_writes(query) -> tuple:
    """(keyword, connection) when the query reads a database with a statement that can change data."""
    keyword = write_statement_keyword(query.sql_query) if query.source_type != 'http' else None
    if not keyword:
        return None, None
    return keyword, db.session.get(DatabaseConnection, query.db_connection_id) if query.db_connection_id else None


def query_from_form(query, form) -> list:
    """
    The fields of the query form on the query, as typed; returns what is wrong with them. With anything
    wrong the caller saves nothing and shows the form again as sent, so nothing typed is lost.
    """
    problems = []
    saved_config = query.stored_source_config()
    query.name = form.get('name', '').strip()
    if not query.name:
        problems.append('Name: required')
    elif MonitoredQuery.query.filter(MonitoredQuery.name == query.name, MonitoredQuery.id != query.id).first():
        problems.append(f'Name: another query is already called {query.name}')
    query.description = form.get('description', '')
    query.source_type = form.get('source_type', 'database')
    # An HTTP query has no database connection: a leftover one would hide the HTTP source
    connection_id = form.get('db_connection_id', '')
    query.db_connection_id = int(connection_id) if connection_id.isdigit() and query.source_type != 'http' else None
    query.sql_query = form.get('sql_query', '')
    query.key_fields = form.get('key_fields', '')
    if not query.get_key_fields_list():
        problems.append('Key fields: at least one column name')
    query.is_active = form.get('is_active') == 'on'
    if query.source_type == 'http':
        query.set_source_config(http_source_config(form, saved_config, problems))

    query.email_subject = form.get('email_subject', query.email_subject)
    query.email_recipients = form.get('email_recipients', '')
    query.email_template = form.get('email_template', '')

    interval = form_number(form.get('check_interval_value', '15'), 1)
    if interval and form.get('check_interval_unit') == 'hours':
        interval *= 60
    if interval and interval <= 1440:
        query.check_interval_minutes = interval
    else:
        problems.append('Interval: a whole number, from 1 minute to 24 hours')
    query.schedule_start_time = parse_time(form.get('schedule_start_time'))
    query.schedule_end_time = parse_time(form.get('schedule_end_time'))
    days = form.get('schedule_days', '')
    if all(day.strip() in ('1', '2', '3', '4', '5', '6', '7') for day in days.split(',') if day.strip()):
        query.schedule_days = days
    else:
        problems.append('Days: numbers from 1 (Monday) to 7 (Sunday)')
    query.schedule_reference_time = parse_time(form.get('schedule_reference_time'))

    query.reminder_enabled = form.get('reminder_enabled') == 'on'
    every = form_number(form.get('reminder_interval_minutes', '60'), 1)
    count = form_number(form.get('reminder_max_count', '5'), 1)
    if every and count:
        query.reminder_interval_minutes, query.reminder_max_count = every, count
    else:
        problems.append('Reminders: whole numbers from 1')

    # Basic routing, only in the form of a saved query (the rules are managed through the API)
    query.routing_enabled = form.get('routing_enabled') == 'on'
    for field in ('routing_default_recipients', 'routing_aggregation', 'routing_no_match_action'):
        if field in form:
            setattr(query, field, form[field])

    channel_ids = [int(i) for i in form.getlist('notification_channels') if i.isdigit()]
    query.notification_channels = NotificationChannel.query.filter(
        NotificationChannel.id.in_(channel_ids)).all() if channel_ids else []
    query.tags = form.get('tags', '')

    keyword, connection = statement_that_writes(query)
    if keyword and connection is None:
        problems.append(f'The query contains {keyword}, which can change data: choose a connection that allows it')
    elif keyword and not connection.allow_writes:
        problems.append(connection.write_refusal(query.sql_query))
    elif keyword and form.get('confirm_write') != 'on':
        problems.append(f'The query contains {keyword}, which can change data: confirm it below the query to save it')

    return problems


def secret_hint(typed: bool, saved: bool):
    """What a secret field says next to its name: typed in a form that came back, it is to be typed again."""
    return 'typed' if typed else 'saved' if saved else None


def render_query_form(query, form=None):
    """
    The query form: empty for a new query, or the query as saved or as just typed (form). Not the saved token
    and header values: whether a token is saved, and the header names.
    """
    keyword, connection = statement_that_writes(query) if query is not None else (None, None)
    connections, channels = form_choices(query)
    editing = query is not None and inspect(query).persistent
    source_config = query.stored_source_config() if query is not None else {}
    # As in the database: the object holds what was just typed
    text = db.session.query(MonitoredQuery.source_config).filter_by(id=query.id).scalar() if editing else None
    saved = json.loads(text) if text else {}
    same_server = form is None or server_of(form.get('source_url')) == server_of(saved.get('url'))
    headers = form.get('source_headers', '') if form is not None else masked_headers(saved.get('headers'))
    return render_template('query_form.html', query=query, editing=editing, source_config=source_config,
                           token_hint=secret_hint(form is not None and bool(form.get('source_auth_token')),
                                                  bool(saved.get('auth_token')) and same_server),
                           headers=headers,
                           # The confirmation, where the connection allows the statement: asked at every save
                           write_keyword=keyword if connection is not None and connection.allow_writes else None,
                           operators=get_operators_list(), connections=connections, channels=channels,
                           email_on=email_service.is_enabled())


def report_failure(action: str, error: Exception):
    """
    A query not saved or not deleted. Our own checks (ValueError) say on screen what to fix;
    anything else, such as a database error with its SQL and values, goes only in the log.
    """
    db.session.rollback()
    if isinstance(error, ValueError):
        flash(f'Error while {action}: {error}', 'danger')
    else:
        logger.error(f"Query form: error while {action}", exc_info=error)
        flash(f'Error while {action} the query: the details are in the log', 'danger')


def warn_about_the_subject(query):
    """Placeholders that do not exist are sent as written: say so when saving."""
    warning = subject_warning(query.email_subject)
    if warning:
        flash(warning, 'warning')


def channel_names() -> dict:
    """{id: name} of every channel: read once for a page, not once per error."""
    return dict(db.session.query(NotificationChannel.id, NotificationChannel.name))


def active_error_counts() -> dict:
    """Active errors per query: {query_id: count}, with a single query."""
    return dict(
        db.session.query(ErrorRecord.query_id, db.func.count(ErrorRecord.id))
        .filter(ErrorRecord.resolved_at.is_(None))
        .group_by(ErrorRecord.query_id)
        .all()
    )


def problems_first(queries, error_counts: dict) -> list:
    """
    Dashboard order: failing queries, then those with active errors (most first),
    then the ones working, outside their schedule and inactive; by name within each group.
    """
    def key(q):
        errors = error_counts.get(q.id, 0)
        running = q.is_active and not q.connection_disabled()
        if q.is_failing():
            group = 0
        elif errors:
            group = 1
        elif running and q.is_in_schedule():
            group = 2
        elif running:
            group = 3
        else:
            group = 4
        return group, -errors, q.name.lower()
    return sorted(queries, key=key)


# --- Pages ---

@main_bp.route('/')
def dashboard():
    """Main dashboard with an overview of the queries."""
    error_counts = active_error_counts()
    queries = problems_first(MonitoredQuery.query.all(), error_counts)

    # Emails of the local day (from midnight in the configured time zone)
    emails_today = dict(
        db.session.query(EmailLog.status, db.func.count(EmailLog.id))
        .filter(EmailLog.sent_at >= local_day_start_utc())
        .group_by(EmailLog.status)
        .all()
    )

    stats = {
        'total_queries': len(queries),
        'active_queries': sum(1 for q in queries if q.is_active),
        'total_active_errors': sum(error_counts.values()),
        'undelivered_errors': ErrorRecord.query.filter(ErrorRecord.resolved_at.is_(None),
                                                       ErrorRecord.undeliverable.isnot(None)).count(),
        'emails_sent_today': emails_today.get('sent', 0),
        'emails_failed_today': emails_today.get('failed', 0),
    }

    return render_template('dashboard.html', queries=queries, stats=stats, error_counts=error_counts)


@main_bp.route('/queries')
def queries_list():
    tag_filter = request.args.get('tag', '').strip()
    all_queries = MonitoredQuery.query.order_by(MonitoredQuery.name).all()

    # Exact tag: "prod" does not match "preprod"
    if tag_filter:
        queries = [q for q in all_queries
                   if tag_filter.lower() in (t.lower() for t in q.get_tags_list())]
    else:
        queries = all_queries

    # All the unique tags, for the filter
    all_tags = set()
    for q in all_queries:
        all_tags.update(q.get_tags_list())

    return render_template('queries_list.html',
                          queries=queries,
                          all_tags=sorted(all_tags),
                          current_tag=tag_filter,
                          error_counts=active_error_counts())


@main_bp.route('/queries/new', methods=['GET', 'POST'])
def query_create():
    if request.method == 'GET':
        return render_query_form(None)
    query = MonitoredQuery(check_interval_minutes=15, reminder_interval_minutes=60, reminder_max_count=5,
                           email_subject='[ErrorEngine] New errors: {query_name}')
    # Not in the session yet: the channels ticked would try to save it along with them
    with db.session.no_autoflush:
        problems = query_from_form(query, request.form)
        if problems:
            for problem in problems:
                flash(problem, 'danger')
            page = render_query_form(query, request.form)
            db.session.rollback()
            return page
    try:
        db.session.add(query)
        db.session.commit()
        flash(f'Query "{query.name}" created!', 'success')
        warn_about_the_subject(query)
        return redirect(url_for('main.query_detail', query_id=query.id))
    except Exception as e:
        report_failure('creating', e)
        return render_query_form(query, request.form)


@main_bp.route('/queries/<int:query_id>')
def query_detail(query_id):
    query = db.get_or_404(MonitoredQuery, query_id)

    # Active errors: the real count, the 10 most recent on the page (the rest on the Errors page)
    active = ErrorRecord.query.filter_by(query_id=query_id, resolved_at=None)
    active_error_count = active.count()
    active_errors = active.order_by(ErrorRecord.first_seen_at.desc()).limit(10).all()
    undelivered_count = active.filter(ErrorRecord.undeliverable.isnot(None)).count()

    recent_logs = QueryLog.query.filter_by(
        query_id=query_id
    ).order_by(QueryLog.executed_at.desc()).limit(20).all()

    return render_template(
        'query_detail.html',
        query=query,
        active_errors=active_errors,
        active_error_count=active_error_count,
        undelivered_count=undelivered_count,
        channel_names=channel_names(),
        recent_logs=recent_logs,
        operators=get_operators_list()
    )


@main_bp.route('/queries/<int:query_id>/edit', methods=['GET', 'POST'])
def query_edit(query_id):
    query = db.get_or_404(MonitoredQuery, query_id)
    if request.method == 'GET':
        return render_query_form(query)
    # Nothing reaches the database before the commit: with problems, the page shows what was typed,
    # then it is dropped
    with db.session.no_autoflush:
        problems = query_from_form(query, request.form)
        if problems:
            for problem in problems:
                flash(problem, 'danger')
            page = render_query_form(query, request.form)
            db.session.rollback()
            return page
    try:
        db.session.commit()
        flash('Query updated!', 'success')
        warn_about_the_subject(query)
        return redirect(url_for('main.query_detail', query_id=query.id))
    except Exception as e:
        report_failure('updating', e)
        return render_query_form(query, request.form)


@main_bp.route('/queries/<int:query_id>/delete', methods=['POST'])
def query_delete(query_id):
    query = db.get_or_404(MonitoredQuery, query_id)
    name = query.name
    
    try:
        # The email log stays, without the link to the deleted query (a database that
        # enforces foreign keys would otherwise refuse the deletion)
        EmailLog.query.filter_by(query_id=query.id).update({'query_id': None})
        db.session.delete(query)
        db.session.commit()
        flash(f'Query "{name}" deleted.', 'success')
    except Exception as e:
        report_failure('deleting', e)
    
    return redirect(url_for('main.queries_list'))


@main_bp.route('/errors')
def errors_list():
    query_id = request.args.get('query_id', type=int)
    delivery = request.args.get('delivery', '')
    
    errors_query = ErrorRecord.query.filter_by(resolved_at=None)
    if query_id:
        errors_query = errors_query.filter_by(query_id=query_id)

    # Notifications still being retried, and those given up: they stay among the active errors
    pending = ErrorRecord.notify_pending.is_(True)
    failed = ErrorRecord.undeliverable.isnot(None)
    delivery_counts = {'pending': errors_query.filter(pending).count(), 'failed': errors_query.filter(failed).count()}
    if delivery in ('pending', 'failed'):
        errors_query = errors_query.filter(pending if delivery == 'pending' else failed)
    
    errors = errors_query.order_by(ErrorRecord.first_seen_at.desc()).all()

    # Active errors per query: {query_id: number of errors}
    error_counts = dict(
        db.session.query(ErrorRecord.query_id, db.func.count(ErrorRecord.id))
        .filter(ErrorRecord.resolved_at.is_(None))
        .group_by(ErrorRecord.query_id)
        .all()
    )

    # The filter lists only queries with active errors. The selected one stays
    # in the list even if its errors have all been resolved in the meantime.
    filter_ids = set(error_counts)
    if query_id:
        filter_ids.add(query_id)
    queries = (
        MonitoredQuery.query.filter(MonitoredQuery.id.in_(filter_ids))
        .order_by(MonitoredQuery.name).all()
    ) if filter_ids else []

    return render_template('errors_list.html', errors=errors, queries=queries,
                          selected_query_id=query_id, error_counts=error_counts,
                          total_errors=sum(error_counts.values()),
                          delivery_counts=delivery_counts, selected_delivery=delivery,
                          channel_names=channel_names())


@main_bp.route('/logs')
def logs_list():
    query_id = request.args.get('query_id', type=int)
    
    logs_query = QueryLog.query
    if query_id:
        logs_query = logs_query.filter_by(query_id=query_id)
    
    logs = logs_query.order_by(QueryLog.executed_at.desc()).limit(100).all()
    queries = MonitoredQuery.query.order_by(MonitoredQuery.name).all()
    
    return render_template('logs_list.html', logs=logs, queries=queries, 
                          selected_query_id=query_id)


@main_bp.route('/stats')
def stats_page():
    """Statistics page; ?query_id= opens the chart on that query."""
    queries = MonitoredQuery.query.order_by(db.func.lower(MonitoredQuery.name)).all()
    return render_template('stats.html', queries=queries,
                           selected_query_id=request.args.get('query_id', type=int))


@main_bp.route('/settings')
def settings():
    """Settings page and connection tests."""
    from scheduler import get_scheduler_status
    from version import __version__
    failing_queries = [q for q in MonitoredQuery.query.filter(MonitoredQuery.consecutive_failures > 0)
                       .order_by(MonitoredQuery.name) if q.is_failing()]

    # Internal database actually in use (never user names or passwords)
    url = db.engine.url
    engine_names = {'sqlite': 'SQLite', 'postgresql': 'PostgreSQL', 'mysql': 'MySQL',
                    'mariadb': 'MariaDB', 'mssql': 'SQL Server', 'oracle': 'Oracle'}
    database = {
        'engine': engine_names.get(db.engine.dialect.name, db.engine.dialect.name),
        'location': url.database if db.engine.dialect.name == 'sqlite'
                    else '/'.join(part for part in (url.host, url.database) if part),
    }
    return render_template('settings.html', scheduler_status=get_scheduler_status(),
                           failing_queries=failing_queries, version=__version__,
                           database=database, server_log=server_log_info(),
                           email_status=email_service.status())


# --- Database connections ---

@main_bp.route('/connections')
def connections_list():
    connections = DatabaseConnection.query.order_by(DatabaseConnection.name).all()
    return render_template('connections_list.html', 
                           connections=connections,
                           driver_labels=DRIVER_LABELS)


def database_server(conn) -> tuple:
    """Host and port of a connection: its saved password goes only there."""
    return (conn.host or '').strip().lower(), conn.port


def connection_from_form(conn, form) -> list:
    """The fields of the connection form on the connection, as typed; returns what is wrong with them."""
    problems = []
    saved_server = database_server(conn)
    conn.name = form.get('name', '').strip()
    if not conn.name:
        problems.append('Name: required')
    conn.db_type = form.get('db_type', '')
    if conn.db_type not in DRIVER_LABELS:
        problems.append('Type: one of those in the list')
    conn.host = form.get('host')
    port = form.get('port', '').strip()
    if not port:
        conn.port = None
    elif form_number(port, 1, 65535):
        conn.port = int(port)
    else:
        problems.append('Port: a number from 1 to 65535, or empty for the usual one')
    conn.database = form.get('database')
    conn.username = form.get('username')
    # Editing, an empty password keeps the saved one, for the same server only
    if form.get('password'):
        conn.password = form['password']
    elif conn._password and database_server(conn) != saved_server:
        problems.append('Password: the server changed, type it again')
    conn.is_active = form.get('is_active') == 'on'
    conn.allow_writes = form.get('allow_writes') == 'on'
    return problems


def render_connection_form(conn, form=None):
    """The connection form: empty, or the connection as saved or as just typed (form); never its password."""
    editing = conn is not None and inspect(conn).persistent
    saved = None
    if editing:
        # As in the database: the object holds what was just typed
        saved = db.session.query(DatabaseConnection.host, DatabaseConnection.port,
                                 DatabaseConnection._password).filter_by(id=conn.id).one()
    keeps = (saved is not None and bool(saved[2])
             and ((saved[0] or '').strip().lower(), saved[1]) == database_server(conn))
    return render_template('connection_form.html', connection=conn, editing=editing, drivers=get_available_drivers(),
                           password_hint=secret_hint(form is not None and bool(form.get('password')), keeps))


@main_bp.route('/connections/new', methods=['GET', 'POST'])
def connection_create():
    if request.method == 'GET':
        return render_connection_form(None)
    conn = DatabaseConnection()
    problems = connection_from_form(conn, request.form)
    if problems:
        for problem in problems:
            flash(problem, 'danger')
        return render_connection_form(conn, request.form)
    db.session.add(conn)
    db.session.commit()
    flash('Connection created!', 'success')
    return redirect(url_for('main.connections_list'))


@main_bp.route('/connections/<int:conn_id>/edit', methods=['GET', 'POST'])
def connection_edit(conn_id):
    conn = db.get_or_404(DatabaseConnection, conn_id)
    if request.method == 'GET':
        return render_connection_form(conn)
    with db.session.no_autoflush:
        problems = connection_from_form(conn, request.form)
        if problems:
            for problem in problems:
                flash(problem, 'danger')
            page = render_connection_form(conn, request.form)
            db.session.rollback()
            return page
    db.session.commit()
    flash('Connection updated!', 'success')
    return redirect(url_for('main.connections_list'))


@main_bp.route('/connections/<int:conn_id>/delete', methods=['POST'])
def connection_delete(conn_id):
    conn = db.get_or_404(DatabaseConnection, conn_id)
    
    if conn.queries.count() > 0:
        flash(f'Cannot delete: the connection is used by {conn.queries.count()} queries.', 'danger')
        return redirect(url_for('main.connections_list'))
    
    db.session.delete(conn)
    db.session.commit()
    flash('Connection deleted.', 'success')
    return redirect(url_for('main.connections_list'))


# --- Notification channels ---

CHANNEL_TYPES = {
    'webhook': 'Generic webhook',
    'telegram': 'Telegram',
    'teams': 'Microsoft Teams'
}

@main_bp.route('/channels')
def channels_list():
    channels = NotificationChannel.query.order_by(NotificationChannel.name).all()
    return render_template('channels_list.html', channels=channels, channel_types=CHANNEL_TYPES)


def saved_server(encrypted_url):
    """The server of a saved address; None when it cannot be read."""
    try:
        return server_of(decrypt(encrypted_url))
    except ValueError:
        return None


def channel_from_form(channel, form) -> list:
    """
    The fields of the channel form on the channel, as typed; returns what is wrong with them. The saved
    addresses, token and header values are not in the form: left empty they stay, (saved) keeps a header
    for the same server only.
    """
    problems = []
    # The saved secrets count for the same type only
    kept = channel.stored_config() if channel.config and channel.channel_type == form.get('channel_type') else {}
    channel.name = form.get('name', '').strip()
    if not channel.name:
        problems.append('Name: required')
    channel.channel_type = form.get('channel_type', '')
    channel.is_active = form.get('is_active') == 'on'
    # A secret field left empty keeps the saved value, encrypted as it is
    if channel.channel_type == 'webhook':
        typed = form.get('webhook_url', '').strip()
        config = {'url': typed or kept.get('url', ''), 'method': form.get('webhook_method', 'POST')}
        if not config['url'] or (typed and not typed.startswith(('http://', 'https://'))):
            problems.append('URL: an address that starts with http:// or https://')
        saved_headers = kept.get('headers') if isinstance(kept.get('headers'), dict) else {}
        # A new address on another server: the saved header values do not go there
        moved = bool(typed) and server_of(typed) != saved_server(kept.get('url'))
        config['headers'] = headers_from_form(form.get('webhook_headers', '').strip(),
                                              {} if moved else saved_headers, problems,
                                              '{"Authorization": "Bearer xxx"}',
                                              elsewhere=saved_headers if moved else ())
    elif channel.channel_type == 'telegram':
        config = {'bot_token': form.get('telegram_bot_token', '').strip() or kept.get('bot_token', ''),
                  'chat_id': form.get('telegram_chat_id', '').strip()}
        if not (config['bot_token'] and config['chat_id']):
            problems.append('Telegram: bot token and chat ID are both required')
    elif channel.channel_type == 'teams':
        typed = form.get('teams_webhook_url', '').strip()
        config = {'webhook_url': typed or kept.get('webhook_url', '')}
        if not config['webhook_url'] or (typed and not typed.startswith(('http://', 'https://'))):
            problems.append('Teams: the webhook URL, which starts with https://')
    else:
        config = {}
        problems.append('Type: one of those in the list')
    channel.set_config(config)
    return problems


# The field of the form of each secret of a channel that has one
SECRET_INPUTS = {'url': 'webhook_url', 'bot_token': 'telegram_bot_token', 'webhook_url': 'teams_webhook_url'}


def render_channel_form(channel, form=None):
    """
    The channel form: empty, or the channel as saved or as just typed (form). Not the saved addresses, token
    and header values: which of them are saved, and the header names.
    """
    editing = channel is not None and inspect(channel).persistent
    stored = channel.stored_config() if channel is not None and channel.config else {}
    saved_type, saved = None, {}
    if editing:
        # As in the database: the object holds what was just typed
        saved_type, text = db.session.query(NotificationChannel.channel_type,
                                            NotificationChannel.config).filter_by(id=channel.id).one()
        saved = json.loads(text) if text else {}
    hints = {field: secret_hint(form is not None and bool(form.get(SECRET_INPUTS[field], '').strip()),
                                saved_type == kind and bool(saved.get(field)))
             for kind, fields in CHANNEL_SECRETS.items() for field in fields if field in SECRET_INPUTS}
    if form is not None:
        headers = form.get('webhook_headers', '')
    else:
        headers = masked_headers(saved.get('headers')) if saved_type == 'webhook' else ''
    return render_template('channel_form.html', channel=channel, channel_types=CHANNEL_TYPES, editing=editing,
                           stored=stored, hints=hints, headers=headers)


@main_bp.route('/channels/new', methods=['GET', 'POST'])
def channel_create():
    if request.method == 'GET':
        return render_channel_form(None)
    channel = NotificationChannel()
    problems = channel_from_form(channel, request.form)
    if problems:
        for problem in problems:
            flash(problem, 'danger')
        return render_channel_form(channel, request.form)
    db.session.add(channel)
    db.session.commit()
    flash('Channel created!', 'success')
    return redirect(url_for('main.channels_list'))


@main_bp.route('/channels/<int:channel_id>/edit', methods=['GET', 'POST'])
def channel_edit(channel_id):
    channel = db.get_or_404(NotificationChannel, channel_id)
    if request.method == 'GET':
        return render_channel_form(channel)
    with db.session.no_autoflush:
        problems = channel_from_form(channel, request.form)
        if problems:
            for problem in problems:
                flash(problem, 'danger')
            page = render_channel_form(channel, request.form)
            db.session.rollback()
            return page
    db.session.commit()
    flash('Channel updated!', 'success')
    return redirect(url_for('main.channels_list'))


@main_bp.route('/channels/<int:channel_id>/delete', methods=['POST'])
def channel_delete(channel_id):
    channel = db.get_or_404(NotificationChannel, channel_id)
    if channel.queries:
        flash(f'Cannot delete: the channel is used by {len(channel.queries)} queries.', 'danger')
        return redirect(url_for('main.channels_list'))
    
    db.session.delete(channel)
    db.session.commit()
    flash('Channel deleted.', 'success')
    return redirect(url_for('main.channels_list'))
