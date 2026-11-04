"""
REST API routes — JSON endpoints.
"""
import logging
from flask import Blueprint, request, jsonify
from datetime import timedelta
from db_drivers import get_driver, driver_timeouts, DRIVER_LABELS
from models import (db, MonitoredQuery, ErrorRecord, EmailLog, RoutingRule,
                    RoutingCondition, DatabaseConnection, NotificationChannel)
from email_service import email_service
from monitor_service import monitor_service
from notification_service import notification_service
from cleanup_service import cleanup_service
from routing_service import get_operators_list, apply_routing_rules, get_routing_summary
from data_sources import test_query_source
from validators import validate_routing_rule, sanitize_string, write_statement_keyword
from utils import (get_utc_now, get_local_now, format_local, local_day_start_utc, utc_to_local,
                   redact_secrets)
from version import __version__


logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/queries', methods=['GET'])
def api_queries_list():
    """API: List of queries."""
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
    """API: Runs a query right away (ignores the time window)."""
    query = MonitoredQuery.query.get_or_404(query_id)
    result = monitor_service.check_query(query, force=True)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/test', methods=['POST'])
def api_test_query(query_id):
    """API: Tests a query without saving the results."""
    query = MonitoredQuery.query.get_or_404(query_id)
    result = test_query_source(query)
    return jsonify(result)


@api_bp.route('/queries/<int:query_id>/toggle', methods=['POST'])
def api_toggle_query(query_id):
    """API: Activates/deactivates a query."""
    query = MonitoredQuery.query.get_or_404(query_id)
    query.is_active = not query.is_active
    db.session.commit()
    
    return jsonify({
        'id': query.id,
        'is_active': query.is_active
    })


@api_bp.route('/queries/<int:query_id>/status', methods=['GET'])
def api_query_status(query_id):
    """API: Current state of a query."""
    MonitoredQuery.query.get_or_404(query_id)
    status = monitor_service.get_query_status(query_id)
    return jsonify(status)


@api_bp.route('/queries/<int:query_id>/fields', methods=['GET'])
def api_query_fields(query_id):
    """API: Fields available from the query (for routing configuration)."""
    fields = monitor_service.get_query_available_fields(query_id)
    return jsonify({'fields': fields})


# --- Routing rules ---

@api_bp.route('/queries/<int:query_id>/routing/rules', methods=['GET'])
def api_get_routing_rules(query_id):
    """API: List of the routing rules of a query."""
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
    """API: Creates a new routing rule."""
    MonitoredQuery.query.get_or_404(query_id)   # 404 if the query does not exist
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
        
        # Add the conditions
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
            'message': 'Rule created'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': redact_secrets(str(e))}), 400


@api_bp.route('/queries/<int:query_id>/routing/rules/<int:rule_id>', methods=['PUT'])
def api_update_routing_rule(query_id, rule_id):
    """API: Updates a routing rule."""
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
                    value=sanitize_string(cond_data.get('value', ''), 500),
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
    """API: Deletes a routing rule."""
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
    """API: Tests the routing with sample data."""
    query = MonitoredQuery.query.get_or_404(query_id)
    data = request.get_json()
    
    test_errors = data.get('errors', [])
    if not test_errors:
        # Use the current real errors
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
    
    # Format the result for the UI
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
    """API: List of the operators available for the conditions."""
    return jsonify(get_operators_list())


# --- Errors ---

@api_bp.route('/errors', methods=['GET'])
def api_errors_list():
    """API: List of active errors."""
    query_id = request.args.get('query_id', type=int)
    errors = monitor_service.get_active_errors(query_id)
    return jsonify(errors)


@api_bp.route('/errors/summary', methods=['GET'])
def api_errors_summary():
    """API: Summary of the active errors, for the automatic page refresh."""
    return jsonify(ErrorRecord.active_summary())


@api_bp.route('/errors/<int:error_id>/resolve', methods=['POST'])
def api_resolve_error(error_id):
    """API: Marks an error as resolved manually."""
    error = ErrorRecord.query.get_or_404(error_id)
    error.resolved_at = get_utc_now()
    db.session.commit()
    
    return jsonify({
        'id': error.id,
        'resolved_at': error.resolved_at.isoformat()
    })


# --- Database connections ---

@api_bp.route('/connections', methods=['GET'])
def api_connections_list():
    """API: List of connections."""
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


@api_bp.route('/connections/<int:conn_id>/test', methods=['POST'])
def api_connection_test(conn_id):
    """API: Tests a saved connection."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    result = conn.test_connection()
    return jsonify(result)


@api_bp.route('/connections/test', methods=['POST'])
def api_connection_test_new():
    """API: Tests a connection not saved yet."""
    data = request.get_json()
    
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
            password=data.get('password')
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': redact_secrets(str(e))})


@api_bp.route('/connections/<int:conn_id>/test-query', methods=['POST'])
def api_connection_test_query(conn_id):
    """API: Tests a query on a specific connection."""
    conn = DatabaseConnection.query.get_or_404(conn_id)
    data = request.get_json() or {}
    sql = data.get('sql')
    
    if not sql:
        return jsonify({'valid': False, 'error': 'SQL query required'}), 400

    # The test runs the statement for real: one that can change data needs the
    # confirmation the form asks for
    keyword = write_statement_keyword(sql)
    if keyword and data.get('confirm_write') is not True:
        return jsonify({'valid': False,
                        'error': f'{keyword} statements can change data: confirm the warning in the query form'}), 400
    
    try:
        driver = conn.get_driver()
        connection = driver.open(
            conn.host, conn.port, conn.database, conn.username, conn.password
        )
        try:
            result = driver.test_query(connection, sql)
            return jsonify(result)
        finally:
            driver.close(connection)
    except Exception as e:
        return jsonify({'valid': False, 'error': redact_secrets(str(e))})


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
    channel = NotificationChannel.query.get_or_404(channel_id)
    result = notification_service.test_channel(channel)
    return jsonify(result)


# --- Tags ---

@api_bp.route('/tags', methods=['GET'])
def api_tags_list():
    """List of existing tags, for autocomplete."""
    all_tags = set()
    for q in MonitoredQuery.query.all():
        all_tags.update(q.get_tags_list())
    return jsonify(sorted(all_tags))


# --- Statistics ---

@api_bp.route('/stats/overview', methods=['GET'])
def api_stats_overview():
    """General statistics."""
    from sqlalchemy import func
    
    days = request.args.get('days', 7, type=int)
    now = get_utc_now()
    today_start = local_day_start_utc()   # local midnight, not UTC
    period_start = now - timedelta(days=days)
    prev_period_start = period_start - timedelta(days=days)
    month_ago = now - timedelta(days=30)
    
    # Errors today
    errors_today = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= today_start
    ).count()
    
    # Errors in the selected period
    errors_period = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= period_start
    ).count()
    
    # Errors in the previous period (for the trend)
    errors_prev_period = ErrorRecord.query.filter(
        ErrorRecord.first_seen_at >= prev_period_start,
        ErrorRecord.first_seen_at < period_start
    ).count()
    
    # Trend
    if errors_prev_period > 0:
        trend_percent = round(((errors_period - errors_prev_period) / errors_prev_period) * 100)
    else:
        trend_percent = 100 if errors_period > 0 else 0
    
    # Active (unresolved) errors
    errors_active = ErrorRecord.query.filter(
        ErrorRecord.resolved_at == None
    ).count()
    
    # Average resolution time (last 30 days)
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
    
    # Top 5 queries by errors
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
    """Errors per day, for the chart: the last N local days, today included."""
    days = max(1, request.args.get('days', 14, type=int))
    stats_dict = errors_per_local_day(days)
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
    """Statistics for a single query."""
    query_id = request.args.get('query_id', type=int)
    days = request.args.get('days', 30, type=int)
    
    if not query_id:
        return jsonify({'error': 'query_id required'}), 400
    
    query = MonitoredQuery.query.get_or_404(query_id)
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
    """Statistics for all queries."""
    from sqlalchemy import func
    
    days = request.args.get('days', 30, type=int)
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


# --- Test email and totals ---

@api_bp.route('/test/email', methods=['POST'])
def api_test_email():
    """API: Test email."""
    data = request.get_json()
    recipient = data.get('recipient')
    
    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient required'}), 400
    
    result = email_service.test_email(recipient)
    return jsonify(result)


@api_bp.route('/stats', methods=['GET'])
def api_stats():
    """API: General statistics."""
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
    """API: Manually cleans up all records."""
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
    """API: Record statistics and retention configuration."""
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
    API: Health check for external monitoring.

    503 if the internal database does not answer or the scheduler is stopped (no round
    for more than 3 minutes); 200 with status 'degraded' when some queries
    cannot read their source.
    """
    try:
        # Internal database
        db_ok = True
        try:
            MonitoredQuery.query.limit(1).all()
        except Exception:
            db_ok = False

        # Configured database connections
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
        else:
            scheduler_check = 'ok'
        scheduler_ok = scheduler_check in ('ok', 'disabled')

        # Active queries that cannot read their source
        failing = MonitoredQuery.query.filter(
            MonitoredQuery.is_active == True,
            MonitoredQuery.consecutive_failures > 0
        ).count() if db_ok else 0

        if not (db_ok and scheduler_ok):
            status = 'unhealthy'
        elif failing:
            status = 'degraded'
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
    """API: Returns the time until the next scheduled check."""
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