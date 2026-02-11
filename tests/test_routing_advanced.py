"""
Test avanzati per il routing: stop_on_match, logica OR, no_match, priorità.
"""
import pytest
from models import db, MonitoredQuery, RoutingRule, RoutingCondition
from routing_service import apply_routing_rules, evaluate_rule, evaluate_condition, get_routing_summary


class TestStopOnMatch:
    """Test per stop_on_match: ferma la valutazione dopo il primo match."""
    
    def test_stop_on_match_prevents_later_rules(self, app, sample_query):
        """Con stop_on_match, regole successive non vengono valutate."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'default@example.com'
            
            # Regola 1: priority 0, stop_on_match=True
            rule1 = RoutingRule(
                query_id=query.id, name='Catch All',
                condition_logic='AND', recipients='first@example.com',
                priority=0, stop_on_match=True, is_active=True
            )
            db.session.add(rule1)
            # Nessuna condizione = catch-all
            
            # Regola 2: priority 1
            rule2 = RoutingRule(
                query_id=query.id, name='Second',
                condition_logic='AND', recipients='second@example.com',
                priority=1, is_active=True
            )
            db.session.add(rule2)
            
            db.session.commit()
            
            errors = [{'ID': '001', 'STATUS': 'ERROR'}]
            result = apply_routing_rules(query, errors)
            
            # Solo first@ riceve, second@ no (bloccato da stop_on_match)
            assert 'first@example.com' in result
            assert 'second@example.com' not in result
    
    def test_without_stop_both_match(self, app, sample_query):
        """Senza stop_on_match, entrambe le regole matchano."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            
            # Regola 1: NO stop_on_match
            rule1 = RoutingRule(
                query_id=query.id, name='First',
                condition_logic='AND', recipients='first@example.com',
                priority=0, stop_on_match=False, is_active=True
            )
            db.session.add(rule1)
            
            # Regola 2
            rule2 = RoutingRule(
                query_id=query.id, name='Second',
                condition_logic='AND', recipients='second@example.com',
                priority=1, is_active=True
            )
            db.session.add(rule2)
            
            db.session.commit()
            
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            # Entrambe le regole matchano (catch-all senza condizioni)
            assert 'first@example.com' in result
            assert 'second@example.com' in result


class TestConditionLogicOR:
    """Test per logica OR tra condizioni."""
    
    def test_or_any_condition_matches(self, app, sample_query):
        """Con logica OR, basta una condizione vera."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            
            rule = RoutingRule(
                query_id=query.id, name='OR Rule',
                condition_logic='OR', recipients='or@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            # Condizione 1: SEVERITY = CRITICAL (falsa per WARNING)
            c1 = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            # Condizione 2: STATUS = OPEN (vera)
            c2 = RoutingCondition(
                rule_id=rule.id, field_name='STATUS',
                operator='equals', value='OPEN', case_sensitive=False
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            
            # Errore: SEVERITY=WARNING, STATUS=OPEN → c1 false, c2 true → OR = true
            errors = [{'ID': '001', 'SEVERITY': 'WARNING', 'STATUS': 'OPEN'}]
            result = apply_routing_rules(query, errors)
            
            assert 'or@example.com' in result
    
    def test_or_no_condition_matches(self, app, sample_query):
        """Con logica OR, se nessuna condizione è vera, no match."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            rule = RoutingRule(
                query_id=query.id, name='OR Rule',
                condition_logic='OR', recipients='or@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c1 = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            c2 = RoutingCondition(
                rule_id=rule.id, field_name='STATUS',
                operator='equals', value='OPEN', case_sensitive=False
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            
            # SEVERITY=WARNING, STATUS=CLOSED → entrambe false → OR = false
            errors = [{'ID': '001', 'SEVERITY': 'WARNING', 'STATUS': 'CLOSED'}]
            result = apply_routing_rules(query, errors)
            
            assert len(result) == 0


class TestConditionLogicAND:
    """Test per logica AND tra condizioni."""
    
    def test_and_all_conditions_must_match(self, app, sample_query):
        """Con logica AND, tutte le condizioni devono essere vere."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            rule = RoutingRule(
                query_id=query.id, name='AND Rule',
                condition_logic='AND', recipients='and@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c1 = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            c2 = RoutingCondition(
                rule_id=rule.id, field_name='STATUS',
                operator='equals', value='OPEN', case_sensitive=False
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            
            # Solo una condizione vera → AND fallisce
            errors = [{'ID': '001', 'SEVERITY': 'CRITICAL', 'STATUS': 'CLOSED'}]
            result = apply_routing_rules(query, errors)
            assert len(result) == 0
            
            # Entrambe vere → AND succede
            errors = [{'ID': '002', 'SEVERITY': 'CRITICAL', 'STATUS': 'OPEN'}]
            result = apply_routing_rules(query, errors)
            assert 'and@example.com' in result


class TestNoMatchAction:
    """Test per il comportamento quando nessuna regola matcha."""
    
    def test_send_default_on_no_match(self, app, sample_query):
        """Con send_default, errori senza match vanno ai destinatari default."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'fallback@example.com'
            query.routing_no_match_action = 'send_default'
            
            # Regola che non matcherà
            rule = RoutingRule(
                query_id=query.id, name='Only Critical',
                condition_logic='AND', recipients='critical@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            db.session.add(c)
            db.session.commit()
            
            # Errore WARNING: non matcha la regola
            errors = [{'ID': '001', 'SEVERITY': 'WARNING'}]
            result = apply_routing_rules(query, errors)
            
            assert 'fallback@example.com' in result
            assert len(result['fallback@example.com']) == 1
    
    def test_skip_on_no_match(self, app, sample_query):
        """Con skip, errori senza match vengono ignorati."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'fallback@example.com'
            query.routing_no_match_action = 'skip'
            
            rule = RoutingRule(
                query_id=query.id, name='Only Critical',
                condition_logic='AND', recipients='critical@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            db.session.add(c)
            db.session.commit()
            
            errors = [{'ID': '001', 'SEVERITY': 'WARNING'}]
            result = apply_routing_rules(query, errors)
            
            # Nessun destinatario (errore perso per design)
            assert len(result) == 0


class TestPriorityOrder:
    """Test per l'ordine di valutazione basato su priorità."""
    
    def test_lower_priority_evaluated_first(self, app, sample_query):
        """Regole con priorità più bassa vengono valutate prima."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            
            # Regola priority=10 (bassa priorità, valutata dopo)
            rule_low = RoutingRule(
                query_id=query.id, name='Low Priority',
                condition_logic='AND', recipients='low@example.com',
                priority=10, stop_on_match=True, is_active=True
            )
            
            # Regola priority=0 (alta priorità, valutata prima)
            rule_high = RoutingRule(
                query_id=query.id, name='High Priority',
                condition_logic='AND', recipients='high@example.com',
                priority=0, stop_on_match=True, is_active=True
            )
            
            db.session.add_all([rule_low, rule_high])
            db.session.commit()
            
            # Entrambe catch-all, ma stop_on_match → solo la prima vince
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            assert 'high@example.com' in result
            assert 'low@example.com' not in result


class TestRoutingDisabled:
    """Test per routing disabilitato."""
    
    def test_sends_to_all_recipients(self, app, sample_query):
        """Con routing disabilitato, tutti gli errori vanno a tutti i destinatari."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = False
            query.email_recipients = 'a@example.com, b@example.com'
            db.session.commit()
            
            errors = [
                {'ID': '001', 'SEVERITY': 'CRITICAL'},
                {'ID': '002', 'SEVERITY': 'WARNING'},
            ]
            result = apply_routing_rules(query, errors)
            
            # Un'unica chiave tuple con entrambi i destinatari
            assert len(result) == 1
            key = list(result.keys())[0]
            assert isinstance(key, tuple)
            assert 'a@example.com' in key
            assert 'b@example.com' in key
            assert len(result[key]) == 2


class TestInactiveRules:
    """Test per regole disattivate."""
    
    def test_inactive_rule_ignored(self, app, sample_query):
        """Regole con is_active=False vengono ignorate."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            rule = RoutingRule(
                query_id=query.id, name='Disabled',
                condition_logic='AND', recipients='disabled@example.com',
                priority=0, is_active=False  # Disattivata
            )
            db.session.add(rule)
            db.session.commit()
            
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            assert len(result) == 0


class TestMixedRouting:
    """Test scenari realistici con routing misto."""
    
    def test_mixed_match_and_no_match(self, app, sample_query):
        """Alcuni errori matchano una regola, altri vanno al default."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'default@example.com'
            query.routing_no_match_action = 'send_default'
            
            rule = RoutingRule(
                query_id=query.id, name='Critical Only',
                condition_logic='AND', recipients='critical@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            db.session.add(c)
            db.session.commit()
            
            errors = [
                {'ID': '001', 'SEVERITY': 'CRITICAL'},
                {'ID': '002', 'SEVERITY': 'WARNING'},
                {'ID': '003', 'SEVERITY': 'CRITICAL'},
            ]
            result = apply_routing_rules(query, errors)
            
            # 2 CRITICAL → critical@, 1 WARNING → default@
            assert len(result['critical@example.com']) == 2
            assert len(result['default@example.com']) == 1


class TestGetRoutingSummary:
    """Test per il riepilogo routing."""
    
    def test_summary_with_routing_disabled(self, app, sample_query):
        """Riepilogo con routing disabilitato."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = False
            query.email_recipients = 'test@example.com'
            db.session.commit()
            
            errors = [{'ID': '001'}, {'ID': '002'}]
            summary = get_routing_summary(query, errors)
            
            assert summary['total_errors'] == 2
            assert summary['routing_enabled'] is False
            assert 'test@example.com' in summary['recipients']
    
    def test_summary_counts_unmatched(self, app, sample_query):
        """Riepilogo conta correttamente gli errori senza match."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            # Regola impossibile
            rule = RoutingRule(
                query_id=query.id, name='Impossible',
                condition_logic='AND', recipients='x@example.com',
                priority=0, is_active=True
            )
            db.session.add(rule)
            db.session.flush()
            
            c = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='IMPOSSIBLE', case_sensitive=False
            )
            db.session.add(c)
            db.session.commit()
            
            errors = [{'ID': '001', 'SEVERITY': 'WARNING'}]
            summary = get_routing_summary(query, errors)
            
            assert summary['unmatched'] == 1
