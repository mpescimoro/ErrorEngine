"""
Test per il modulo routing_service.
"""
import pytest
from routing_service import (
    evaluate_condition, evaluate_rule, apply_routing_rules,
    get_field_value, get_operators_list, OPERATORS
)
from models import RoutingCondition, RoutingRule, MonitoredQuery


class TestGetFieldValue:
    """Test per get_field_value."""
    
    def test_exact_match(self):
        error = {'STATUS': 'ERROR', 'CODE': '001'}
        assert get_field_value(error, 'STATUS') == 'ERROR'
    
    def test_case_insensitive(self):
        error = {'STATUS': 'ERROR'}
        assert get_field_value(error, 'status') == 'ERROR'
        assert get_field_value(error, 'Status') == 'ERROR'
    
    def test_not_found(self):
        error = {'STATUS': 'ERROR'}
        assert get_field_value(error, 'MISSING') is None


class TestOperators:
    """Test per gli operatori di routing."""
    
    def test_equals(self):
        fn = OPERATORS['equals']['fn']
        assert fn('test', 'test', True) is True
        assert fn('test', 'TEST', True) is False
        assert fn('test', 'other', True) is False
    
    def test_not_equals(self):
        fn = OPERATORS['not_equals']['fn']
        assert fn('test', 'other', True) is True
        assert fn('test', 'test', True) is False
    
    def test_contains(self):
        fn = OPERATORS['contains']['fn']
        assert fn('hello world', 'world', True) is True
        assert fn('hello world', 'WORLD', True) is False
        assert fn('hello', 'world', True) is False
    
    def test_not_contains(self):
        fn = OPERATORS['not_contains']['fn']
        assert fn('hello', 'world', True) is True
        assert fn('hello world', 'world', True) is False
    
    def test_startswith(self):
        fn = OPERATORS['startswith']['fn']
        assert fn('hello world', 'hello', True) is True
        assert fn('hello world', 'world', True) is False
    
    def test_endswith(self):
        fn = OPERATORS['endswith']['fn']
        assert fn('hello world', 'world', True) is True
        assert fn('hello world', 'hello', True) is False
    
    def test_in_list(self):
        fn = OPERATORS['in']['fn']
        # case sensitive
        assert fn('apple', 'apple,banana,cherry', True) is True
        assert fn('APPLE', 'apple,banana,cherry', True) is False
        # case insensitive
        assert fn('apple', 'APPLE,BANANA', False) is True
    
    def test_not_in_list(self):
        fn = OPERATORS['not_in']['fn']
        assert fn('grape', 'apple,banana', True) is True
        assert fn('apple', 'apple,banana', True) is False
    
    def test_gt(self):
        fn = OPERATORS['gt']['fn']
        assert fn('10', '5', True) is True
        assert fn('5', '10', True) is False
        assert fn('invalid', '5', True) is False
    
    def test_gte(self):
        fn = OPERATORS['gte']['fn']
        assert fn('10', '10', True) is True
        assert fn('10', '5', True) is True
        assert fn('5', '10', True) is False
    
    def test_lt(self):
        fn = OPERATORS['lt']['fn']
        assert fn('5', '10', True) is True
        assert fn('10', '5', True) is False
    
    def test_lte(self):
        fn = OPERATORS['lte']['fn']
        assert fn('10', '10', True) is True
        assert fn('5', '10', True) is True
        assert fn('15', '10', True) is False
    
    def test_is_empty(self):
        fn = OPERATORS['is_empty']['fn']
        assert fn('', '', True) is True
        assert fn('   ', '', True) is True
        assert fn('text', '', True) is False
    
    def test_is_not_empty(self):
        fn = OPERATORS['is_not_empty']['fn']
        assert fn('text', '', True) is True
        assert fn('', '', True) is False
    
    def test_regex(self):
        fn = OPERATORS['regex']['fn']
        assert fn('ERROR-001', r'ERROR-\d+', True) is True
        assert fn('WARNING-001', r'ERROR-\d+', True) is False
        # case insensitive
        assert fn('error-001', r'ERROR-\d+', False) is True


class TestGetOperatorsList:
    """Test per get_operators_list."""
    
    def test_returns_list(self):
        operators = get_operators_list()
        assert isinstance(operators, list)
        assert len(operators) > 0
    
    def test_operator_structure(self):
        operators = get_operators_list()
        for op in operators:
            assert 'value' in op
            assert 'label' in op
            assert 'needs_value' in op


class TestApplyRoutingRules:
    """Test per apply_routing_rules con fixture."""
    
    def test_routing_disabled(self, app, sample_query):
        """Se routing disabilitato, tutti gli errori vanno ai destinatari di default."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.routing_enabled = False
            
            errors = [
                {'ID': '1', 'STATUS': 'ERROR'},
                {'ID': '2', 'STATUS': 'WARNING'}
            ]
            
            result = apply_routing_rules(query, errors)
            
            # Dovrebbe avere una sola chiave (tuple di destinatari)
            assert len(result) == 1
            # Tutti gli errori dovrebbero essere assegnati
            recipients = list(result.keys())[0]
            assert len(result[recipients]) == 2
    
    def test_routing_with_rules(self, app, sample_query_with_routing, sample_error_data):
        """Test routing con regole attive."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query_with_routing.id)
            
            result = apply_routing_rules(query, sample_error_data)
            
            # Verifica che critical@example.com riceva l'errore critico
            assert 'critical@example.com' in result
            critical_errors = result['critical@example.com']
            assert len(critical_errors) == 1
            assert critical_errors[0]['SEVERITY'] == 'CRITICAL'
            
            # Verifica che warning@example.com riceva il warning
            assert 'warning@example.com' in result
            warning_errors = result['warning@example.com']
            assert len(warning_errors) == 1
            assert warning_errors[0]['SEVERITY'] == 'WARNING'
