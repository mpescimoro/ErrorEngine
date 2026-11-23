"""
Routing: field names in any case, every operator, how rules and conditions pick the recipients, the summary.
"""
import itertools

import pytest

from models import RoutingCondition
from routing_service import (OPERATORS, apply_routing_rules, evaluate_condition, get_field_value, get_operators_list,
                             get_routing_summary)
from tests.helpers import make_query

CRITICAL = [('SEVERITY', 'CRITICAL')]
names = itertools.count()


def routed(errors, *rules, **settings):
    """Where each error goes, routing on with the given rules (see tests/helpers.make_query)."""
    query = make_query(name=f'Routed {next(names)}', routing_enabled=True, rules=rules, **settings)
    return apply_routing_rules(query, errors)


def test_field_names_in_any_case():
    error = {'STATUS': 'ERROR', 'CODE': '001'}
    assert get_field_value(error, 'STATUS') == get_field_value(error, 'status') == get_field_value(error, 'Status')
    assert get_field_value(error, 'Status') == 'ERROR' and get_field_value(error, 'MISSING') is None


@pytest.mark.parametrize('operator, cases', [
    ('equals', [('test', 'test', True), ('test', 'TEST', False), ('test', 'other', False)]),
    ('not_equals', [('test', 'other', True), ('test', 'test', False)]),
    ('contains', [('hello world', 'world', True), ('hello world', 'WORLD', False), ('hello', 'world', False)]),
    ('not_contains', [('hello', 'world', True), ('hello world', 'world', False)]),
    ('startswith', [('hello world', 'hello', True), ('hello world', 'world', False)]),
    ('endswith', [('hello world', 'world', True), ('hello world', 'hello', False)]),
    ('in', [('apple', 'apple,banana,cherry', True), ('APPLE', 'apple,banana,cherry', False)]),
    ('not_in', [('grape', 'apple,banana', True), ('apple', 'apple,banana', False)]),
    ('gt', [('10', '5', True), ('5', '10', False), ('invalid', '5', False)]),
    ('gte', [('10', '10', True), ('10', '5', True), ('5', '10', False)]),
    ('lt', [('5', '10', True), ('10', '5', False)]),
    ('lte', [('10', '10', True), ('5', '10', True), ('15', '10', False)]),
    ('is_empty', [('', '', True), ('   ', '', True), ('text', '', False)]),
    ('is_not_empty', [('text', '', True), ('', '', False)]),
    ('regex', [('ERROR-001', r'ERROR-\d+', True), ('WARNING-001', r'ERROR-\d+', False)]),
])
def test_operators_matching_the_case(operator, cases):
    for value, compared, result in cases:
        assert OPERATORS[operator]['fn'](value, compared, True) is result, (value, compared)


def test_operators_ignoring_the_case():
    """A regular expression keeps its meaning: \\D is not lowered to \\d, the match ignores the case instead."""
    assert OPERATORS['in']['fn']('apple', 'APPLE,BANANA', False) is True
    assert OPERATORS['regex']['fn']('error-001', r'ERROR-\d+', False) is True

    def matches(pattern, value):
        condition = RoutingCondition(field_name='CODE', operator='regex', value=pattern, case_sensitive=False)
        return evaluate_condition({'CODE': value}, condition)
    assert matches(r'^\D+$', 'ABC') and not matches(r'^\D+$', '123')
    assert matches('^abc$', 'ABC')


def test_operators_for_the_form():
    operators = get_operators_list()
    assert [op['value'] for op in operators] == list(OPERATORS)
    assert all({'label', 'needs_value'} <= set(op) for op in operators)


class TestRules:
    ERRORS = [{'ID': '1', 'SEVERITY': 'CRITICAL', 'STATUS': 'OPEN'},
              {'ID': '2', 'SEVERITY': 'WARNING', 'STATUS': 'OPEN'},
              {'ID': '3', 'SEVERITY': 'CRITICAL', 'STATUS': 'CLOSED'}]

    def test_each_rule_sends_its_errors(self, app):
        critical, warning, closed = self.ERRORS
        result = routed(self.ERRORS, ('critical@x.it', CRITICAL), ('warning@x.it', [('SEVERITY', 'WARNING')]))
        assert result == {'critical@x.it': [critical, closed], 'warning@x.it': [warning]}

    def test_without_a_match(self, app):
        """send_default: to the default recipients; skip: nowhere."""
        critical, warning, closed = self.ERRORS
        result = routed(self.ERRORS, ('critical@x.it', CRITICAL), routing_default_recipients='default@x.it',
                        routing_no_match_action='send_default')
        assert result == {'critical@x.it': [critical, closed], 'default@x.it': [warning]}
        assert routed([warning], ('critical@x.it', CRITICAL), routing_default_recipients='default@x.it',
                      routing_no_match_action='skip') == {}

    def test_conditions_with_or_and_with_and(self, app):
        open_or_critical = [('SEVERITY', 'CRITICAL'), ('STATUS', 'OPEN')]
        assert routed(self.ERRORS, ('any@x.it', open_or_critical)) == {'any@x.it': self.ERRORS}
        both = routed(self.ERRORS, ('both@x.it', open_or_critical, {'condition_logic': 'AND'}),
                      routing_no_match_action='skip')
        assert both == {'both@x.it': [self.ERRORS[0]]}
        assert routed([{'ID': '4', 'SEVERITY': 'WARNING', 'STATUS': 'CLOSED'}], ('any@x.it', open_or_critical),
                      routing_no_match_action='skip') == {}

    def test_order_and_stop(self, app):
        """Without conditions a rule takes every error; the lowest priority value goes first, and a rule that
        stops keeps the later ones out."""
        error = [{'ID': '1'}]
        assert set(routed(error, ('first@x.it', []), ('second@x.it', []))) == {'first@x.it', 'second@x.it'}
        assert set(routed(error, ('first@x.it', [], {'stop_on_match': True}), ('second@x.it', []))) == {'first@x.it'}
        assert set(routed(error, ('late@x.it', [], {'priority': 10, 'stop_on_match': True}),
                          ('early@x.it', [], {'priority': 0, 'stop_on_match': True}))) == {'early@x.it'}

    def test_a_rule_turned_off_does_not_count(self, app):
        assert routed([{'ID': '1'}], ('off@x.it', [], {'is_active': False}), routing_no_match_action='skip') == {}

    def test_routing_off_sends_every_error_to_every_recipient(self, app):
        query = make_query(name='Classic', email_recipients='a@x.it, b@x.it')
        assert apply_routing_rules(query, self.ERRORS) == {('a@x.it', 'b@x.it'): self.ERRORS}


def test_the_summary(app):
    query = make_query(name='Classic', email_recipients='test@x.it')
    summary = get_routing_summary(query, [{'ID': '1'}, {'ID': '2'}])
    assert (summary['total_errors'], summary['routing_enabled']) == (2, False) and 'test@x.it' in summary['recipients']
    query = make_query(name='Routed', routing_enabled=True, routing_no_match_action='skip',
                       rules=[('x@x.it', [('SEVERITY', 'IMPOSSIBLE')])])
    assert get_routing_summary(query, [{'ID': '1', 'SEVERITY': 'WARNING'}])['unmatched'] == 1
