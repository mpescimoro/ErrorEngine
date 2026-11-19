"""
Validators - Input validation functions for the APIs.
"""
import re
from typing import Tuple, List

from routing_service import OPERATORS

EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def validate_email(email: str) -> Tuple[bool, str]:
    """Validates an email address: (is_valid, error_message)."""
    if not email:
        return False, "Email cannot be empty"
    
    email = email.strip()
    
    if len(email) > 254:
        return False, "Email too long (max 254 characters)"
    
    if not EMAIL_PATTERN.match(email):
        return False, f"Invalid email format: {email}"
    
    return True, ""


def validate_email_list(emails_str: str) -> Tuple[bool, str, List[str]]:
    """Validates comma-separated emails: (is_valid, error_message, emails)."""
    if not emails_str:
        return True, "", []
    
    emails = [e.strip() for e in emails_str.split(',') if e.strip()]
    validated = []
    
    for email in emails:
        is_valid, error = validate_email(email)
        if not is_valid:
            return False, error, []
        validated.append(email)
    
    return True, "", validated


# Quoted text and comments run nothing: in a single pass, so that '--' in a string is not a comment
_SQL_NOT_CODE = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"|\[[^\]]*\]|`[^`]*`|/\*.*?\*/|--[^\n]*", re.S)
# Wherever they are: a CTE with DELETE, SELECT ... INTO, a procedure that can write
_WRITE_WORDS = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|'
                          r'RENAME|EXEC|EXECUTE|CALL|INTO)\b')


def write_statement_keyword(sql: str):
    """
    The word that lets the query change data (e.g. 'DELETE'); None for a read query. Every statement
    must start with SELECT or WITH and contain none of those words. It reads the text: a function that
    writes, called from a SELECT, still gets through, a database user that can only read does not.
    """
    text = _SQL_NOT_CODE.sub(' ', sql or '').upper()
    for statement in text.split(';'):
        first = re.match(r'[\s(]*([A-Z]+)', statement)
        if not first:
            continue
        if first.group(1) not in ('SELECT', 'WITH'):
            return first.group(1)
        found = _WRITE_WORDS.search(statement)
        if found:
            return found.group(1)
    return None


def validate_routing_rule(data: dict) -> Tuple[bool, str]:
    """Validates the data of a routing rule: (is_valid, error_message)."""
    if not isinstance(data, dict):
        return False, "The rule must be a JSON object"
    if not isinstance(data.get('name', ''), str):
        return False, "Rule name: text"
    # Recipients are required
    recipients = data.get('recipients', '')
    if not isinstance(recipients, str):
        return False, "Rule recipients: comma-separated addresses, as text"
    is_valid, error, _ = validate_email_list(recipients)
    if not is_valid:
        return False, f"Rule recipients: {error}"
    
    if not recipients.strip():
        return False, "The rule needs at least one recipient"
    
    # Valid logic
    logic = data.get('condition_logic', 'AND')
    if logic not in ('AND', 'OR'):
        return False, "Logic must be 'AND' or 'OR'"
    
    # Numeric priority
    try:
        priority = int(data.get('priority', 0))
        if priority < 0 or priority > 1000:
            return False, "Priority must be between 0 and 1000"
    except (TypeError, ValueError):
        return False, "Priority must be a number"
    
    # Conditions
    conditions = data.get('conditions', [])
    if not isinstance(conditions, list) or not all(isinstance(cond, dict) for cond in conditions):
        return False, "Conditions must be a list of objects"
    for i, cond in enumerate(conditions):
        field, operator, value = cond.get('field_name'), cond.get('operator'), cond.get('value')
        if not isinstance(field, str) or not field.strip():
            return False, f"Condition {i+1}: field is required"
        if not operator:
            return False, f"Condition {i+1}: operator is required"
        if not isinstance(operator, str) or operator not in OPERATORS:
            # Saved, it would never match
            return False, f"Condition {i+1}: unknown operator {operator!r}"
        # A number from a script is kept as the page sends it, as text
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            return False, f"Condition {i+1}: the value is text or a number"
        if operator == 'regex':
            try:
                re.compile('' if value is None else str(value))
            except re.error as e:
                return False, f"Condition {i+1}: invalid regular expression ({e})"

    return True, ""


def sanitize_string(value: str, max_length: int = 500) -> str:
    """Removes control characters and truncates to max_length."""
    if not value:
        return ""
    
    # Remove control characters
    value = ''.join(char for char in value if ord(char) >= 32 or char in '\n\r\t')
    
    # Truncate if too long
    if len(value) > max_length:
        value = value[:max_length]
    
    return value.strip()
