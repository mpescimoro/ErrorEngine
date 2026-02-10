"""
Web routes — HTML pages and form handling.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, time
from db_drivers import get_available_drivers, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, QueryLog, EmailLog,
                    DatabaseConnection, NotificationChannel)
from routing_service import get_operators_list
from utils import get_utc_now

import json

main_bp = Blueprint('main', __name__)


# ============================================================================
# CONTEXT PROCESSOR - Inject global data into templates
# ============================================================================

@main_bp.app_context_processor
def inject_global_data():
    """Inietta dati globali in tutti i template."""
    return {
        'error_count': ErrorRecord.query.filter_by(resolved_at=None).count()
    }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_time(time_str):
    """Converte stringa HH:MM in oggetto time."""
    if not time_str:
        return None
    try:
        parts = time_str.split(':')
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def format_time(t):
    """Formatta oggetto time in stringa HH:MM."""
    if not t:
        return ''
    return t.strftime('%H:%M')


# ============================================================================
# PAGINE WEB
# ============================================================================

@main_bp.route('/')
def dashboard():
    """Dashboard principale con panoramica delle consultazioni."""
    queries = MonitoredQuery.query.order_by(MonitoredQuery.name).all()
    
    # Statistiche generali
    stats = {
        'total_queries': len(queries),
        'active_queries': sum(1 for q in queries if q.is_active),
        'total_active_errors': ErrorRecord.query.filter_by(resolved_at=None).count(),
        'emails_sent_today': EmailLog.query.filter(
            EmailLog.sent_at >= get_utc_now().replace(hour=0, minute=0, second=0)
        ).count()
    }
    
    return render_template('dashboard.html', queries=queries, stats=stats)


@main_bp.route('/queries')
def queries_list():
    tag_filter = request.args.get('tag', '')
    
    if tag_filter:
        # Filtra per tag (LIKE perché è CSV)
        queries = MonitoredQuery.query.filter(
            MonitoredQuery.tags.contains(tag_filter)
        ).order_by(MonitoredQuery.name).all()
    else:
        queries = MonitoredQuery.query.order_by(MonitoredQuery.name).all()
    
    # Raccogli tutti i tag unici per il filtro
    all_tags = set()
    for q in MonitoredQuery.query.all():
        all_tags.update(q.get_tags_list())
    
    return render_template('queries_list.html', 
                          queries=queries, 
                          all_tags=sorted(all_tags),
                          current_tag=tag_filter)


@main_bp.route('/queries/new', methods=['GET', 'POST'])
def query_create():
    """Creazione di una nuova consultazione."""
    if request.method == 'POST':
        try:
            # Dati base
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
            
            # Source config per HTTP
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
            query.email_subject = request.form.get('email_subject', '[ErrorEngine] Nuovi errori: {query_name}')
            query.email_recipients = request.form.get('email_recipients', '')
            query.email_template = request.form.get('email_template', '')
            
            # Scheduling
            interval_value = int(request.form.get('check_interval_value', 15))
            interval_unit = request.form.get('check_interval_unit', 'minutes')
            if interval_unit == 'hours':
                query.check_interval_minutes = interval_value * 60
            else:  # minutes
                query.check_interval_minutes = interval_value
            # Limita a max 24h
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
            db.session.flush()  # Per ottenere l'ID
            
            channel_ids = request.form.getlist('notification_channels')
            if channel_ids:
                channels = NotificationChannel.query.filter(NotificationChannel.id.in_(channel_ids)).all()
                query.notification_channels = channels
            
            db.session.commit()
            flash(f'Consultazione "{query.name}" creata con successo!', 'success')
            return redirect(url_for('main.query_detail', query_id=query.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Errore nella creazione: {str(e)}', 'danger')
    
    return render_template('query_form.html', 
                        query=None, 
                          operators=get_operators_list(),
                        connections=DatabaseConnection.query.filter_by(is_active=True).all(),
                        channels=NotificationChannel.query.filter_by(is_active=True).all())


@main_bp.route('/queries/<int:query_id>')
def query_detail(query_id):
    """Dettaglio di una consultazione."""
    query = MonitoredQuery.query.get_or_404(query_id)
    
    # Errori attivi
    active_errors = ErrorRecord.query.filter_by(
        query_id=query_id, resolved_at=None
    ).order_by(ErrorRecord.first_seen_at.desc()).limit(50).all()
    
    # Ultimi log
    recent_logs = QueryLog.query.filter_by(
        query_id=query_id
    ).order_by(QueryLog.executed_at.desc()).limit(20).all()
    
    return render_template(
        'query_detail.html', 
        query=query, 
        active_errors=active_errors,
        recent_logs=recent_logs,
        operators=get_operators_list()
    )


@main_bp.route('/queries/<int:query_id>/edit', methods=['GET', 'POST'])
def query_edit(query_id):
    """Modifica di una consultazione esistente."""
    query = MonitoredQuery.query.get_or_404(query_id)
    
    if request.method == 'POST':
        try:
            # Dati base
            query.name = request.form['name']
            query.description = request.form.get('description', '')
            query.source_type = request.form.get('source_type', 'database')
            
            # Database connection
            db_conn_id = request.form.get('db_connection_id')
            query.db_connection_id = int(db_conn_id) if db_conn_id else None
            
            query.sql_query = request.form.get('sql_query', '')
            query.key_fields = request.form['key_fields']
            query.is_active = request.form.get('is_active') == 'on'
            
            # Source config per HTTP
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
            # Limita a max 24h
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
            
            # Routing base (le regole si gestiscono via API)
            query.routing_enabled = request.form.get('routing_enabled') == 'on'
            query.routing_default_recipients = request.form.get('routing_default_recipients', '')
            query.routing_aggregation = request.form.get('routing_aggregation', 'per_recipient')
            query.routing_no_match_action = request.form.get('routing_no_match_action', 'send_default')
            
            channel_ids = request.form.getlist('notification_channels')
            query.notification_channels = NotificationChannel.query.filter(NotificationChannel.id.in_(channel_ids)).all() if channel_ids else []
            
            query.tags = request.form.get('tags', '')
            
            db.session.commit()
            flash('Consultazione aggiornata con successo!', 'success')
            return redirect(url_for('main.query_detail', query_id=query.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Errore nell\'aggiornamento: {str(e)}', 'danger')
    
    return render_template('query_form.html', query=query, action='edit',
                          operators=get_operators_list(),
                          connections=DatabaseConnection.query.filter_by(is_active=True).all(),
                          channels=NotificationChannel.query.filter_by(is_active=True).all())


@main_bp.route('/queries/<int:query_id>/delete', methods=['POST'])
def query_delete(query_id):
    """Elimina una consultazione."""
    query = MonitoredQuery.query.get_or_404(query_id)
    name = query.name
    
    try:
        db.session.delete(query)
        db.session.commit()
        flash(f'Consultazione "{name}" eliminata.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Errore nell\'eliminazione: {str(e)}', 'danger')
    
    return redirect(url_for('main.queries_list'))


@main_bp.route('/errors')
def errors_list():
    """Lista di tutti gli errori attivi."""
    query_id = request.args.get('query_id', type=int)
    
    errors_query = ErrorRecord.query.filter_by(resolved_at=None)
    if query_id:
        errors_query = errors_query.filter_by(query_id=query_id)
    
    errors = errors_query.order_by(ErrorRecord.first_seen_at.desc()).all()
    queries = MonitoredQuery.query.order_by(MonitoredQuery.name).all()
    
    return render_template('errors_list.html', errors=errors, queries=queries, 
                          selected_query_id=query_id)


@main_bp.route('/logs')
def logs_list():
    """Visualizzazione dei log di esecuzione."""
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
    """Pagina statistiche."""
    return render_template('stats.html')


@main_bp.route('/settings')
def settings():
    """Pagina impostazioni e test connessioni."""
    return render_template('settings.html')


# ============================================================================
# DATABASE CONNECTIONS ROUTES
# ============================================================================

@main_bp.route('/connections')
def connections_list():
    """Lista connessioni database."""
    connections = DatabaseConnection.query.order_by(DatabaseConnection.name).all()
    return render_template('connections_list.html', 
                           connections=connections,
                           driver_labels=DRIVER_LABELS)


@main_bp.route('/connections/new', methods=['GET', 'POST'])
def connection_create():
    """Crea nuova connessione."""
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
        flash('Connessione creata con successo!', 'success')
        return redirect(url_for('main.connections_list'))
    
    return render_template('connection_form.html', 
                           connection=None,
                           drivers=get_available_drivers())


@main_bp.route('/connections/<int:conn_id>/edit', methods=['GET', 'POST'])
def connection_edit(conn_id):
    """Modifica connessione esistente."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    
    if request.method == 'POST':
        conn.name = request.form.get('name')
        conn.db_type = request.form.get('db_type')
        conn.host = request.form.get('host')
        conn.port = int(request.form.get('port') or 0) or None
        conn.database = request.form.get('database')
        conn.username = request.form.get('username')
        # Aggiorna password solo se fornita
        new_password = request.form.get('password')
        if new_password:
            conn.password = new_password
        conn.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        flash('Connessione aggiornata!', 'success')
        return redirect(url_for('main.connections_list'))
    
    return render_template('connection_form.html', 
                           connection=conn,
                           drivers=get_available_drivers())


@main_bp.route('/connections/<int:conn_id>/delete', methods=['POST'])
def connection_delete(conn_id):
    """Elimina connessione."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    
    # Verifica se è usata da qualche query
    if conn.queries.count() > 0:
        flash(f'Impossibile eliminare: connessione usata da {conn.queries.count()} consultazioni.', 'danger')
        return redirect(url_for('main.connections_list'))
    
    db.session.delete(conn)
    db.session.commit()
    flash('Connessione eliminata.', 'success')
    return redirect(url_for('main.connections_list'))


# ============================================================================
# NOTIFICATION CHANNELS
# ============================================================================

CHANNEL_TYPES = {
    'webhook': 'Webhook Generico',
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
            flash('Tipo canale non valido', 'danger')
            return redirect(url_for('main.channels_list'))
        
        channel = NotificationChannel(
            name=request.form.get('name'),
            channel_type=channel_type,
            is_active=request.form.get('is_active') == 'on'
        )
        channel.set_config(config)
        
        db.session.add(channel)
        db.session.commit()
        flash('Canale creato!', 'success')
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
        flash('Canale aggiornato!', 'success')
        return redirect(url_for('main.channels_list'))
    
    return render_template('channel_form.html', channel=channel, channel_types=CHANNEL_TYPES)


@main_bp.route('/channels/<int:channel_id>/delete', methods=['POST'])
def channel_delete(channel_id):
    channel = NotificationChannel.query.get_or_404(channel_id)
    if channel.queries:
        flash(f'Impossibile: usato da {len(channel.queries)} consultazioni.', 'danger')
        return redirect(url_for('main.channels_list'))
    
    db.session.delete(channel)
    db.session.commit()
    flash('Canale eliminato.', 'success')
    return redirect(url_for('main.channels_list'))
