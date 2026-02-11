"""
Routing Service - Logica per il routing condizionale delle notifiche.
Permette di indirizzare errori a destinatari diversi in base ai valori dei campi.
"""
import re
import logging
from collections import defaultdict
from flask_babel import lazy_gettext as _l
from models import MonitoredQuery, RoutingRule, RoutingCondition

logger = logging.getLogger(__name__)


# Operatori disponibili per le condizioni
OPERATORS = {
    'equals': {
        'label': _l('operator_equals'),
        'fn': lambda f, v, cs: f == v,
        'needs_value': True,
    },
    'not_equals': {
        'label': _l('operator_not_equals'),
        'fn': lambda f, v, cs: f != v,
        'needs_value': True,
    },
    'contains': {
        'label': _l('operator_contains'),
        'fn': lambda f, v, cs: v in f,
        'needs_value': True,
    },
    'not_contains': {
        'label': _l('operator_not_contains'),
        'fn': lambda f, v, cs: v not in f,
        'needs_value': True,
    },
    'startswith': {
        'label': _l('operator_startswith'),
        'fn': lambda f, v, cs: f.startswith(v),
        'needs_value': True,
    },
    'endswith': {
        'label': _l('operator_endswith'),
        'fn': lambda f, v, cs: f.endswith(v),
        'needs_value': True,
    },
    'in': {
        'label': _l('operator_in'),
        'fn': lambda f, v, cs: (f if cs else f.lower()) in [x.strip() if cs else x.strip().lower() for x in v.split(',')],
        'needs_value': True,
        'value_hint': _l('operator_in_hint'),
    },
    'not_in': {
        'label': _l('operator_not_in'),
        'fn': lambda f, v, cs: (f if cs else f.lower()) not in [x.strip() if cs else x.strip().lower() for x in v.split(',')],
        'needs_value': True,
        'value_hint': _l('operator_not_in_hint'),
    },
    'gt': {
        'label': _l('operator_gt'),
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a > b),
        'needs_value': True,
        'value_hint': _l('operator_numeric_hint'),
    },
    'gte': {
        'label': _l('operator_gte'),
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a >= b),
        'needs_value': True,
        'value_hint': _l('operator_numeric_hint'),
    },
    'lt': {
        'label': _l('operator_lt'),
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a < b),
        'needs_value': True,
        'value_hint': _l('operator_numeric_hint'),
    },
    'lte': {
        'label': _l('operator_lte'),
        'fn': lambda f, v, cs: _numeric_compare(f, v, lambda a, b: a <= b),
        'needs_value': True,
        'value_hint': _l('operator_numeric_hint'),
    },
    'is_empty': {
        'label': _l('operator_is_empty'),
        'fn': lambda f, v, cs: not f or str(f).strip() == '',
        'needs_value': False,
    },
    'is_not_empty': {
        'label': _l('operator_is_not_empty'),
        'fn': lambda f, v, cs: bool(f and str(f).strip() != ''),
        'needs_value': False,
    },
    'regex': {
        'label': _l('operator_regex'),
        'fn': lambda f, v, cs: _regex_match(f, v, cs),
        'needs_value': True,
        'value_hint': _l('operator_regex_hint'),
    },
}


def _numeric_compare(field_value, compare_value, comparator):
    """Helper per confronti numerici con gestione errori."""
    try:
        num_field = float(field_value)
        num_compare = float(compare_value)
        return comparator(num_field, num_compare)
    except (ValueError, TypeError):
        return False


def _regex_match(field_value, pattern, case_sensitive):
    """Helper per match regex con gestione errori."""
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        return bool(re.search(pattern, str(field_value), flags))
    except re.error:
        logger.warning(f"Pattern regex non valido: {pattern}")
        return False


def get_field_value(error: dict, field_name: str):
    """
    Ottiene il valore di un campo dall'errore (case-insensitive).
    
    Args:
        error: dizionario con i dati dell'errore
        field_name: nome del campo da cercare
        
    Returns:
        Il valore del campo o None se non trovato
    """
    for key, value in error.items():
        if key.upper() == field_name.upper():
            return value
    return None


def evaluate_condition(error: dict, condition: RoutingCondition) -> bool:
    """
    Valuta se un errore soddisfa una singola condizione.
    
    Args:
        error: dizionario con i dati dell'errore
        condition: RoutingCondition da valutare
        
    Returns:
        True se la condizione è soddisfatta
    """
    # Ottieni valore del campo
    field_value = get_field_value(error, condition.field_name)
    
    # Gestione None/NULL
    if field_value is None:
        if condition.operator == 'is_empty':
            return True
        elif condition.operator == 'is_not_empty':
            return False
        field_value = ''
    
    # Prepara valori per confronto
    field_str = str(field_value)
    compare_value = condition.value or ''
    
    # Case sensitivity
    if not condition.case_sensitive:
        field_str = field_str.lower()
        compare_value = compare_value.lower()
    
    # Ottieni operatore e valuta
    operator_def = OPERATORS.get(condition.operator)
    if not operator_def:
        logger.warning(f"Operatore non riconosciuto: {condition.operator}")
        return False
    
    try:
        return operator_def['fn'](field_str, compare_value, condition.case_sensitive)
    except Exception as e:
        logger.error(f"Errore valutazione condizione: {e}")
        return False


def evaluate_rule(error: dict, rule: RoutingRule) -> bool:
    """
    Valuta se un errore soddisfa tutte/alcune condizioni di una regola.
    
    Args:
        error: dizionario con i dati dell'errore
        rule: RoutingRule da valutare
        
    Returns:
        True se la regola è soddisfatta
    """
    if not rule.is_active:
        return False
    
    if not rule.conditions:
        # Regola senza condizioni = sempre match (catch-all)
        return True
    
    results = [evaluate_condition(error, cond) for cond in rule.conditions]
    
    if rule.condition_logic == 'OR':
        return any(results)
    else:  # AND (default)
        return all(results)


def apply_routing_rules(query: MonitoredQuery, errors: list) -> dict:
    """
    Applica le regole di routing e raggruppa gli errori per destinatario.
    
    Args:
        query: MonitoredQuery con le regole configurate
        errors: lista di dizionari con i dati degli errori
        
    Returns:
        dict: {recipient: [errors]} 
              - Se routing disabilitato: {tuple(recipients): all_errors}
              - Se routing abilitato: {recipient1: [err1, err3], recipient2: [err2], ...}
    """
    if not query.routing_enabled:
        # Comportamento classico: tutti gli errori a tutti i destinatari
        recipients = tuple(query.get_recipients_list())
        if recipients:
            return {recipients: errors}
        return {}
    
    # Routing abilitato: valuta regole per ogni errore
    recipient_errors = defaultdict(list)
    unmatched_errors = []
    
    # Ordina regole per priorità
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
    
    # Gestisci errori senza match
    if unmatched_errors:
        if query.routing_no_match_action == 'send_default':
            default_recipients = query.get_default_routing_recipients()
            for recipient in default_recipients:
                recipient_errors[recipient].extend(unmatched_errors)
        # else: 'skip' - non fare nulla, errori persi (ma loggati)
        
        if unmatched_errors and query.routing_no_match_action == 'skip':
            logger.warning(
                f"Query {query.name}: {len(unmatched_errors)} errori senza match routing (skipped)"
            )
    
    return dict(recipient_errors)


def get_routing_summary(query: MonitoredQuery, errors: list) -> dict:
    """
    Genera un riepilogo del routing per debug/log.
    
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
        
        # Calcola unmatched
        matched_errors = set()
        for errs in routing_result.values():
            for e in errs:
                matched_errors.add(id(e))
        summary['unmatched'] = len(errors) - len(matched_errors)
    
    return summary


def get_operators_list():
    """
    Restituisce la lista degli operatori disponibili per l'UI.
    
    Returns:
        list: [{'value': 'equals', 'label': 'Uguale a', 'needs_value': True}, ...]
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
