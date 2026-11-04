"""
Validators - Input validation functions for the APIs.
"""
import re
from typing import Tuple, List

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


_SQL_COMMENTS = re.compile(r'/\*.*?\*/|--[^\n]*', re.S)


def write_statement_keyword(sql: str):
    """
    First keyword of a statement that is not a SELECT or a WITH, checked like the
    query form does (e.g. 'DELETE'); None for a read query.
    """
    text = _SQL_COMMENTS.sub('', sql or '').lstrip().upper()
    match = re.match(r'[A-Z]+', text)
    if not match or match.group(0) in ('SELECT', 'WITH'):
        return None
    return match.group(0)


def validate_routing_rule(data: dict) -> Tuple[bool, str]:
    """Validates the data of a routing rule: (is_valid, error_message)."""
    # Recipients are required
    recipients = data.get('recipients', '')
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
    for i, cond in enumerate(conditions):
        if not cond.get('field_name'):
            return False, f"Condition {i+1}: field is required"
        if not cond.get('operator'):
            return False, f"Condition {i+1}: operator is required"
        if cond.get('operator') == 'regex':
            try:
                re.compile(cond.get('value') or '')
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
