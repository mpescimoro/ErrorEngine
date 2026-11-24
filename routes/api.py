"""
REST API routes — JSON endpoints.
"""
import logging
from flask import Blueprint, request, jsonify, abort
from datetime import timedelta
from types import SimpleNamespace
from db_drivers import get_driver, driver_timeouts, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, EmailLog, RoutingRule,
                    RoutingCondition, DatabaseConnection, NotificationChannel)
from email_service import email_service
from encryption import UnreadableSecret
from monitor_service import monitor_service
from notification_service import notification_service
from cleanup_service import cleanup_service
from routing_service import apply_routing_rules, get_routing_summary
from data_sources import test_query_source
from validators import (validate_email, validate_email_list, validate_routing_rule, sanitize_string,
                        write_statement_keyword)
from utils import (get_utc_now, get_local_now, format_local, local_day_start_utc, utc_to_local,
                   redact_secrets)
from version import __version__


logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/queries', methods=['GET'])
def api_queries_list():
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
    """Runs a query right away (ignores the time window)."""
    query = db.get_or_404(MonitoredQuery, query_id)
    result = monitor_service.check_query(query, force=True)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/test', methods=['POST'])
def api_test_query(query_id):
    """Tests a query without saving the results."""
    query = db.get_or_404(MonitoredQuery, query_id)
    result = test_query_source(query)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/toggle', methods=['POST'])
def api_toggle_query(query_id):
    query = db.get_or_404(MonitoredQuery, query_id)
    query.is_active = not query.is_active
    db.session.commit()
    
    return jsonify({
        'id': query.id,
        'is_active': query.is_active
    })


@api_bp.route('/queries/<int:query_id>/status', methods=['GET'])
def api_query_status(query_id):
    db.get_or_404(MonitoredQuery, query_id)
    status = monitor_service.get_query_status(query_id)
    return jsonify(status)


@api_bp.route('/queries/<int:query_id>/fields', methods=['GET'])
def api_query_fields(query_id):
    """Fields available from the query (for routing configuration)."""
    fields = monitor_service.get_query_available_fields(query_id)
    return jsonify({'fields': fields})


# --- Routing rules ---

@api_bp.route('/queries/<int:query_id>/routing/rules', methods=['GET'])
def api_get_routing_rules(query_id):
    query = db.get_or_404(MonitoredQuery, query_id)
    
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


def condition_value(value) -> str:
    """The value of a condition as the page sends it: text, numbers from scripts included."""
    return '' if value is None else str(value)


@api_bp.route('/queries/<int:query_id>/routing/rules', methods=['POST'])
def api_create_routing_rule(query_id):
    db.get_or_404(MonitoredQuery, query_id)
    data = request.get_json()
    
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
        db.session.flush()  # To get the ID
        for cond_data in data.get('conditions', []):
            condition = RoutingCondition(
                rule_id=rule.id,
                field_name=sanitize_string(cond_data.get('field_name', ''), 100),
                operator=cond_data.get('operator', 'equals'),
                value=sanitize_string(condition_value(cond_data.get('value')), 500),
                case_sensitive=bool(cond_data.get('case_sensitive', False))
            )
            db.session.add(condition)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'rule_id': rule.id,
            'message': 'Rule created'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': redact_secrets(str(e))}), 400


@api_bp.route('/queries/<int:query_id>/routing/rules/<int:rule_id>', methods=['PUT'])
def api_update_routing_rule(query_id, rule_id):
    rule = RoutingRule.query.filter_by(id=rule_id, query_id=query_id).first_or_404()
    data = request.get_json()
    
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
        
        # Update the conditions (remove and recreate)
        if 'conditions' in data:
            RoutingCondition.query.filter_by(rule_id=rule.id).delete()
            for cond_data in data['conditions']:
                condition = RoutingCondition(
                    rule_id=rule.id,
                    field_name=sanitize_string(cond_data.get('field_name', ''), 100),
                    operator=cond_data.get('operator', 'equals'),
                    value=sanitize_string(condition_value(cond_data.get('value')), 500),
                    case_sensitive=bool(cond_data.get('case_sensitive', False))
                )
                db.session.add(condition)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Rule updated'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': redact_secrets(str(e))}), 400


@api_bp.route('/queries/<int:query_id>/routing/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_routing_rule(query_id, rule_id):
    rule = RoutingRule.query.filter_by(id=rule_id, query_id=query_id).first_or_404()
    
    try:
        db.session.delete(rule)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Rule deleted'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': redact_secrets(str(e))}), 400


@api_bp.route('/queries/<int:query_id>/routing/test', methods=['POST'])
def api_test_routing(query_id):
    """Recipients the routing rules choose for the errors sent, or for the active ones."""
    query = db.get_or_404(MonitoredQuery, query_id)
    data = request.get_json(silent=True) if request.get_data() else {}
    test_errors = data.get('errors', []) if isinstance(data, dict) else None
    if not (isinstance(test_errors, list) and all(isinstance(error, dict) for error in test_errors)):
        return jsonify({'success': False, 'message': 'The body is JSON: {"errors": [{"FIELD": "value", ...}, ...]}, '
                                                     'or nothing for the active errors of the query'}), 400

    if not test_errors:
        test_errors = [e.get_error_data() for e in 
                      ErrorRecord.query.filter_by(query_id=query_id, resolved_at=None).limit(10).all()]
    
    if not test_errors:
        return jsonify({
            'success': True,
            'message': 'No errors to test',
            'routing_result': {}
        })
    
    summary = get_routing_summary(query, test_errors)
    routing_result = apply_routing_rules(query, test_errors)
    
    # A count per recipient; with routing off, the recipients of the query together
    formatted_result = {}
    for recipient, errors in routing_result.items():
        key = recipient if isinstance(recipient, str) else ', '.join(recipient)
        formatted_result[key] = len(errors)
    
    return jsonify({
        'success': True,
        'summary': summary,
        'routing_result': formatted_result
    })


# --- Errors ---

@api_bp.route('/errors', methods=['GET'])
def api_errors_list():
    query_id = request.args.get('query_id', type=int)
    errors = monitor_service.get_active_errors(query_id)
    return jsonify(errors)


@api_bp.route('/errors/summary', methods=['GET'])
def api_errors_summary():
    """Summary of the active errors, for the automatic page refresh."""
    return jsonify(ErrorRecord.active_summary())


@api_bp.route('/errors/<int:error_id>/resolve', methods=['POST'])
def api_resolve_error(error_id):
    """Marks an error as resolved manually."""
    error = db.get_or_404(ErrorRecord, error_id)
    # Already resolved: it keeps its time, which the cleanup of resolved errors counts from
    if error.resolved_at is None:
        error.resolved_at = get_utc_now()
        db.session.commit()
    
    return jsonify({
        'id': error.id,
        'resolved_at': error.resolved_at.isoformat()
    })


# --- Database connections ---

@api_bp.route('/connections', methods=['GET'])
def api_connections_list():
    connections = DatabaseConnection.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'db_type': c.db_type,
        'db_label': DRIVER_LABELS.get(c.db_type, c.db_type),
        'host': c.host,
        'port': c.port,
        'database': c.database,
        'is_active': c.is_active
    } for c in connections])


def test_typed_connection(data: dict, password):
    """A connection as typed in the form, with this password."""
    db_type = data.get('db_type')
    if not db_type:
        return jsonify({'status': 'error', 'message': 'Database type required'}), 400

    try:
        driver = get_driver(db_type, **driver_timeouts())
        result = driver.test_connection(
            host=data.get('host'),
            port=int(data.get('port') or driver.default_port or 0),
            database=data.get('database'),
            username=data.get('username'),
            password=password
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': redact_secrets(str(e))})


@api_bp.route('/connections/<int:conn_id>/test', methods=['POST'])
def api_connection_test(conn_id):
    """
    The saved connection. With the fields of the edit form, where the password was left empty: those fields
    with the saved password, which goes only to the server it was saved for, as when the form is saved.
    """
    from auth import signed_in_page
    conn = db.get_or_404(DatabaseConnection, conn_id)
    # The API token tests what is saved: typed servers, users and files need the session (docs/api-reference.md)
    typed = request.get_json(silent=True) if signed_in_page() else None
    if not isinstance(typed, dict) or not typed:
        return jsonify(conn.test_connection())
    port = str(typed.get('port') or '').strip()
    if (str(typed.get('host') or '').strip().lower(), int(port) if port.isdigit() else None) != \
            ((conn.host or '').strip().lower(), conn.port):
        return jsonify({'status': 'error', 'message': 'Password: the server changed, type it again'})
    try:
        password = conn.password
    except UnreadableSecret as e:
        return jsonify({'status': 'error', 'message': str(e)})
    return test_typed_connection(typed, password)


@api_bp.route('/connections/test', methods=['POST'])
def api_connection_test_new():
    """Tests a connection not saved yet."""
    data = request.get_json(silent=True) or {}
    return test_typed_connection(data, data.get('password'))


@api_bp.route('/connections/<int:conn_id>/test-query', methods=['POST'])
def api_connection_test_query(conn_id):
    """Tests a query on a specific connection."""
    conn = db.get_or_404(DatabaseConnection, conn_id)
    data = request.get_json() or {}
    sql = data.get('sql')
    
    if not sql:
        return jsonify({'valid': False, 'error': 'SQL query required'}), 400

    # The test runs the statement for real: one that can change data only where the connection allows
    # it, and confirmed (the page asks when it gets 'confirm')
    refusal = conn.write_refusal(sql)
    if refusal:
        return jsonify({'valid': False, 'error': refusal})
    keyword = write_statement_keyword(sql)
    if keyword and data.get('confirm_write') is not True:
        return jsonify({'valid': False, 'confirm': keyword,
                        'error': f'The query contains {keyword}, which can change data: confirm it to test it'})

    try:
        columns, rows = conn.execute_query(sql)
    except Exception as e:
        return jsonify({'valid': False, 'error': redact_secrets(str(e))})
    return jsonify({'valid': True, 'columns': columns, 'row_count': len(rows), 'sample_rows': rows[:5], 'error': None})


# --- Notification channels ---

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
    channel = db.get_or_404(NotificationChannel, channel_id)
    result = notification_service.test_channel(channel)
    return jsonify(result)


# --- Statistics ---

def days_asked(default: int) -> int:
    """?days=, from 1 to 365: more only makes the server work (700,000 days took 4.8 s and 650 MB)."""
    days = request.args.get('days', default, type=int)
    if not 1 <= days <= 365:
        abort(400, 'days: from 1 to 365')
    return days


@api_bp.route('/stats/overview', methods=['GET'])
def api_stats_overview():
    """General statistics; ?query_id= for one query."""
    from sqlalchemy import func
    
    days = days_asked(7)
    query_id = request.args.get('query_id', type=int)

    def errors(*conditions):
        """The errors that meet the conditions, of the query asked for or of all of them."""
        found = ErrorRecord.query.filter(*conditions)
        return found.filter(ErrorRecord.query_id == query_id) if query_id else found

    now = get_utc_now()
    today_start = local_day_start_utc()   # local midnight, not UTC
    period_start = now - timedelta(days=days)
    prev_period_start = period_start - timedelta(days=days)
    month_ago = now - timedelta(days=30)
    
    errors_today = errors(ErrorRecord.first_seen_at >= today_start).count()
    errors_period = errors(ErrorRecord.first_seen_at >= period_start).count()
    
    errors_prev_period = errors(ErrorRecord.first_seen_at >= prev_period_start,
                                ErrorRecord.first_seen_at < period_start).count()
    
    if errors_prev_period > 0:
        trend_percent = round(((errors_period - errors_prev_period) / errors_prev_period) * 100)
    else:
        trend_percent = 100 if errors_period > 0 else 0
    
    errors_active = errors(ErrorRecord.resolved_at.is_(None)).count()
    
    resolved_errors = errors(ErrorRecord.resolved_at.isnot(None), ErrorRecord.resolved_at >= month_ago).all()
    
    avg_resolution_hours = 0
    if resolved_errors:
        total_seconds = sum(
            (e.resolved_at - e.first_seen_at).total_seconds() 
            for e in resolved_errors if e.resolved_at and e.first_seen_at
        )
        avg_resolution_hours = round(total_seconds / len(resolved_errors) / 3600, 1)
    
    top_queries = db.session.query(
        MonitoredQuery.id,
        MonitoredQuery.name,
        func.count(ErrorRecord.id).label('error_count')
    ).join(ErrorRecord).filter(
        ErrorRecord.first_seen_at >= period_start,
        *([MonitoredQuery.id == query_id] if query_id else [])
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
    """Errors per day, for the chart: the last N local days, today included; ?query_id= for one query."""
    days = days_asked(14)
    stats_dict = errors_per_local_day(days, request.args.get('query_id', type=int))
    first_day = get_local_now().date() - timedelta(days=days - 1)

    timeline = []
    for i in range(days):
        date = first_day + timedelta(days=i)
        timeline.append({
            'date': str(date),
            'label': date.strftime('%d/%m'),
            'count': stats_dict.get(str(date), 0)
        })

    return jsonify(timeline)


def errors_per_local_day(days: int, query_id: int = None) -> dict:
    """
    Errors first seen in each of the last `days` local days (today included):
    {'YYYY-MM-DD': count}. Days follow the configured time zone, not UTC.
    """
    errors = db.session.query(ErrorRecord.first_seen_at).filter(
        ErrorRecord.first_seen_at >= local_day_start_utc(max(1, days) - 1))
    if query_id:
        errors = errors.filter(ErrorRecord.query_id == query_id)
    counts = {}
    for (first_seen,) in errors:
        day = str(utc_to_local(first_seen).date())
        counts[day] = counts.get(day, 0) + 1
    return counts


@api_bp.route('/stats/by-query', methods=['GET'])
def api_stats_by_query():
    query_id = request.args.get('query_id', type=int)
    days = days_asked(30)
    
    if not query_id:
        return jsonify({'error': 'query_id required'}), 400
    
    query = db.get_or_404(MonitoredQuery, query_id)
    start_date = get_utc_now() - timedelta(days=days)
    
    total_errors = ErrorRecord.query.filter(
        ErrorRecord.query_id == query_id,
        ErrorRecord.first_seen_at >= start_date
    ).count()
    
    active_errors = ErrorRecord.query.filter(
        ErrorRecord.query_id == query_id,
        ErrorRecord.resolved_at == None
    ).count()
    
    daily = errors_per_local_day(days, query_id)

    return jsonify({
        'query_id': query_id,
        'query_name': query.name,
        'days': days,
        'total_errors': total_errors,
        'active_errors': active_errors,
        'daily': [{'date': date, 'count': count} for date, count in sorted(daily.items())]
    })


@api_bp.route('/stats/all-queries', methods=['GET'])
def api_stats_all_queries():
    from sqlalchemy import func
    
    days = days_asked(30)
    start_date = get_utc_now() - timedelta(days=days)
    
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
        'last_error': format_local(r.last_error, '%d/%m/%y %H:%M') if r.last_error else None
    } for r in results])


# --- Test email, sample email and totals ---

@api_bp.route('/test/email', methods=['POST'])
def api_test_email():
    data = request.get_json(silent=True) or {}
    recipient = str(data.get('recipient') or '').strip()

    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient required'}), 400
    valid, error = validate_email(recipient)
    if not valid:
        return jsonify({'success': False, 'message': error}), 400

    result = email_service.test_email(recipient)
    return jsonify(result)


def sample_rows(data: dict, query_id) -> tuple:
    """
    Rows for the sample email, as (columns, rows, where they come from): those of Test query,
    else the latest errors of the query, else one made-up row with the key fields.
    """
    rows = [row for row in (data.get('rows') or []) if isinstance(row, dict)][:5]
    if rows:
        columns = [str(c) for c in (data.get('columns') or [])] or list(dict.fromkeys(k for r in rows for k in r))
        return columns, rows, 'the rows of Test query'
    if query_id:
        records = ErrorRecord.query.filter_by(query_id=query_id).order_by(ErrorRecord.id.desc()).limit(5).all()
        rows = [record.get_error_data() for record in reversed(records)]
        if rows:
            return list(dict.fromkeys(k for r in rows for k in r)), rows, 'the latest errors of the query'
    fields = [f.strip() for f in str(data.get('key_fields') or '').split(',') if f.strip()] or ['ID']
    return fields, [{field: 'sample' for field in fields}], 'a made-up row (Test query gives real ones)'


@api_bp.route('/test/sample-email', methods=['POST'])
def api_sample_email():
    """The notification email of the query form, as the form is now (saved or not), to the addresses given."""
    data = request.get_json(silent=True) or {}
    valid, error, recipients = validate_email_list(str(data.get('recipients') or ''))
    if not valid or not recipients:
        return jsonify({'success': False, 'message': error or 'Enter an email address'}), 400
    try:
        query_id = int(data.get('query_id') or 0) or None
    except (TypeError, ValueError):
        query_id = None
    query = SimpleNamespace(id=query_id, name=str(data.get('name') or 'Query'),
                            description=str(data.get('description') or ''),
                            email_subject=str(data.get('email_subject') or ''),
                            email_template=str(data.get('email_template') or ''))
    columns, rows, origin = sample_rows(data, query_id)
    result = email_service.send_sample(query, rows, columns, recipients)
    if result['success']:
        result['message'] += f' with {origin}'
    return jsonify(result)


@api_bp.route('/stats', methods=['GET'])
def api_stats():
    return jsonify({
        'total_queries': MonitoredQuery.query.count(),
        'active_queries': MonitoredQuery.query.filter_by(is_active=True).count(),
        'total_active_errors': ErrorRecord.query.filter_by(resolved_at=None).count(),
        'total_resolved_errors': ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count(),
        'total_emails_sent': EmailLog.query.filter_by(status='sent').count(),
        'queries_with_routing': MonitoredQuery.query.filter_by(routing_enabled=True).count()
    })


# --- Cleanup and health check ---

@api_bp.route('/cleanup/run', methods=['POST'])
def api_run_cleanup():
    """Manually cleans up all records."""
    try:
        result = cleanup_service.run_manual_cleanup()
        return jsonify({
            'success': True,
            'message': 'Cleanup done',
            'result': result
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': redact_secrets(str(e))
        }), 500


@api_bp.route('/cleanup/stats', methods=['GET'])
def api_cleanup_stats():
    try:
        stats = cleanup_service.get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({
            'error': redact_secrets(str(e))
        }), 500


@api_bp.route('/health', methods=['GET'])
def api_health():
    """
    Health check for external monitoring: 503 if the internal database does not answer or the scheduler is
    stopped (no round for more than 3 minutes); 200 with status 'degraded' when some queries cannot read their
    source, 'busy' when a query has waited more than a minute for a free thread (ErrorEngine works, late).
    """
    try:
        db_ok = True
        try:
            MonitoredQuery.query.limit(1).all()
        except Exception:
            db_ok = False

        connections_count = DatabaseConnection.query.filter_by(is_active=True).count() if db_ok else 0

        # Scheduler: must be running and have done a round recently
        from scheduler import get_scheduler_status
        sched = get_scheduler_status()
        if not sched['enabled']:
            scheduler_check = 'disabled'
        elif not sched['running']:
            scheduler_check = 'stopped'
        elif sched['last_tick_at'] and get_utc_now() - sched['last_tick_at'] > timedelta(minutes=3):
            scheduler_check = 'stalled'
        elif sched['busy']:
            scheduler_check = 'busy'
        else:
            scheduler_check = 'ok'
        scheduler_ok = scheduler_check in ('ok', 'busy', 'disabled')

        # Active queries that cannot read their source
        failing = sum(1 for q in MonitoredQuery.query.filter(MonitoredQuery.consecutive_failures > 0)
                      if q.is_failing()) if db_ok else 0

        if not (db_ok and scheduler_ok):
            status = 'unhealthy'
        elif failing:
            status = 'degraded'
        elif scheduler_check == 'busy':
            status = 'busy'
        else:
            status = 'healthy'

        body = {
            'status': status,
            'checks': {
                'database': 'ok' if db_ok else 'error',
                'scheduler': scheduler_check,
            },
            'timestamp': get_utc_now().isoformat()
        }
        # Details only for who is signed in or has the API token: without login,
        # the state and nothing that describes the installation
        from auth import is_authenticated
        if is_authenticated():
            body['version'] = __version__
            body['checks'].update({
                'last_scheduler_run': sched['last_tick_at'].isoformat() if sched['last_tick_at'] else None,
                'queries_failing': failing,
                'queries_running': sched['in_flight'] - sched['waiting'],
                'queries_waiting': sched['waiting'],
                'connections_configured': connections_count
            })
        return jsonify(body), 200 if (db_ok and scheduler_ok) else 503
        
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': redact_secrets(str(e))
        }), 503


@api_bp.route('/scheduler/next', methods=['GET'])
def api_scheduler_next():
    try:
        queries = MonitoredQuery.query.filter_by(is_active=True).all()
        
        if not queries:
            return jsonify({
                'has_scheduled': False,
                'message': 'No active queries'
            })
        
        next_run = None
        next_query = None
        
        for query in queries:
            try:
                query_next = query.get_next_run_time()
                if query_next is None:
                    continue
                
                if next_run is None or query_next < next_run:
                    next_run = query_next
                    next_query = query
            except Exception as e:
                logger.debug(f"Error computing the next run for {query.name}: {e}")
                continue
        
        if next_query is None:
            return jsonify({
                'has_scheduled': False,
                'message': 'No scheduled queries'
            })
        
        # Remaining seconds (local time)
        now_local = next_query._get_local_now()
        seconds_remaining = max(0, int((next_run - now_local).total_seconds()))
        
        return jsonify({
            'has_scheduled': True,
            'query_id': next_query.id,
            'query_name': next_query.name,
            'seconds_remaining': seconds_remaining,
            'next_check_at': next_run.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error in api_scheduler_next: {e}")
        return jsonify({
            'has_scheduled': False,
            'error': redact_secrets(str(e))
        }), 500