"""
Servizio per l'invio di email tramite SMTP (Exchange).
Supporta routing condizionale e reminder.
"""
import smtplib
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app, render_template_string
from datetime import datetime, timezone
from models import db, EmailLog
from utils import format_local_now

logger = logging.getLogger(__name__)


# Template HTML di default per le notifiche nuovi errori
def load_email_template(template_name=None):
    """
    Carica un template email.
    
    Args:
        template_name: Nome del template (senza .html) o None per default
        
    Returns:
        str: Contenuto del template
    """
    if template_name is None:
        template_name = 'default'
    
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'email', f'{template_name}.html')
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Template email non trovato: {template_path}")
        if template_name != 'default':
            # Fallback a default se template custom non trovato
            logger.info("Fallback a template default")
            return load_email_template('default')
        # Fallback inline se neanche default esiste
    return """
<table cellpadding="5" cellspacing="0" border="1" style="border-collapse:collapse;font-family:Arial;font-size:12px;">
<tr style="background:#0d9488;color:#fff;">
    <th colspan="{{ columns|length }}" align="left">{{ query_name }}</th>
</tr>
<tr>
    <td colspan="{{ columns|length }}">
        {{ check_time|localtime('%d/%m/%Y %H:%M:%S') }} · Errori: {{ error_count }}
    </td>
</tr>
<tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr>
{% for e in errors %}
<tr>{% for c in columns %}<td>{{ e[c] or '' }}</td>{% endfor %}</tr>
{% endfor %}
</table>
"""


def resolve_template(template_field):
    """
    Risolve il contenuto del campo template.
    
    Args:
        template_field: Valore del campo email_template della query
        
    Returns:
        str: Template HTML da usare
        
    Logica:
        - Vuoto/None → carica default.html
        - {nome_template} → carica nome_template.html
        - Altro → usa come HTML diretto
    """
    if not template_field or not template_field.strip():
        return load_email_template('default')
    
    template_field = template_field.strip()
    
    # Check se è un riferimento a template: {nome}
    if template_field.startswith('{') and template_field.endswith('}'):
        template_name = template_field[1:-1].strip()
        if template_name:
            return load_email_template(template_name)
        else:
            return load_email_template('default')
    
    # Altrimenti è HTML custom diretto
    return template_field

class EmailService:
    """
    Gestisce l'invio di email di notifica.
    Supporta routing condizionale tramite recipients_override.
    """
    
    def __init__(self, app=None):
        self.app = app
        
    def init_app(self, app):
        """Inizializza l'estensione Flask."""
        self.app = app
        app.extensions['email'] = self
    
    def _get_smtp_connection(self):
        """Crea una connessione SMTP."""
        try:
            if current_app.config['MAIL_USE_TLS']:
                server = smtplib.SMTP(
                    current_app.config['MAIL_SERVER'],
                    current_app.config['MAIL_PORT']
                )
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(
                    current_app.config['MAIL_SERVER'],
                    current_app.config['MAIL_PORT']
                )
            
            server.login(
                current_app.config['MAIL_USERNAME'],
                current_app.config['MAIL_PASSWORD']
            )
            return server
        except Exception as e:
            logger.error(f"Errore connessione SMTP: {e}")
            raise
    
    def send_error_notification(self, query, errors: list, columns: list,
                                recipients_override: list = None,
                                email_type: str = 'new_errors') -> dict:
        """
        Invia una notifica email per gli errori.
        
        Args:
            query: Oggetto MonitoredQuery
            errors: Lista di dizionari con i dati degli errori
            columns: Lista dei nomi delle colonne
            recipients_override: Se specificato, usa questi destinatari invece di quelli della query
            email_type: 'new_errors' o 'reminder'
            
        Returns:
            dict: {'success': bool, 'message': str, 'recipients': list}
        """
        # Determina destinatari
        if recipients_override:
            recipients = recipients_override
        else:
            recipients = query.get_recipients_list()
        
        if not recipients:
            return {
                'success': False,
                'message': 'Nessun destinatario configurato',
                'recipients': []
            }
        
        if not errors:
            return {
                'success': True,
                'message': 'Nessun errore da notificare',
                'recipients': []
            }
        
        # Prepara il template
        template = resolve_template(query.email_template)
        
        # Prepara subject
        subject_template = query.email_subject
        if email_type == 'reminder':
            # Prefisso REMINDER se non già presente
            if 'REMINDER' not in subject_template.upper():
                subject_template = '[REMINDER] ' + subject_template
        
        try:
            # Renderizza il template
            html_content = render_template_string(
                template,
                query_name=query.name,
                query_description=query.description,
                check_time=datetime.now(timezone.utc),
                error_count=len(errors),
                errors=errors,
                columns=columns,
                email_type=email_type
            )
            
            # Crea il messaggio
            msg = MIMEMultipart('alternative')
            
            # Formatta subject con variabili
            subject = subject_template
            if '{' in subject:
                subject = subject.format(
                    query_name=query.name,
                    error_count=len(errors)
                )
            
            msg['Subject'] = subject
            msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
            msg['To'] = ', '.join(recipients)
            
            # Versione plain text
            error_type_text = 'ancora attivi (REMINDER)' if email_type == 'reminder' else 'nuovi'
            plain_text = f"""
{'[REMINDER] ' if email_type == 'reminder' else ''}Errori rilevati - {query.name}

Consultazione: {query.name}
Descrizione: {query.description or 'N/A'}
Data controllo: {format_local_now()}
Errori {error_type_text}: {len(errors)}

{'⚠️ Questo è un promemoria - questi errori non sono ancora stati risolti.' if email_type == 'reminder' else ''}

Accedi al pannello di controllo per visualizzare i dettagli.
            """
            
            msg.attach(MIMEText(plain_text, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            # Invia
            server = self._get_smtp_connection()
            server.sendmail(
                current_app.config['MAIL_DEFAULT_SENDER'],
                recipients,
                msg.as_string()
            )
            server.quit()
            
            # Log dell'invio
            email_log = EmailLog(
                query_id=query.id,
                recipients=', '.join(recipients),
                subject=subject,
                error_count=len(errors),
                email_type=email_type,
                status='sent'
            )
            db.session.add(email_log)
            db.session.commit()
            
            logger.info(
                f"Email {email_type} inviata per {query.name} a {len(recipients)} destinatari"
            )
            
            return {
                'success': True,
                'message': f'Email inviata a {len(recipients)} destinatari',
                'recipients': recipients
            }
            
        except Exception as e:
            # Log dell'errore
            email_log = EmailLog(
                query_id=query.id,
                recipients=', '.join(recipients),
                subject=query.email_subject,
                error_count=len(errors),
                email_type=email_type,
                status='failed',
                error_message=str(e)
            )
            db.session.add(email_log)
            db.session.commit()
            
            logger.error(f"Errore invio email per {query.name}: {e}")
            
            return {
                'success': False,
                'message': str(e),
                'recipients': recipients
            }
    
    def test_email(self, recipient: str) -> dict:
        """
        Invia un'email di test.
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            msg = MIMEMultipart()
            msg['Subject'] = '[ErrorEngine] Email di test'
            msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
            msg['To'] = recipient
            
            html = """
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #5cb85c;">✅ Test connessione email riuscito!</h2>
                <p>La configurazione SMTP è corretta e le email possono essere inviate.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="color: #666; font-size: 12px;">
                    <strong>Server:</strong> {server}<br>
                    <strong>Porta:</strong> {port}<br>
                    <strong>Data test:</strong> {date}
                </p>
            </body>
            </html>
            """.format(
                server=current_app.config['MAIL_SERVER'],
                port=current_app.config['MAIL_PORT'],
                date=format_local_now()
            )
            
            msg.attach(MIMEText(html, 'html'))
            
            server = self._get_smtp_connection()
            server.sendmail(
                current_app.config['MAIL_DEFAULT_SENDER'],
                [recipient],
                msg.as_string()
            )
            server.quit()
            
            return {'success': True, 'message': f'Email di test inviata a {recipient}'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}


# Singleton
email_service = EmailService()
