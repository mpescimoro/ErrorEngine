"""
Routes Flask per la gestione dell'applicazione web.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from datetime import datetime, time, timedelta
from db_drivers import get_driver, get_available_drivers, DRIVER_LABELS
from models import db, MonitoredQuery, ErrorRecord, QueryLog, EmailLog, RoutingRule, RoutingCondition, DatabaseConnection, NotificationChannel
from email_service import email_service
from monitor_service import monitor_service
from notification_service import notification_service
from cleanup_service import cleanup_service
from routing_service import get_operators_list, apply_routing_rules, get_routing_summary
from data_sources import test_query_source, get_query_fields
from scheduler import trigger_immediate_check
from validators import (
    validate_email_list, validate_query_name, validate_key_fields,
    validate_sql_query, validate_interval, validate_routing_rule, 
    validate_url, sanitize_string, ValidationError
)

import json

main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__, url_prefix='/api')


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
            EmailLog.sent_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
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
            query.schedule_start_time = parse_time(request.form.get('schedule_start_time'))
            query.schedule_end_time = parse_time(request.form.get('schedule_end_time'))
            query.schedule_days = request.form.get('schedule_days', '')
            
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
            query.schedule_start_time = parse_time(request.form.get('schedule_start_time'))
            query.schedule_end_time = parse_time(request.form.get('schedule_end_time'))
            query.schedule_days = request.form.get('schedule_days', '')
            
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


# ============================================================================
# API REST
# ============================================================================

@api_bp.route('/queries', methods=['GET'])
def api_queries_list():
    """API: Lista delle consultazioni."""
    queries = MonitoredQuery.query.all()
    return jsonify([{
        'id': q.id,
        'name': q.name,
        'description': q.description,
        'source_type': q.source_type,
        'is_active': q.is_active,
        'check_interval_minutes': q.check_interval_minutes,
        'last_check_at': q.last_check_at.isoformat() if q.last_check_at else None,
        'total_errors_found': q.total_errors_found,
        'routing_enabled': q.routing_enabled
    } for q in queries])


@api_bp.route('/queries/<int:query_id>/run', methods=['POST'])
def api_run_query(query_id):
    """API: Esegue immediatamente una consultazione (ignora fascia oraria)."""
    query = MonitoredQuery.query.get_or_404(query_id)
    result = monitor_service.check_query(query, force=True)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/test', methods=['POST'])
def api_test_query(query_id):
    """API: Testa una query senza salvare i risultati."""
    query = MonitoredQuery.query.get_or_404(query_id)
    result = test_query_source(query)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/toggle', methods=['POST'])
def api_toggle_query(query_id):
    """API: Attiva/disattiva una consultazione."""
    query = MonitoredQuery.query.get_or_404(query_id)
    query.is_active = not query.is_active
    db.session.commit()
    
    return jsonify({
        'id': query.id,
        'is_active': query.is_active
    })


@api_bp.route('/queries/<int:query_id>/status', methods=['GET'])
def api_query_status(query_id):
    """API: Stato attuale di una consultazione."""
    status = monitor_service.get_query_status(query_id)
    return jsonify(status)


@api_bp.route('/queries/<int:query_id>/fields', methods=['GET'])
def api_query_fields(query_id):
    """API: Campi disponibili dalla query (per configurazione routing)."""
    fields = monitor_service.get_query_available_fields(query_id)
    return jsonify({'fields': fields})


# ============================================================================
# API ROUTING RULES
# ============================================================================

@api_bp.route('/queries/<int:query_id>/routing/rules', methods=['GET'])
def api_get_routing_rules(query_id):
    """API: Lista regole di routing per una query."""
    query = MonitoredQuery.query.get_or_404(query_id)
    
    rules = []
    for rule in query.routing_rules:
        rules.append({
            'id': rule.id,
            'name': rule.name,
            'condition_logic': rule.condition_logic,
            'recipients': rule.recipients,
            'priority': rule.priority,
            'stop_on_match': rule.stop_on_match,
            'is_active': rule.is_active,
            'conditions': [{
                'id': c.id,
                'field_name': c.field_name,
                'operator': c.operator,
                'value': c.value,
                'case_sensitive': c.case_sensitive
            } for c in rule.conditions]
        })
    
    return jsonify({
        'routing_enabled': query.routing_enabled,
        'routing_default_recipients': query.routing_default_recipients,
        'routing_aggregation': query.routing_aggregation,
        'routing_no_match_action': query.routing_no_match_action,
        'rules': rules
    })


@api_bp.route('/queries/<int:query_id>/routing/rules', methods=['POST'])
def api_create_routing_rule(query_id):
    """API: Crea una nuova regola di routing."""
    query = MonitoredQuery.query.get_or_404(query_id)
    data = request.get_json()
    
    # Validazione input
    is_valid, error = validate_routing_rule(data)
    if not is_valid:
        return jsonify({'success': False, 'message': error}), 400
    
    try:
        rule = RoutingRule(
            query_id=query_id,
            name=sanitize_string(data.get('name', ''), 100),
            condition_logic=data.get('condition_logic', 'AND'),
            recipients=data.get('recipients', ''),
            priority=int(data.get('priority', 0)),
            stop_on_match=bool(data.get('stop_on_match', False)),
            is_active=bool(data.get('is_active', True))
        )
        db.session.add(rule)
        db.session.flush()  # Per ottenere l'ID
        
        # Aggiungi condizioni
        for cond_data in data.get('conditions', []):
            condition = RoutingCondition(
                rule_id=rule.id,
                field_name=sanitize_string(cond_data.get('field_name', ''), 100),
                operator=cond_data.get('operator', 'equals'),
                value=sanitize_string(cond_data.get('value', ''), 500),
                case_sensitive=bool(cond_data.get('case_sensitive', False))
            )
            db.session.add(condition)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'rule_id': rule.id,
            'message': 'Regola creata con successo'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@api_bp.route('/queries/<int:query_id>/routing/rules/<int:rule_id>', methods=['PUT'])
def api_update_routing_rule(query_id, rule_id):
    """API: Aggiorna una regola di routing."""
    rule = RoutingRule.query.filter_by(id=rule_id, query_id=query_id).first_or_404()
    data = request.get_json()
    
    # Validazione input
    is_valid, error = validate_routing_rule(data)
    if not is_valid:
        return jsonify({'success': False, 'message': error}), 400
    
    try:
        rule.name = sanitize_string(data.get('name', rule.name), 100)
        rule.condition_logic = data.get('condition_logic', rule.condition_logic)
        rule.recipients = data.get('recipients', rule.recipients)
        rule.priority = int(data.get('priority', rule.priority))
        rule.stop_on_match = bool(data.get('stop_on_match', rule.stop_on_match))
        rule.is_active = bool(data.get('is_active', rule.is_active))
        
        # Aggiorna condizioni (rimuovi e ricrea)
        if 'conditions' in data:
            RoutingCondition.query.filter_by(rule_id=rule.id).delete()
            for cond_data in data['conditions']:
                condition = RoutingCondition(
                    rule_id=rule.id,
                    field_name=sanitize_string(cond_data.get('field_name', ''), 100),
                    operator=cond_data.get('operator', 'equals'),
                    value=sanitize_string(cond_data.get('value', ''), 500),
                    case_sensitive=bool(cond_data.get('case_sensitive', False))
                )
                db.session.add(condition)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Regola aggiornata con successo'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@api_bp.route('/queries/<int:query_id>/routing/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_routing_rule(query_id, rule_id):
    """API: Elimina una regola di routing."""
    rule = RoutingRule.query.filter_by(id=rule_id, query_id=query_id).first_or_404()
    
    try:
        db.session.delete(rule)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Regola eliminata'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@api_bp.route('/queries/<int:query_id>/routing/test', methods=['POST'])
def api_test_routing(query_id):
    """API: Testa il routing con dati di esempio."""
    query = MonitoredQuery.query.get_or_404(query_id)
    data = request.get_json()
    
    test_errors = data.get('errors', [])
    if not test_errors:
        # Usa gli errori reali attuali
        test_errors = [e.get_error_data() for e in 
                      ErrorRecord.query.filter_by(query_id=query_id, resolved_at=None).limit(10).all()]
    
    if not test_errors:
        return jsonify({
            'success': True,
            'message': 'Nessun errore da testare',
            'routing_result': {}
        })
    
    summary = get_routing_summary(query, test_errors)
    routing_result = apply_routing_rules(query, test_errors)
    
    # Formatta risultato per UI
    formatted_result = {}
    for recipient, errors in routing_result.items():
        key = recipient if isinstance(recipient, str) else ', '.join(recipient)
        formatted_result[key] = len(errors)
    
    return jsonify({
        'success': True,
        'summary': summary,
        'routing_result': formatted_result
    })


@api_bp.route('/routing/operators', methods=['GET'])
def api_get_operators():
    """API: Lista degli operatori disponibili per le condizioni."""
    return jsonify(get_operators_list())


# ============================================================================
# API ERRORS
# ============================================================================

@api_bp.route('/errors', methods=['GET'])
def api_errors_list():
    """API: Lista errori attivi."""
    query_id = request.args.get('query_id', type=int)
    errors = monitor_service.get_active_errors(query_id)
    return jsonify(errors)


@api_bp.route('/errors/<int:error_id>/resolve', methods=['POST'])
def api_resolve_error(error_id):
    """API: Marca un errore come risolto manualmente."""
    error = ErrorRecord.query.get_or_404(error_id)
    error.resolved_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'id': error.id,
        'resolved_at': error.resolved_at.isoformat()
    })


# ============================================================================
# API DATABASE CONNECTIONS
# ============================================================================

@api_bp.route('/connections', methods=['GET'])
def api_connections_list():
    """API: Lista connessioni."""
    connections = DatabaseConnection.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'db_type': c.db_type,
        'host': c.host,
        'port': c.port,
        'database': c.database,
        'is_active': c.is_active
    } for c in connections])


@api_bp.route('/connections/<int:conn_id>/test', methods=['POST'])
def api_connection_test(conn_id):
    """API: Testa una connessione salvata."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    result = conn.test_connection()
    return jsonify(result)


@api_bp.route('/connections/test', methods=['POST'])
def api_connection_test_new():
    """API: Testa una connessione non ancora salvata."""
    data = request.get_json()
    
    db_type = data.get('db_type')
    if not db_type:
        return jsonify({'status': 'error', 'message': 'Tipo database richiesto'}), 400
    
    try:
        driver = get_driver(db_type)
        result = driver.test_connection(
            host=data.get('host'),
            port=int(data.get('port') or driver.default_port or 0),
            database=data.get('database'),
            username=data.get('username'),
            password=data.get('password')
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@api_bp.route('/connections/<int:conn_id>/test-query', methods=['POST'])
def api_connection_test_query(conn_id):
    """API: Testa una query su una connessione specifica."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    data = request.get_json()
    sql = data.get('sql')
    
    if not sql:
        return jsonify({'valid': False, 'error': 'Query SQL richiesta'}), 400
    
    try:
        driver = conn.get_driver()
        connection = driver.connect(
            conn.host, conn.port, conn.database, conn.username, conn.password
        )
        try:
            result = driver.test_query(connection, sql)
            return jsonify(result)
        finally:
            connection.close()
    except Exception as e:
        return jsonify({'valid': False, 'error': str(e)})


# ============================================================================
# API NOTIFICATION CHANNELS
# ============================================================================

@api_bp.route('/channels', methods=['GET'])
def api_channels_list():
    channels = NotificationChannel.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'channel_type': c.channel_type
    } for c in channels])


@api_bp.route('/channels/<int:channel_id>/test', methods=['POST'])
def api_channel_test(channel_id):
    channel = NotificationChannel.query.get_or_404(channel_id)
    result = notification_service.test_channel(channel)
    return jsonify(result)


# ============================================================================
# API TAGS
# ============================================================================

@api_bp.route('/tags', methods=['GET'])
def api_tags_list():
    """Lista tag esistenti per autocomplete."""
    all_tags = set()
    for q in MonitoredQuery.query.all():
        all_tags.update(q.get_tags_list())
    return jsonify(sorted(all_tags))


# ============================================================================
# API STATISTICS
# ============================================================================

@api_bp.route('/stats/overview', methods=['GET'])
def api_stats_overview():
    """Statistiche generali."""
    from sqlalchemy import func
    
    days = request.args.get('days', 7, type=int)
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = now - timedelta(days=days)
    prev_period_start = period_start - timedelta(days=days)
    month_ago = now - timedelta(days=30)
    
    # Errori oggi
    errors_today = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= today_start
    ).count()
    
    # Errori nel periodo selezionato
    errors_period = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= period_start
    ).count()
    
    # Errori periodo precedente (per trend)
    errors_prev_period = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= prev_period_start,
        ErrorRecord.first_seen_at < period_start
    ).count()
    
    # Calcola trend
    if errors_prev_period > 0:
        trend_percent = round(((errors_period - errors_prev_period) / errors_prev_period) * 100)
    else:
        trend_percent = 100 if errors_period > 0 else 0
    
    # Errori attivi (non risolti)
    errors_active = ErrorRecord.query.filter(
        ErrorRecord.resolved_at == None
    ).count()
    
    # Tempo medio risoluzione (ultimi 30 giorni)
    resolved_errors = ErrorRecord.query.filter(
        ErrorRecord.resolved_at != None,
        ErrorRecord.resolved_at >= month_ago
    ).all()
    
    avg_resolution_hours = 0
    if resolved_errors:
        total_seconds = sum(
            (e.resolved_at - e.first_seen_at).total_seconds() 
            for e in resolved_errors if e.resolved_at and e.first_seen_at
        )
        avg_resolution_hours = round(total_seconds / len(resolved_errors) / 3600, 1)
    
    # Top 5 consultazioni per errori
    top_queries = db.session.query(
        MonitoredQuery.id,
        MonitoredQuery.name,
        func.count(ErrorRecord.id).label('error_count')
    ).join(ErrorRecord).filter(
        ErrorRecord.first_seen_at >= period_start
    ).group_by(MonitoredQuery.id).order_by(
        func.count(ErrorRecord.id).desc()
    ).limit(5).all()
    
    return jsonify({
        'errors_today': errors_today,
        'errors_week': errors_period,
        'errors_active': errors_active,
        'trend_percent': trend_percent,
        'trend_direction': 'up' if trend_percent > 0 else ('down' if trend_percent < 0 else 'stable'),
        'avg_resolution_hours': avg_resolution_hours,
        'top_queries': [
            {'id': q.id, 'name': q.name, 'count': q.error_count}
            for q in top_queries
        ]
    })


@api_bp.route('/stats/timeline', methods=['GET'])
def api_stats_timeline():
    """Errori per giorno per grafico."""
    from sqlalchemy import func
    
    days = int(request.args.get('days', 14))
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    
    daily_stats = db.session.query(
        func.date(ErrorRecord.first_seen_at).label('date'),
        func.count(ErrorRecord.id).label('count')
    ).filter(
        ErrorRecord.first_seen_at >= start_date
    ).group_by(
        func.date(ErrorRecord.first_seen_at)
    ).order_by(
        func.date(ErrorRecord.first_seen_at)
    ).all()
    
    stats_dict = {str(s.date): s.count for s in daily_stats}
    
    timeline = []
    for i in range(days):
        date = (start_date + timedelta(days=i)).date()
        timeline.append({
            'date': str(date),
            'label': date.strftime('%d/%m'),
            'count': stats_dict.get(str(date), 0)
        })
    
    return jsonify(timeline)


@api_bp.route('/stats/by-query', methods=['GET'])
def api_stats_by_query():
    """Statistiche per singola consultazione."""
    from sqlalchemy import func
    
    query_id = request.args.get('query_id', type=int)
    days = request.args.get('days', 30, type=int)
    
    if not query_id:
        return jsonify({'error': 'query_id richiesto'}), 400
    
    query = MonitoredQuery.query.get_or_404(query_id)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    total_errors = ErrorRecord.query.filter(
        ErrorRecord.query_id == query_id,
        ErrorRecord.first_seen_at >= start_date
    ).count()
    
    active_errors = ErrorRecord.query.filter(
        ErrorRecord.query_id == query_id,
        ErrorRecord.resolved_at == None
    ).count()
    
    daily = db.session.query(
        func.date(ErrorRecord.first_seen_at).label('date'),
        func.count(ErrorRecord.id).label('count')
    ).filter(
        ErrorRecord.query_id == query_id,
        ErrorRecord.first_seen_at >= start_date
    ).group_by(
        func.date(ErrorRecord.first_seen_at)
    ).all()
    
    return jsonify({
        'query_id': query_id,
        'query_name': query.name,
        'days': days,
        'total_errors': total_errors,
        'active_errors': active_errors,
        'daily': [{'date': str(d.date), 'count': d.count} for d in daily]
    })


@api_bp.route('/stats/all-queries', methods=['GET'])
def api_stats_all_queries():
    """Statistiche per tutte le consultazioni."""
    from sqlalchemy import func
    
    days = request.args.get('days', 30, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    results = db.session.query(
        MonitoredQuery.id,
        MonitoredQuery.name,
        MonitoredQuery.tags,
        func.count(ErrorRecord.id).label('error_count'),
        func.sum(
            db.case(
                (ErrorRecord.resolved_at == None, 1),
                else_=0
            )
        ).label('active_count'),
        func.max(ErrorRecord.first_seen_at).label('last_error')
    ).outerjoin(
        ErrorRecord, 
        db.and_(
            ErrorRecord.query_id == MonitoredQuery.id,
            ErrorRecord.first_seen_at >= start_date
        )
    ).group_by(MonitoredQuery.id).having(
        func.count(ErrorRecord.id) > 0
    ).order_by(
        func.count(ErrorRecord.id).desc()
    ).all()
    
    return jsonify([{
        'id': r.id,
        'name': r.name,
        'tags': r.tags or '',
        'error_count': r.error_count,
        'active_count': int(r.active_count or 0),
        'last_error': r.last_error.strftime('%d/%m/%y %H:%M') if r.last_error else None
    } for r in results])


# ============================================================================
# API TEST
# ============================================================================

@api_bp.route('/test/email', methods=['POST'])
def api_test_email():
    """API: Test invio email."""
    data = request.get_json()
    recipient = data.get('recipient')
    
    if not recipient:
        return jsonify({'success': False, 'message': 'Destinatario richiesto'}), 400
    
    result = email_service.test_email(recipient)
    return jsonify(result)


@api_bp.route('/stats', methods=['GET'])
def api_stats():
    """API: Statistiche generali."""
    return jsonify({
        'total_queries': MonitoredQuery.query.count(),
        'active_queries': MonitoredQuery.query.filter_by(is_active=True).count(),
        'total_active_errors': ErrorRecord.query.filter_by(resolved_at=None).count(),
        'total_resolved_errors': ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count(),
        'total_emails_sent': EmailLog.query.filter_by(status='sent').count(),
        'queries_with_routing': MonitoredQuery.query.filter_by(routing_enabled=True).count()
    })


# ============================================================================
# API CLEANUP & MAINTENANCE
# ============================================================================

@api_bp.route('/cleanup/run', methods=['POST'])
def api_run_cleanup():
    """API: Esegue manualmente la pulizia di tutti i record."""
    try:
        result = cleanup_service.run_manual_cleanup()
        return jsonify({
            'success': True,
            'message': 'Pulizia completata',
            'result': result
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@api_bp.route('/cleanup/stats', methods=['GET'])
def api_cleanup_stats():
    """API: Statistiche sui record e configurazione retention."""
    try:
        stats = cleanup_service.get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 500


@api_bp.route('/health', methods=['GET'])
def api_health():
    """API: Health check per monitoring esterno."""
    try:
        # Verifica database SQLite
        db_ok = True
        try:
            MonitoredQuery.query.limit(1).all()
        except Exception:
            db_ok = False
        
        # Conta connessioni database configurate
        connections_count = DatabaseConnection.query.filter_by(is_active=True).count()
        
        status = 'healthy' if db_ok else 'unhealthy'
        
        return jsonify({
            'status': status,
            'checks': {
                'database': 'ok' if db_ok else 'error',
                'connections_configured': connections_count
            },
            'timestamp': datetime.utcnow().isoformat()
        }), 200 if db_ok else 503
        
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@api_bp.route('/scheduler/next', methods=['GET'])
def api_scheduler_next():
    """API: Restituisce il tempo al prossimo check schedulato."""
    try:
        now = datetime.utcnow()
        queries = MonitoredQuery.query.filter_by(is_active=True).all()
        
        if not queries:
            return jsonify({
                'has_scheduled': False,
                'message': 'Nessuna consultazione attiva'
            })
        
        next_check = None
        next_query = None
        
        for query in queries:
            # Verifica se in fascia oraria
            if not query.is_in_schedule():
                continue
            
            # Calcola prossimo check
            if query.last_check_at is None:
                # Mai eseguita, sarà al prossimo ciclo scheduler
                query_next = now
            else:
                query_next = query.last_check_at + timedelta(minutes=query.check_interval_minutes)
            
            # Se è già passato, sarà al prossimo ciclo
            if query_next < now:
                query_next = now
            
            if next_check is None or query_next < next_check:
                next_check = query_next
                next_query = query
        
        if next_query is None:
            return jsonify({
                'has_scheduled': False,
                'message': 'Nessuna consultazione in fascia oraria'
            })
        
        # Calcola secondi rimanenti
        seconds_remaining = max(0, int((next_check - now).total_seconds()))
        
        return jsonify({
            'has_scheduled': True,
            'query_id': next_query.id,
            'query_name': next_query.name,
            'seconds_remaining': seconds_remaining,
            'next_check_at': next_check.isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'has_scheduled': False,
            'error': str(e)
        }), 500
