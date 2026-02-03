"""
Validators - Funzioni di validazione input per le API.
"""
import re
from typing import Tuple, List, Optional

# Pattern per email valida
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Pattern per SQL injection basilare (non esaustivo, ma utile)
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
    Valida un indirizzo email.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not email:
        return False, "Email non può essere vuota"
    
    email = email.strip()
    
    if len(email) > 254:
        return False, "Email troppo lunga (max 254 caratteri)"
    
    if not EMAIL_PATTERN.match(email):
        return False, f"Formato email non valido: {email}"
    
    return True, ""


def validate_email_list(emails_str: str) -> Tuple[bool, str, List[str]]:
    """
    Valida una lista di email separate da virgola.
    
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
    Valida il nome di una query.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not name:
        return False, "Nome consultazione obbligatorio"
    
    name = name.strip()
    
    if len(name) < 3:
        return False, "Nome troppo corto (minimo 3 caratteri)"
    
    if len(name) > 100:
        return False, "Nome troppo lungo (massimo 100 caratteri)"
    
    # Solo caratteri alfanumerici, spazi, underscore e trattini
    if not re.match(r'^[\w\s\-]+$', name, re.UNICODE):
        return False, "Nome contiene caratteri non validi"
    
    return True, ""


def validate_key_fields(key_fields: str) -> Tuple[bool, str, List[str]]:
    """
    Valida i campi chiave.
    
    Returns:
        Tuple[bool, str, List[str]]: (is_valid, error_message, fields_list)
    """
    if not key_fields:
        return False, "Almeno un campo chiave è obbligatorio", []
    
    fields = [f.strip() for f in key_fields.split(',') if f.strip()]
    
    if not fields:
        return False, "Almeno un campo chiave è obbligatorio", []
    
    for field in fields:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
            return False, f"Campo chiave non valido: {field}"
    
    return True, "", fields


def validate_sql_query(sql: str) -> Tuple[bool, str]:
    """
    Valida una query SQL (controllo basilare, non esaustivo).
    Verifica solo SELECT e pattern pericolosi evidenti.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not sql:
        return False, "Query SQL obbligatoria"
    
    sql = sql.strip()
    
    if len(sql) > 10000:
        return False, "Query troppo lunga (massimo 10000 caratteri)"
    
    # Deve iniziare con SELECT
    if not sql.upper().startswith('SELECT'):
        return False, "La query deve essere una SELECT"
    
    # Controlla pattern pericolosi
    sql_upper = sql.upper()
    for pattern in SQL_DANGEROUS_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            return False, "Query contiene pattern non consentiti"
    
    return True, ""


def validate_interval(value: int, min_val: int = 1, max_val: int = 1440) -> Tuple[bool, str]:
    """
    Valida un intervallo in minuti.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return False, "Intervallo deve essere un numero intero"
    
    if value < min_val:
        return False, f"Intervallo minimo: {min_val} minuti"
    
    if value > max_val:
        return False, f"Intervallo massimo: {max_val} minuti"
    
    return True, ""


def validate_routing_rule(data: dict) -> Tuple[bool, str]:
    """
    Valida i dati di una regola di routing.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    # Recipients obbligatori
    recipients = data.get('recipients', '')
    is_valid, error, _ = validate_email_list(recipients)
    if not is_valid:
        return False, f"Destinatari regola: {error}"
    
    if not recipients.strip():
        return False, "Almeno un destinatario è obbligatorio per la regola"
    
    # Logic valido
    logic = data.get('condition_logic', 'AND')
    if logic not in ('AND', 'OR'):
        return False, "Logic deve essere 'AND' o 'OR'"
    
    # Priority numerico
    try:
        priority = int(data.get('priority', 0))
        if priority < 0 or priority > 1000:
            return False, "Priorità deve essere tra 0 e 1000"
    except (TypeError, ValueError):
        return False, "Priorità deve essere un numero"
    
    # Condizioni
    conditions = data.get('conditions', [])
    for i, cond in enumerate(conditions):
        if not cond.get('field_name'):
            return False, f"Condizione {i+1}: campo obbligatorio"
        if not cond.get('operator'):
            return False, f"Condizione {i+1}: operatore obbligatorio"
    
    return True, ""


def validate_url(url: str) -> Tuple[bool, str]:
    """
    Valida un URL.
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not url:
        return False, "URL obbligatorio"
    
    url = url.strip()
    
    if not url.startswith(('http://', 'https://')):
        return False, "URL deve iniziare con http:// o https://"
    
    if len(url) > 2000:
        return False, "URL troppo lungo (massimo 2000 caratteri)"
    
    return True, ""


def sanitize_string(value: str, max_length: int = 500) -> str:
    """
    Sanitizza una stringa rimuovendo caratteri potenzialmente pericolosi.
    
    Returns:
        str: Stringa sanitizzata
    """
    if not value:
        return ""
    
    # Rimuovi caratteri di controllo
    value = ''.join(char for char in value if ord(char) >= 32 or char in '\n\r\t')
    
    # Tronca se troppo lunga
    if len(value) > max_length:
        value = value[:max_length]
    
    return value.strip()


class ValidationError(Exception):
    """Eccezione per errori di validazione."""
    
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
