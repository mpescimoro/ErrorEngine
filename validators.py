"""
Validators - Input validation functions for the APIs.
"""
import re
from typing import Tuple, List

# Valid email pattern
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Basic SQL injection patterns (not exhaustive, but useful)
SQL_DANGEROUS_PATTERNS = [
    r';\s*DROP\s+',
    r';\s*DELETE\s+',
    r';\s*UPDATE\s+',
    r';\s*INSERT\s+',
    r';\s*ALTER\s+',
    r';\s*CREATE\s+',
    r';\s*TRUNCATE\s+',
    r'--',
    r'/\*.*\*/',
]


def validate_email(email: str) -> Tuple[bool, str]:
    """
    Validates an email address.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not email:
        return False, "Email cannot be empty"
    
    email = email.strip()
    
    if len(email) > 254:
        return False, "Email too long (max 254 characters)"
    
    if not EMAIL_PATTERN.match(email):
        return False, f"Invalid email format: {email}"
    
    return True, ""


def validate_email_list(emails_str: str) -> Tuple[bool, str, List[str]]:
    """
    Validates a comma-separated list of emails.
    
    Returns:
        Tuple[bool, str, List[str]]: (is_valid, error_message, validated_emails)
    """
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


def validate_query_name(name: str) -> Tuple[bool, str]:
    """
    Validates the name of a query.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not name:
        return False, "Query name is required"
    
    name = name.strip()
    
    if len(name) < 3:
        return False, "Name too short (minimum 3 characters)"
    
    if len(name) > 100:
        return False, "Name too long (maximum 100 characters)"
    
    # Only letters, digits, spaces, underscores and hyphens
    if not re.match(r'^[\w\s\-]+$', name, re.UNICODE):
        return False, "Name contains invalid characters"
    
    return True, ""


def validate_key_fields(key_fields: str) -> Tuple[bool, str, List[str]]:
    """
    Validates the key fields.
    
    Returns:
        Tuple[bool, str, List[str]]: (is_valid, error_message, fields_list)
    """
    if not key_fields:
        return False, "At least one key field is required", []
    
    fields = [f.strip() for f in key_fields.split(',') if f.strip()]
    
    if not fields:
        return False, "At least one key field is required", []
    
    for field in fields:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
            return False, f"Invalid key field: {field}", []
    
    return True, "", fields


def validate_sql_query(sql: str) -> Tuple[bool, str]:
    """
    Validates a SQL query (basic check, not exhaustive).
    Only checks for SELECT and obvious dangerous patterns.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not sql:
        return False, "SQL query is required"
    
    sql = sql.strip()
    
    if len(sql) > 10000:
        return False, "Query too long (maximum 10000 characters)"
    
    # Must start with SELECT
    if not sql.upper().startswith('SELECT'):
        return False, "The query must be a SELECT"
    
    # Check for dangerous patterns
    sql_upper = sql.upper()
    for pattern in SQL_DANGEROUS_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            return False, "Query contains patterns that are not allowed"
    
    return True, ""


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


def validate_interval(value: int, min_val: int = 1, max_val: int = 1440) -> Tuple[bool, str]:
    """
    Validates an interval in minutes.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return False, "Interval must be an integer"
    
    if value < min_val:
        return False, f"Minimum interval: {min_val} minutes"
    
    if value > max_val:
        return False, f"Maximum interval: {max_val} minutes"
    
    return True, ""


def validate_routing_rule(data: dict) -> Tuple[bool, str]:
    """
    Validates the data of a routing rule.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
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


def validate_url(url: str) -> Tuple[bool, str]:
    """
    Validates a URL.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not url:
        return False, "URL is required"
    
    url = url.strip()
    
    if not url.startswith(('http://', 'https://')):
        return False, "URL must start with http:// or https://"
    
    if len(url) > 2000:
        return False, "URL too long (maximum 2000 characters)"
    
    return True, ""


def sanitize_string(value: str, max_length: int = 500) -> str:
    """
    Sanitizes a string by removing potentially dangerous characters.
    
    Returns:
        str: Sanitized string
    """
    if not value:
        return ""
    
    # Remove control characters
    value = ''.join(char for char in value if ord(char) >= 32 or char in '\n\r\t')
    
    # Truncate if too long
    if len(value) > max_length:
        value = value[:max_length]
    
    return value.strip()


class ValidationError(Exception):
    """Exception for validation errors."""
    
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        return {
            'error': 'validation_error',
            'message': self.message,
            'field': self.field
        }
