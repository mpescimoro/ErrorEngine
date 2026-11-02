"""
Routing Service - Conditional routing of notifications.
Sends errors to different recipients depending on the field values.
"""
import re
import logging
from collections import defaultdict
from models import MonitoredQuery, RoutingRule, RoutingCondition

logger = logging.getLogger(__name__)


# Operators available for the conditions
OPERATORS = {
    'equals': {
        'label': 'Equals',
        'fn': lambda f, v, cs: f == v,
        'needs_value': True,
    },
    'not_equals': {
        'label': 'Not equal to',
        'fn': lambda f, v, cs: f != v,
        'needs_value': True,
    },
    'contains': {
        'label': 'Contains',
        'fn': lambda f, v, cs: v in f,
        'needs_value': True,
    },
    'not_contains': {
        'label': 'Does not contain',
        'fn': lambda f, v, cs: v not in f,
        'needs_value': True,
    },
    'startswith': {
        'label': 'Starts with',
        'fn': lambda f, v, cs: f.startswith(v),
        'needs_value': True,
    },
    'endswith': {
        'label': 'Ends with',
        'fn': lambda f, v, cs: f.endswith(v),
        'needs_value': True,
    },
    'in': {
        'label': 'In list',
        'fn': lambda f, v, cs: (f if cs else f.lower()) in [x.strip() if cs else x.strip().lower() for x in v.split(',')],
        'needs_value': True,
        'value_hint': 'Comma-separated values',
    },
    'not_in': {
        'label': 'Not in list',
        'fn': lambda f, v, cs: (f if cs else f.lower()) not in [x.strip() if cs else x.strip().lower() for x in v.split(',')],
        'needs_value': True,
        'value_hint': 'Comma-separated values',
    },
    'gt': {
        'label': 'Greater than',
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a > b),
        'needs_value': True,
        'value_hint': 'Number',
    },
    'gte': {
        'label': 'Greater than or equal to',
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a >= b),
        'needs_value': True,
        'value_hint': 'Number',
    },
    'lt': {
        'label': 'Less than',
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a < b),
        'needs_value': True,
        'value_hint': 'Number',
    },
    'lte': {
        'label': 'Less than or equal to',
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a <= b),
        'needs_value': True,
        'value_hint': 'Number',
    },
    'is_empty': {
        'label': 'Is empty',
        'fn': lambda f, v, cs: not f or str(f).strip() == '',
        'needs_value': False,
    },
    'is_not_empty': {
        'label': 'Is not empty',
        'fn': lambda f, v, cs: bool(f and str(f).strip() != ''),
        'needs_value': False,
    },
    'regex': {
        'label': 'Matches regex',
        'fn': lambda f, v, cs: _regex_match(f, v, cs),
        'needs_value': True,
        'value_hint': 'Regular expression',
    },
}


def _numeric_compare(field_value, compare_value, comparator):
    """Helper for numeric comparisons with error handling."""
    try:
        num_field = float(field_value)
        num_compare = float(compare_value)
        return comparator(num_field, num_compare)
    except (ValueError, TypeError):
        return False


def _regex_match(field_value, pattern, case_sensitive):
    """Helper for regex matching with error handling."""
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        return bool(re.search(pattern, str(field_value), flags))
    except re.error:
        logger.warning(f"Invalid regex pattern: {pattern}")
        return False


def get_field_value(error: dict, field_name: str):
    """
    Gets the value of a field from the error (case-insensitive).
    
    Args:
        error: dictionary with the error data
        field_name: name of the field to look up
        
    Returns:
        The field value, or None if not found
    """
    for key, value in error.items():
        if key.upper() == field_name.upper():
            return value
    return None


def evaluate_condition(error: dict, condition: RoutingCondition) -> bool:
    """
    Checks whether an error satisfies a single condition.
    
    Args:
        error: dictionary with the error data
        condition: RoutingCondition to evaluate
        
    Returns:
        True if the condition is satisfied
    """
    # Field value
    field_value = get_field_value(error, condition.field_name)
    
    # None/NULL handling
    if field_value is None:
        if condition.operator == 'is_empty':
            return True
        elif condition.operator == 'is_not_empty':
            return False
        field_value = ''
    
    # Prepare the values to compare
    field_str = str(field_value)
    compare_value = condition.value or ''
    
    # Case sensitivity. Not for regular expressions: lowercase \D, \S, \W would mean
    # the opposite; _regex_match uses re.IGNORECASE instead.
    if not condition.case_sensitive and condition.operator != 'regex':
        field_str = field_str.lower()
        compare_value = compare_value.lower()
    
    # Get the operator and evaluate
    operator_def = OPERATORS.get(condition.operator)
    if not operator_def:
        logger.warning(f"Unknown operator: {condition.operator}")
        return False
    
    try:
        return operator_def['fn'](field_str, compare_value, condition.case_sensitive)
    except Exception as e:
        logger.error(f"Condition evaluation error: {e}")
        return False


def evaluate_rule(error: dict, rule: RoutingRule) -> bool:
    """
    Checks whether an error satisfies all/any of the conditions of a rule.
    
    Args:
        error: dictionary with the error data
        rule: RoutingRule to evaluate
        
    Returns:
        True if the rule is satisfied
    """
    if not rule.is_active:
        return False
    
    if not rule.conditions:
        # A rule without conditions always matches (catch-all)
        return True
    
    results = [evaluate_condition(error, cond) for cond in rule.conditions]
    
    if rule.condition_logic == 'OR':
        return any(results)
    else:  # AND (default)
        return all(results)


def apply_routing_rules(query: MonitoredQuery, errors: list) -> dict:
    """
    Applies the routing rules and groups the errors by recipient.
    
    Args:
        query: MonitoredQuery with the configured rules
        errors: list of dictionaries with the error data
        
    Returns:
        dict: {recipient: [errors]} 
              - Routing disabled: {tuple(recipients): all_errors}
              - Routing enabled: {recipient1: [err1, err3], recipient2: [err2], ...}
    """
    if not query.routing_enabled:
        # Classic behaviour: every error to every recipient
        recipients = tuple(query.get_recipients_list())
        if recipients:
            return {recipients: errors}
        return {}
    
    # Routing enabled: evaluate the rules for each error
    recipient_errors = defaultdict(list)
    unmatched_errors = []
    
    # Sort the rules by priority
    sorted_rules = sorted(query.routing_rules, key=lambda r: r.priority)
    
    for error in errors:
        matched_recipients = set()
        should_stop = False
        
        for rule in sorted_rules:
            if should_stop:
                break
                
            if evaluate_rule(error, rule):
                for recipient in rule.get_recipients_list():
                    matched_recipients.add(recipient)
                
                if rule.stop_on_match:
                    should_stop = True
        
        if matched_recipients:
            for recipient in matched_recipients:
                recipient_errors[recipient].append(error)
        else:
            unmatched_errors.append(error)
    
    # Errors without a match
    if unmatched_errors:
        if query.routing_no_match_action == 'send_default':
            default_recipients = query.get_default_routing_recipients()
            for recipient in default_recipients:
                recipient_errors[recipient].extend(unmatched_errors)
        # else: 'skip' - do nothing, the errors are dropped (but logged)
        
        if unmatched_errors and query.routing_no_match_action == 'skip':
            logger.warning(
                f"Query {query.name}: {len(unmatched_errors)} errors without a routing match (skipped)"
            )
    
    return dict(recipient_errors)


def get_routing_summary(query: MonitoredQuery, errors: list) -> dict:
    """
    Builds a routing summary for debugging/logging.
    
    Returns:
        dict: {
            'total_errors': int,
            'routing_enabled': bool,
            'recipients': {recipient: count},
            'unmatched': int
        }
    """
    routing_result = apply_routing_rules(query, errors)
    
    summary = {
        'total_errors': len(errors),
        'routing_enabled': query.routing_enabled,
        'recipients': {},
        'unmatched': 0
    }
    
    if not query.routing_enabled:
        recipients = query.get_recipients_list()
        for r in recipients:
            summary['recipients'][r] = len(errors)
    else:
        for recipient, recipient_errors in routing_result.items():
            if isinstance(recipient, tuple):
                for r in recipient:
                    summary['recipients'][r] = len(recipient_errors)
            else:
                summary['recipients'][recipient] = len(recipient_errors)
        
        # Unmatched count
        matched_errors = set()
        for errs in routing_result.values():
            for e in errs:
                matched_errors.add(id(e))
        summary['unmatched'] = len(errors) - len(matched_errors)
    
    return summary


def get_operators_list():
    """
    Returns the list of operators available to the UI.
    
    Returns:
        list: [{'value': 'equals', 'label': 'Equals', 'needs_value': True}, ...]
    """
    return [
        {
            'value': key,
            'label': op['label'],
            'needs_value': op['needs_value'],
            'value_hint': op.get('value_hint', '')
        }
        for key, op in OPERATORS.items()
    ]
