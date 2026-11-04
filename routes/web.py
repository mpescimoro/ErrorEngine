"""
Web routes — HTML pages and form handling.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import time
from db_drivers import get_available_drivers, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, QueryLog, EmailLog,
                    DatabaseConnection, NotificationChannel)
from routing_service import get_operators_list
from utils import local_day_start_utc, server_log_info
from email_service import email_service

import json

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


def format_time(t):
    """Formats a time object as an HH:MM string."""
    if not t:
        return ''
    return t.strftime('%H:%M')


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
        if q.is_active and q.consecutive_failures:
            group = 0
        elif errors:
            group = 1
        elif q.is_active and q.is_in_schedule():
            group = 2
        elif q.is_active:
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
    if request.method == 'POST':
        try:
            # Basic data
            source_type = request.form.get('source_type', 'database')
            db_conn_id = request.form.get('db_connection_id')
            
            query = MonitoredQuery(
                name=request.form['name'],
                description=request.form.get('description', ''),
                source_type=source_type,
                db_connection_id=int(db_conn_id) if db_conn_id else None,
                sql_query=request.form.get('sql_query', ''),
                key_fields=request.form['key_fields'],
                is_active=request.form.get('is_active') == 'on'
            )
            
            # Source config for HTTP
            if source_type == 'http':
                source_config = {
                    'url': request.form.get('source_url', ''),
                    'method': request.form.get('source_method', 'GET'),
                    'headers': json.loads(request.form.get('source_headers', '{}') or '{}'),
                    'response_path': request.form.get('source_response_path', ''),
                    'auth_type': request.form.get('source_auth_type', ''),
                    'auth_token': request.form.get('source_auth_token', ''),
                }
                query.set_source_config(source_config)
            
            # Email
            query.email_subject = request.form.get('email_subject', '[ErrorEngine] New errors: {query_name}')
            query.email_recipients = request.form.get('email_recipients', '')
            query.email_template = request.form.get('email_template', '')
            
            # Scheduling
            interval_value = int(request.form.get('check_interval_value', 15))
            interval_unit = request.form.get('check_interval_unit', 'minutes')
            if interval_unit == 'hours':
                query.check_interval_minutes = interval_value * 60
            else:  # minutes
                query.check_interval_minutes = interval_value
            # Limit to 24 h at most
            if query.check_interval_minutes > 1440:
                query.check_interval_minutes = 1440
            query.schedule_start_time = parse_time(request.form.get('schedule_start_time'))
            query.schedule_end_time = parse_time(request.form.get('schedule_end_time'))
            query.schedule_days = request.form.get('schedule_days', '')
            query.schedule_reference_time = parse_time(request.form.get('schedule_reference_time'))
            
            # Reminder
            query.reminder_enabled = request.form.get('reminder_enabled') == 'on'
            query.reminder_interval_minutes = int(request.form.get('reminder_interval_minutes', 60))
            query.reminder_max_count = int(request.form.get('reminder_max_count', 5))
            
            query.tags = request.form.get('tags', '')

            db.session.add(query)
            db.session.flush()  # To get the ID
            
            channel_ids = request.form.getlist('notification_channels')
            if channel_ids:
                channels = NotificationChannel.query.filter(NotificationChannel.id.in_(channel_ids)).all()
                query.notification_channels = channels
            
            db.session.commit()
            flash(f'Query "{query.name}" created!', 'success')
            return redirect(url_for('main.query_detail', query_id=query.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error while creating: {str(e)}', 'danger')
    
    return render_template('query_form.html', 
                        query=None, 
                          operators=get_operators_list(),
                        connections=DatabaseConnection.query.filter_by(is_active=True).all(),
                        channels=NotificationChannel.query.filter_by(is_active=True).all())


@main_bp.route('/queries/<int:query_id>')
def query_detail(query_id):
    """Query detail."""
    query = MonitoredQuery.query.get_or_404(query_id)

    # Active errors: the real count, the 10 most recent on the page (the rest on the Errors page)
    active = ErrorRecord.query.filter_by(query_id=query_id, resolved_at=None)
    active_error_count = active.count()
    active_errors = active.order_by(ErrorRecord.first_seen_at.desc()).limit(10).all()

    # Latest logs
    recent_logs = QueryLog.query.filter_by(
        query_id=query_id
    ).order_by(QueryLog.executed_at.desc()).limit(20).all()

    return render_template(
        'query_detail.html',
        query=query,
        active_errors=active_errors,
        active_error_count=active_error_count,
        recent_logs=recent_logs,
        operators=get_operators_list()
    )


@main_bp.route('/queries/<int:query_id>/edit', methods=['GET', 'POST'])
def query_edit(query_id):
    """Edits an existing query."""
    query = MonitoredQuery.query.get_or_404(query_id)
    
    if request.method == 'POST':
        try:
            # Basic data
            query.name = request.form['name']
            query.description = request.form.get('description', '')
            query.source_type = request.form.get('source_type', 'database')
            
            # Database connection
            db_conn_id = request.form.get('db_connection_id')
            query.db_connection_id = int(db_conn_id) if db_conn_id else None
            
            query.sql_query = request.form.get('sql_query', '')
            query.key_fields = request.form['key_fields']
            query.is_active = request.form.get('is_active') == 'on'
            
            # Source config for HTTP
            if query.source_type == 'http':
                source_config = {
                    'url': request.form.get('source_url', ''),
                    'method': request.form.get('source_method', 'GET'),
                    'headers': json.loads(request.form.get('source_headers', '{}') or '{}'),
                    'response_path': request.form.get('source_response_path', ''),
                    'auth_type': request.form.get('source_auth_type', ''),
                    'auth_token': request.form.get('source_auth_token', ''),
                }
                query.set_source_config(source_config)
            
            # Email
            query.email_subject = request.form.get('email_subject', query.email_subject)
            query.email_recipients = request.form.get('email_recipients', '')
            query.email_template = request.form.get('email_template', '')
            
            # Scheduling
            interval_value = int(request.form.get('check_interval_value', 15))
            interval_unit = request.form.get('check_interval_unit', 'minutes')
            if interval_unit == 'hours':
                query.check_interval_minutes = interval_value * 60
            else:  # minutes
                query.check_interval_minutes = interval_value
            # Limit to 24 h at most
            if query.check_interval_minutes > 1440:
                query.check_interval_minutes = 1440
            query.schedule_start_time = parse_time(request.form.get('schedule_start_time'))
            query.schedule_end_time = parse_time(request.form.get('schedule_end_time'))
            query.schedule_days = request.form.get('schedule_days', '')
            query.schedule_reference_time = parse_time(request.form.get('schedule_reference_time'))

            # Reminder
            query.reminder_enabled = request.form.get('reminder_enabled') == 'on'
            query.reminder_interval_minutes = int(request.form.get('reminder_interval_minutes', 60))
            query.reminder_max_count = int(request.form.get('reminder_max_count', 5))
            
            # Basic routing (the rules are managed through the API)
            query.routing_enabled = request.form.get('routing_enabled') == 'on'
            query.routing_default_recipients = request.form.get('routing_default_recipients', '')
            query.routing_aggregation = request.form.get('routing_aggregation', 'per_recipient')
            query.routing_no_match_action = request.form.get('routing_no_match_action', 'send_default')
            
            channel_ids = request.form.getlist('notification_channels')
            query.notification_channels = NotificationChannel.query.filter(NotificationChannel.id.in_(channel_ids)).all() if channel_ids else []
            
            query.tags = request.form.get('tags', '')
            
            db.session.commit()
            flash('Query updated!', 'success')
            return redirect(url_for('main.query_detail', query_id=query.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error while updating: {str(e)}', 'danger')
    
    return render_template('query_form.html', query=query, action='edit',
                          operators=get_operators_list(),
                          connections=DatabaseConnection.query.filter_by(is_active=True).all(),
                          channels=NotificationChannel.query.filter_by(is_active=True).all())


@main_bp.route('/queries/<int:query_id>/delete', methods=['POST'])
def query_delete(query_id):
    """Deletes a query."""
    query = MonitoredQuery.query.get_or_404(query_id)
    name = query.name
    
    try:
        # The email log stays, without the link to the deleted query (a database that
        # enforces foreign keys would otherwise refuse the deletion)
        EmailLog.query.filter_by(query_id=query.id).update({'query_id': None})
        db.session.delete(query)
        db.session.commit()
        flash(f'Query "{name}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error while deleting: {str(e)}', 'danger')
    
    return redirect(url_for('main.queries_list'))


@main_bp.route('/errors')
def errors_list():
    """List of all active errors."""
    query_id = request.args.get('query_id', type=int)
    
    errors_query = ErrorRecord.query.filter_by(resolved_at=None)
    if query_id:
        errors_query = errors_query.filter_by(query_id=query_id)
    
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
                          total_errors=sum(error_counts.values()))


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
    """Statistics page."""
    return render_template('stats.html')


@main_bp.route('/settings')
def settings():
    """Settings page and connection tests."""
    from scheduler import get_scheduler_status
    from version import __version__
    failing_queries = MonitoredQuery.query.filter(
        MonitoredQuery.is_active == True,
        MonitoredQuery.consecutive_failures > 0
    ).order_by(MonitoredQuery.name).all()

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


@main_bp.route('/connections/new', methods=['GET', 'POST'])
def connection_create():
    """Creates a new connection."""
    if request.method == 'POST':
        conn = DatabaseConnection(
            name=request.form.get('name'),
            db_type=request.form.get('db_type'),
            host=request.form.get('host'),
            port=int(request.form.get('port') or 0) or None,
            database=request.form.get('database'),
            username=request.form.get('username'),
            password=request.form.get('password'),
            is_active=request.form.get('is_active') == 'on'
        )
        db.session.add(conn)
        db.session.commit()
        flash('Connection created!', 'success')
        return redirect(url_for('main.connections_list'))
    
    return render_template('connection_form.html', 
                           connection=None,
                           drivers=get_available_drivers())


@main_bp.route('/connections/<int:conn_id>/edit', methods=['GET', 'POST'])
def connection_edit(conn_id):
    """Edits an existing connection."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    
    if request.method == 'POST':
        conn.name = request.form.get('name')
        conn.db_type = request.form.get('db_type')
        conn.host = request.form.get('host')
        conn.port = int(request.form.get('port') or 0) or None
        conn.database = request.form.get('database')
        conn.username = request.form.get('username')
        # Update the password only if one is given
        new_password = request.form.get('password')
        if new_password:
            conn.password = new_password
        conn.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        flash('Connection updated!', 'success')
        return redirect(url_for('main.connections_list'))
    
    return render_template('connection_form.html', 
                           connection=conn,
                           drivers=get_available_drivers())


@main_bp.route('/connections/<int:conn_id>/delete', methods=['POST'])
def connection_delete(conn_id):
    """Deletes a connection."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    
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


@main_bp.route('/channels/new', methods=['GET', 'POST'])
def channel_create():
    if request.method == 'POST':
        channel_type = request.form.get('channel_type')
        
        if channel_type == 'webhook':
            config = {
                'url': request.form.get('webhook_url', ''),
                'method': request.form.get('webhook_method', 'POST'),
                'headers': json.loads(request.form.get('webhook_headers', '{}') or '{}')
            }
        elif channel_type == 'telegram':
            config = {
                'bot_token': request.form.get('telegram_bot_token', ''),
                'chat_id': request.form.get('telegram_chat_id', '')
            }
        elif channel_type == 'teams':
            config = {'webhook_url': request.form.get('teams_webhook_url', '')}
        else:
            flash('Invalid channel type', 'danger')
            return redirect(url_for('main.channels_list'))
        
        channel = NotificationChannel(
            name=request.form.get('name'),
            channel_type=channel_type,
            is_active=request.form.get('is_active') == 'on'
        )
        channel.set_config(config)
        
        db.session.add(channel)
        db.session.commit()
        flash('Channel created!', 'success')
        return redirect(url_for('main.channels_list'))
    
    return render_template('channel_form.html', channel=None, channel_types=CHANNEL_TYPES)


@main_bp.route('/channels/<int:channel_id>/edit', methods=['GET', 'POST'])
def channel_edit(channel_id):
    channel = NotificationChannel.query.get_or_404(channel_id)
    
    if request.method == 'POST':
        channel.name = request.form.get('name')
        channel.channel_type = request.form.get('channel_type')
        channel.is_active = request.form.get('is_active') == 'on'
        
        if channel.channel_type == 'webhook':
            config = {
                'url': request.form.get('webhook_url', ''),
                'method': request.form.get('webhook_method', 'POST'),
                'headers': json.loads(request.form.get('webhook_headers', '{}') or '{}')
            }
        elif channel.channel_type == 'telegram':
            config = {
                'bot_token': request.form.get('telegram_bot_token', ''),
                'chat_id': request.form.get('telegram_chat_id', '')
            }
        elif channel.channel_type == 'teams':
            config = {'webhook_url': request.form.get('teams_webhook_url', '')}
        
        channel.set_config(config)
        db.session.commit()
        flash('Channel updated!', 'success')
        return redirect(url_for('main.channels_list'))
    
    return render_template('channel_form.html', channel=channel, channel_types=CHANNEL_TYPES)


@main_bp.route('/channels/<int:channel_id>/delete', methods=['POST'])
def channel_delete(channel_id):
    channel = NotificationChannel.query.get_or_404(channel_id)
    if channel.queries:
        flash(f'Cannot delete: the channel is used by {len(channel.queries)} queries.', 'danger')
        return redirect(url_for('main.channels_list'))
    
    db.session.delete(channel)
    db.session.commit()
    flash('Channel deleted.', 'success')
    return redirect(url_for('main.channels_list'))
