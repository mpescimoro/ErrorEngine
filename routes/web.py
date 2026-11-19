"""
Web routes — HTML pages and form handling.
"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import time
from sqlalchemy import inspect
from db_drivers import get_available_drivers, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, QueryLog, EmailLog,
                    DatabaseConnection, NotificationChannel)
from routing_service import get_operators_list
from utils import local_day_start_utc, server_log_info
from email_service import email_service, subject_warning
from validators import write_statement_keyword

import json

logger = logging.getLogger(__name__)
main_bp = Blueprint('main', __name__)


# --- Data for every template ---

@main_bp.app_context_processor
def inject_global_data():
    """Injects global data into every template."""
    summary = ErrorRecord.active_summary()
    return {
        'error_count': summary['active'],
        'errors_signature': summary['signature'],
        'driver_labels': DRIVER_LABELS,
    }


# --- Helpers ---

def parse_time(time_str):
    """Converts an HH:MM string to a time object."""
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


def http_source_config(form, saved: dict) -> dict:
    """
    HTTP settings from the query form, over the saved ones: a setting the form
    does not show (e.g. a timeout) stays as it is.
    """
    config = dict(saved or {})
    for key, field in (('url', 'source_url'), ('method', 'source_method'),
                       ('response_path', 'source_response_path'), ('auth_type', 'source_auth_type'),
                       ('api_key_name', 'source_api_key_name')):
        if field in form:
            config[key] = form.get(field, '').strip()
    if 'source_auth_token' in form:
        config['auth_token'] = form.get('source_auth_token', '')
    if 'source_headers' in form:
        text = form.get('source_headers', '').strip()
        # Not a JSON object: the text as typed, for the form to show it again with the problem
        headers = json_object(text)
        config['headers'] = headers if headers is not None else text
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
    saved_config = query.get_source_config()
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
        query.set_source_config(http_source_config(form, saved_config))
        if not isinstance(query.get_source_config().get('headers', {}), dict):
            problems.append('Headers must be a JSON object, e.g. {"Accept": "application/json"}')

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

    if problems and query.source_type == 'http':
        # Shown again as typed, but a token typed does not go back to the browser
        config = query.get_source_config()
        config['auth_token'] = saved_config.get('auth_token', '')
        query.set_source_config(config)
    return problems


def render_query_form(query):
    """The query form: empty for a new query, or the query as saved or as just typed."""
    keyword, connection = statement_that_writes(query) if query is not None else (None, None)
    connections, channels = form_choices(query)
    return render_template('query_form.html', query=query,
                           editing=query is not None and inspect(query).persistent,
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

    # General statistics
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
    """Creates a new query."""
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
            page = render_query_form(query)
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
        return render_query_form(query)


@main_bp.route('/queries/<int:query_id>')
def query_detail(query_id):
    """Query detail."""
    query = db.get_or_404(MonitoredQuery, query_id)

    # Active errors: the real count, the 10 most recent on the page (the rest on the Errors page)
    active = ErrorRecord.query.filter_by(query_id=query_id, resolved_at=None)
    active_error_count = active.count()
    active_errors = active.order_by(ErrorRecord.first_seen_at.desc()).limit(10).all()
    undelivered_count = active.filter(ErrorRecord.undeliverable.isnot(None)).count()

    # Latest logs
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
    """Edits an existing query."""
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
            page = render_query_form(query)
            db.session.rollback()
            return page
    try:
        db.session.commit()
        flash('Query updated!', 'success')
        warn_about_the_subject(query)
        return redirect(url_for('main.query_detail', query_id=query.id))
    except Exception as e:
        report_failure('updating', e)
        return render_query_form(query)


@main_bp.route('/queries/<int:query_id>/delete', methods=['POST'])
def query_delete(query_id):
    """Deletes a query."""
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
    """List of all active errors."""
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
    """Run logs."""
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
    """List of database connections."""
    connections = DatabaseConnection.query.order_by(DatabaseConnection.name).all()
    return render_template('connections_list.html', 
                           connections=connections,
                           driver_labels=DRIVER_LABELS)


def connection_from_form(conn, form) -> list:
    """The fields of the connection form on the connection, as typed; returns what is wrong with them."""
    problems = []
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
    # Editing, an empty password keeps the saved one
    if form.get('password'):
        conn.password = form['password']
    conn.is_active = form.get('is_active') == 'on'
    conn.allow_writes = form.get('allow_writes') == 'on'
    return problems


def render_connection_form(conn):
    """The connection form: empty, or the connection as saved or as just typed (never its password)."""
    return render_template('connection_form.html', connection=conn,
                           editing=conn is not None and inspect(conn).persistent, drivers=get_available_drivers())


@main_bp.route('/connections/new', methods=['GET', 'POST'])
def connection_create():
    """Creates a new connection."""
    if request.method == 'GET':
        return render_connection_form(None)
    conn = DatabaseConnection()
    problems = connection_from_form(conn, request.form)
    if problems:
        for problem in problems:
            flash(problem, 'danger')
        return render_connection_form(conn)
    db.session.add(conn)
    db.session.commit()
    flash('Connection created!', 'success')
    return redirect(url_for('main.connections_list'))


@main_bp.route('/connections/<int:conn_id>/edit', methods=['GET', 'POST'])
def connection_edit(conn_id):
    """Edits an existing connection."""
    conn = db.get_or_404(DatabaseConnection, conn_id)
    if request.method == 'GET':
        return render_connection_form(conn)
    with db.session.no_autoflush:
        problems = connection_from_form(conn, request.form)
        if problems:
            for problem in problems:
                flash(problem, 'danger')
            page = render_connection_form(conn)
            db.session.rollback()
            return page
    db.session.commit()
    flash('Connection updated!', 'success')
    return redirect(url_for('main.connections_list'))


@main_bp.route('/connections/<int:conn_id>/delete', methods=['POST'])
def connection_delete(conn_id):
    """Deletes a connection."""
    conn = db.get_or_404(DatabaseConnection, conn_id)
    
    # Is it used by any query
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


# Settings of a channel that let anyone send to it: a form with problems does not send back those typed
CHANNEL_SECRETS = {'webhook': 'url', 'telegram': 'bot_token', 'teams': 'webhook_url'}


def channel_from_form(channel, form) -> list:
    """The fields of the channel form on the channel, as typed; returns what is wrong with them."""
    problems = []
    saved_type, saved = channel.channel_type, (channel.get_config() if channel.config else {})
    channel.name = form.get('name', '').strip()
    if not channel.name:
        problems.append('Name: required')
    channel.channel_type = form.get('channel_type', '')
    channel.is_active = form.get('is_active') == 'on'
    if channel.channel_type == 'webhook':
        config = {'url': form.get('webhook_url', '').strip(), 'method': form.get('webhook_method', 'POST')}
        if not config['url'].startswith(('http://', 'https://')):
            problems.append('URL: an address that starts with http:// or https://')
        headers = json_object(form.get('webhook_headers', '').strip())
        if headers is not None:
            config['headers'] = headers
        else:
            config['headers'] = form.get('webhook_headers', '').strip()
            problems.append('Headers must be a JSON object, e.g. {"Authorization": "Bearer xxx"}')
    elif channel.channel_type == 'telegram':
        config = {'bot_token': form.get('telegram_bot_token', '').strip(),
                  'chat_id': form.get('telegram_chat_id', '').strip()}
        if not (config['bot_token'] and config['chat_id']):
            problems.append('Telegram: bot token and chat ID are both required')
    elif channel.channel_type == 'teams':
        config = {'webhook_url': form.get('teams_webhook_url', '').strip()}
        if not config['webhook_url'].startswith(('http://', 'https://')):
            problems.append('Teams: the webhook URL, which starts with https://')
    else:
        config = {}
        problems.append('Type: one of those in the list')
    secret = CHANNEL_SECRETS.get(channel.channel_type)
    if problems and secret:
        # As saved when editing, as GET shows it; empty for a new channel
        config[secret] = saved.get(secret, '') if saved_type == channel.channel_type else ''
    channel.set_config(config)
    return problems


def render_channel_form(channel):
    """The channel form: empty, or the channel as saved or as just typed (not the secrets typed)."""
    return render_template('channel_form.html', channel=channel, channel_types=CHANNEL_TYPES,
                           editing=channel is not None and inspect(channel).persistent)


@main_bp.route('/channels/new', methods=['GET', 'POST'])
def channel_create():
    if request.method == 'GET':
        return render_channel_form(None)
    channel = NotificationChannel()
    problems = channel_from_form(channel, request.form)
    if problems:
        for problem in problems:
            flash(problem, 'danger')
        return render_channel_form(channel)
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
            page = render_channel_form(channel)
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
