"""
Advanced routing tests: stop_on_match, OR logic, no_match, priority.
"""
from models import db, MonitoredQuery, RoutingRule, RoutingCondition
from routing_service import apply_routing_rules, get_routing_summary


class TestStopOnMatch:
    """Tests for stop_on_match: stops the evaluation after the first match."""
    
    def test_stop_on_match_prevents_later_rules(self, app, sample_query):
        """With stop_on_match, later rules are not evaluated."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'default@example.com'
            
            # Rule 1: priority 0, stop_on_match=True
            rule1 = RoutingRule(
                query_id=query.id, name='Catch All',
                condition_logic='AND', recipients='first@example.com',
                priority=0, stop_on_match=True, is_active=True
            )
            db.session.add(rule1)
            # No conditions = catch-all
            
            # Rule 2: priority 1
            rule2 = RoutingRule(
                query_id=query.id, name='Second',
                condition_logic='AND', recipients='second@example.com',
                priority=1, is_active=True
            )
            db.session.add(rule2)
            
            db.session.commit()
            
            errors = [{'ID': '001', 'STATUS': 'ERROR'}]
            result = apply_routing_rules(query, errors)
            
            # Only first@ receives it, second@ does not (blocked by stop_on_match)
            assert 'first@example.com' in result
            assert 'second@example.com' not in result
    
    def test_without_stop_both_match(self, app, sample_query):
        """Without stop_on_match, both rules match."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            
            # Rule 1: NO stop_on_match
            rule1 = RoutingRule(
                query_id=query.id, name='First',
                condition_logic='AND', recipients='first@example.com',
                priority=0, stop_on_match=False, is_active=True
            )
            db.session.add(rule1)
            
            # Rule 2
            rule2 = RoutingRule(
                query_id=query.id, name='Second',
                condition_logic='AND', recipients='second@example.com',
                priority=1, is_active=True
            )
            db.session.add(rule2)
            
            db.session.commit()
            
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            # Both rules match (catch-all without conditions)
            assert 'first@example.com' in result
            assert 'second@example.com' in result


class TestConditionLogicOR:
    """Tests for OR logic between conditions."""
    
    def test_or_any_condition_matches(self, app, sample_query):
        """With OR logic, one true condition is enough."""
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
            
            # Condition 1: SEVERITY = CRITICAL (false for WARNING)
            c1 = RoutingCondition(
                rule_id=rule.id, field_name='SEVERITY',
                operator='equals', value='CRITICAL', case_sensitive=False
            )
            # Condition 2: STATUS = OPEN (true)
            c2 = RoutingCondition(
                rule_id=rule.id, field_name='STATUS',
                operator='equals', value='OPEN', case_sensitive=False
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            
            # Error: SEVERITY=WARNING, STATUS=OPEN → c1 false, c2 true → OR = true
            errors = [{'ID': '001', 'SEVERITY': 'WARNING', 'STATUS': 'OPEN'}]
            result = apply_routing_rules(query, errors)
            
            assert 'or@example.com' in result
    
    def test_or_no_condition_matches(self, app, sample_query):
        """With OR logic, if no condition is true, no match."""
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
            
            # SEVERITY=WARNING, STATUS=CLOSED → both false → OR = false
            errors = [{'ID': '001', 'SEVERITY': 'WARNING', 'STATUS': 'CLOSED'}]
            result = apply_routing_rules(query, errors)
            
            assert len(result) == 0


class TestConditionLogicAND:
    """Tests for AND logic between conditions."""
    
    def test_and_all_conditions_must_match(self, app, sample_query):
        """With AND logic, all conditions must be true."""
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
            
            # Only one condition true → AND fails
            errors = [{'ID': '001', 'SEVERITY': 'CRITICAL', 'STATUS': 'CLOSED'}]
            result = apply_routing_rules(query, errors)
            assert len(result) == 0
            
            # Both true → AND succeeds
            errors = [{'ID': '002', 'SEVERITY': 'CRITICAL', 'STATUS': 'OPEN'}]
            result = apply_routing_rules(query, errors)
            assert 'and@example.com' in result


class TestNoMatchAction:
    """Tests for the behaviour when no rule matches."""
    
    def test_send_default_on_no_match(self, app, sample_query):
        """With send_default, errors without a match go to the default recipients."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_default_recipients = 'fallback@example.com'
            query.routing_no_match_action = 'send_default'
            
            # A rule that will not match
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
            
            # WARNING error: does not match the rule
            errors = [{'ID': '001', 'SEVERITY': 'WARNING'}]
            result = apply_routing_rules(query, errors)
            
            assert 'fallback@example.com' in result
            assert len(result['fallback@example.com']) == 1
    
    def test_skip_on_no_match(self, app, sample_query):
        """With skip, errors without a match are ignored."""
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
            
            # No recipients (error dropped by design)
            assert len(result) == 0


class TestPriorityOrder:
    """Tests for the evaluation order based on priority."""
    
    def test_lower_priority_evaluated_first(self, app, sample_query):
        """Rules with a lower priority value are evaluated first."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            
            # Rule priority=10 (low priority, evaluated later)
            rule_low = RoutingRule(
                query_id=query.id, name='Low Priority',
                condition_logic='AND', recipients='low@example.com',
                priority=10, stop_on_match=True, is_active=True
            )
            
            # Rule priority=0 (high priority, evaluated first)
            rule_high = RoutingRule(
                query_id=query.id, name='High Priority',
                condition_logic='AND', recipients='high@example.com',
                priority=0, stop_on_match=True, is_active=True
            )
            
            db.session.add_all([rule_low, rule_high])
            db.session.commit()
            
            # Both catch-all, but stop_on_match → only the first wins
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            assert 'high@example.com' in result
            assert 'low@example.com' not in result


class TestRoutingDisabled:
    """Tests for disabled routing."""
    
    def test_sends_to_all_recipients(self, app, sample_query):
        """With routing disabled, all errors go to all recipients."""
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
            
            # A single tuple key with both recipients
            assert len(result) == 1
            key = list(result.keys())[0]
            assert isinstance(key, tuple)
            assert 'a@example.com' in key
            assert 'b@example.com' in key
            assert len(result[key]) == 2


class TestInactiveRules:
    """Tests for inactive rules."""
    
    def test_inactive_rule_ignored(self, app, sample_query):
        """Rules with is_active=False are ignored."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            rule = RoutingRule(
                query_id=query.id, name='Disabled',
                condition_logic='AND', recipients='disabled@example.com',
                priority=0, is_active=False  # Inactive
            )
            db.session.add(rule)
            db.session.commit()
            
            errors = [{'ID': '001'}]
            result = apply_routing_rules(query, errors)
            
            assert len(result) == 0


class TestMixedRouting:
    """Realistic scenarios with mixed routing."""
    
    def test_mixed_match_and_no_match(self, app, sample_query):
        """Some errors match a rule, the others go to the default."""
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
    """Tests for the routing summary."""
    
    def test_summary_with_routing_disabled(self, app, sample_query):
        """Summary with routing disabled."""
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
        """The summary counts the errors without a match correctly."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = True
            query.routing_no_match_action = 'skip'
            
            # Impossible rule
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
