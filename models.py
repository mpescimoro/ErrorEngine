"""
Modelli del database SQLite per il monitoraggio di ErrorEngine 
"""
from datetime import datetime, time, timedelta
from utils import get_utc_now
from flask_sqlalchemy import SQLAlchemy
import hashlib
import json

db = SQLAlchemy()


class MonitoredQuery(db.Model):
    """
    Definizione delle consultazioni da monitorare.
    Supporta sorgenti multiple e routing condizionale.
    """
    __tablename__ = 'monitored_queries'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    
    # === SORGENTE DATI ===
    source_type = db.Column(db.String(20), default='database')  # database, http
    
    # Connessione database (per sorgenti SQL)
    db_connection_id = db.Column(db.Integer, db.ForeignKey('database_connections.id'), nullable=True)
    
    # Per database: query SQL
    sql_query = db.Column(db.Text)
    
    # Per HTTP/API: configurazione JSON
    # HTTP: {"url": "...", "method": "GET/POST", "headers": {...}, "body": {...}, "response_path": "data.items"}
    # API: {"endpoint": "...", "auth_type": "bearer/basic/api_key", "auth_value": "...", ...}
    source_config = db.Column(db.Text)
    
    # Campi che identificano univocamente un errore (separati da virgola)
    key_fields = db.Column(db.String(500), nullable=False)
    
    # === CONFIGURAZIONE EMAIL BASE ===
    email_subject = db.Column(db.String(200), default='[ErrorEngine] Nuovi errori: {query_name}')
    email_recipients = db.Column(db.Text)  # Email separate da virgola
    email_template = db.Column(db.Text)  # Template HTML personalizzato
    
    # === SCHEDULING AVANZATO ===
    check_interval_minutes = db.Column(db.Integer, default=15)
    is_active = db.Column(db.Boolean, default=True)
    
    # Fascia oraria di esecuzione
    schedule_start_time = db.Column(db.Time)  # es. 08:00
    schedule_end_time = db.Column(db.Time)    # es. 20:00
    schedule_days = db.Column(db.String(20))  # es. "1,2,3,4,5" (lun-ven, ISO weekday)
    schedule_reference_time = db.Column(db.Time)  # Ora di riferimento per le esecuzioni (es. 00:00)
    
    # === REMINDER ===
    reminder_enabled = db.Column(db.Boolean, default=False)
    reminder_interval_minutes = db.Column(db.Integer, default=60)
    reminder_max_count = db.Column(db.Integer, default=5)  # Max reminder per errore
    
    # === ROUTING CONDIZIONALE ===
    routing_enabled = db.Column(db.Boolean, default=False)
    routing_default_recipients = db.Column(db.Text)  # Fallback se nessuna regola matcha
    routing_aggregation = db.Column(db.String(20), default='per_recipient')
    # 'per_recipient' = una mail per destinatario con tutti i suoi errori
    # 'per_error' = una mail per errore
    routing_no_match_action = db.Column(db.String(20), default='send_default')
    # 'send_default' = invia a default_recipients
    # 'skip' = non inviare
    
    # === TIMESTAMPS ===
    created_at = db.Column(db.DateTime, default=get_utc_now)
    updated_at = db.Column(db.DateTime, default=get_utc_now, onupdate=get_utc_now)
    last_check_at = db.Column(db.DateTime)
    locked_at = db.Column(db.DateTime, nullable=True)
    last_error_at = db.Column(db.DateTime)
    
    # === STATISTICHE ===
    total_errors_found = db.Column(db.Integer, default=0)
    total_emails_sent = db.Column(db.Integer, default=0)
    
    # === RELAZIONI ===
    errors = db.relationship('ErrorRecord', backref='monitored_query', lazy='dynamic', 
                            cascade='all, delete-orphan')
    logs = db.relationship('QueryLog', backref='monitored_query', lazy='dynamic',
                          cascade='all, delete-orphan')
    routing_rules = db.relationship('RoutingRule', backref='monitored_query',
                                   cascade='all, delete-orphan',
                                   order_by='RoutingRule.priority')
    
    # === NOTIFICHE ===
    notification_channels = db.relationship('NotificationChannel', 
                                           secondary='query_notification_channels',
                                           backref='queries')
    
    # === TAGS ===
    tags = db.Column(db.String(500), default='')  # Tags separati da virgola

    def get_recipients_list(self):
        """Restituisce la lista dei destinatari email (separati da virgola)"""
        if not self.email_recipients:
            return []
        return [r.strip() for r in self.email_recipients.split(',') if r.strip()]
    
    def get_default_routing_recipients(self):
        """Restituisce i destinatari di fallback per il routing"""
        if not self.routing_default_recipients:
            return []
        return [r.strip() for r in self.routing_default_recipients.split(',') if r.strip()]
    
    def get_key_fields_list(self):
        """Restituisce la lista dei campi chiave"""
        return [f.strip() for f in self.key_fields.split(',') if f.strip()]
    
    def get_schedule_days_list(self):
        """Restituisce la lista dei giorni attivi (ISO weekday: 1=lun, 7=dom)"""
        if not self.schedule_days:
            return [1, 2, 3, 4, 5, 6, 7]  # Tutti i giorni se non specificato
        return [int(d.strip()) for d in self.schedule_days.split(',') if d.strip()]
    
    def get_source_config(self):
        """Restituisce la configurazione sorgente come dict"""
        if not self.source_config:
            return {}
        return json.loads(self.source_config)
        
    def get_tags_list(self):
        """Restituisce lista dei tag."""
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def set_source_config(self, config):
        """Imposta la configurazione sorgente da dict"""
        self.source_config = json.dumps(config, ensure_ascii=False)
    
    def _get_local_now(self):
        """Restituisce ora locale usando timezone configurata."""
        from utils import get_local_now
        return get_local_now()

    def is_in_schedule(self, now=None):
        """Verifica se la query può essere eseguita in questo momento (ora locale)."""
        if now is None:
            now = self._get_local_now()
        
        current_time = now.time()
        current_weekday = now.isoweekday()
        
        # Verifica giorno
        allowed_days = self.get_schedule_days_list()
        if current_weekday not in allowed_days:
            return False
        
        # Verifica fascia oraria (>= per includere estremi)
        if self.schedule_start_time and current_time < self.schedule_start_time:
            return False
        if self.schedule_end_time and current_time > self.schedule_end_time:
            return False
        
        return True

    def get_next_scheduled_time(self, now=None):
        """Calcola la prossima esecuzione basandosi su reference_time + intervallo (ora locale)."""
        if now is None:
            now = self._get_local_now()
        
        # Se non c'è reference_time, usa mezzanotte
        ref_time = self.schedule_reference_time or time(0, 0)
        interval = timedelta(minutes=self.check_interval_minutes)
        
        # Costruisci il primo slot di oggi
        today_ref = datetime.combine(now.date(), ref_time)
        
        # Trova lo slot corrente o precedente
        if now >= today_ref:
            elapsed = now - today_ref
            intervals_passed = int(elapsed.total_seconds() // interval.total_seconds())
            current_slot = today_ref + (interval * intervals_passed)
            next_slot = current_slot + interval
        else:
            yesterday_ref = today_ref - timedelta(days=1)
            elapsed = now - yesterday_ref
            intervals_passed = int(elapsed.total_seconds() // interval.total_seconds())
            current_slot = yesterday_ref + (interval * intervals_passed)
            next_slot = current_slot + interval
        
        return current_slot, next_slot

    def get_next_run_time(self, now=None):
        """Calcola quando la query verrà eseguita la prossima volta (considera fascia oraria)."""
        if now is None:
            now = self._get_local_now()
        
        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Determina quale slot è il prossimo da eseguire
        if self.last_check_at is not None:
            # Converti last_check_at in ora locale per confronto
            last_check_local = self._utc_to_local(self.last_check_at)
            if last_check_local >= current_slot:
                target_slot = next_slot
            else:
                target_slot = current_slot
        else:
            target_slot = current_slot if current_slot >= now else next_slot
        
        # Verifica se target_slot è in fascia oraria
        for _ in range(1000):  # max 1000 iterazioni per sicurezza
            slot_time = target_slot.time()
            slot_weekday = target_slot.isoweekday()
            
            # Verifica giorno
            allowed_days = self.get_schedule_days_list()
            if slot_weekday not in allowed_days:
                target_slot = self._next_day_start(target_slot)
                continue
            
            # Verifica fascia oraria
            if self.schedule_start_time and slot_time < self.schedule_start_time:
                target_slot = datetime.combine(target_slot.date(), self.schedule_start_time)
                continue
            if self.schedule_end_time and slot_time > self.schedule_end_time:
                target_slot = self._next_day_start(target_slot)
                continue
            
            # Slot valido
            return target_slot
        
        return None  # Non dovrebbe mai arrivare qui

    def _next_day_start(self, dt):
        """Restituisce l'inizio del prossimo giorno con reference_time."""
        ref_time = self.schedule_reference_time or time(0, 0)
        next_day = dt.date() + timedelta(days=1)
        return datetime.combine(next_day, ref_time)

    def _utc_to_local(self, utc_dt):
        """Converte datetime UTC in locale."""
        from utils import utc_to_local
        return utc_to_local(utc_dt)

    def should_run_now(self, now=None):
        """Verifica se la query deve essere eseguita adesso."""
        if now is None:
            now = self._get_local_now()
        
        # Verifica fascia oraria e giorno
        if not self.is_in_schedule(now):
            return False, "Fuori fascia oraria"
        
        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Se non è mai stata eseguita, esegui se siamo nello slot corrente
        if self.last_check_at is None:
            return True, "Prima esecuzione"
        
        # Converti last_check_at in locale
        last_check_local = self._utc_to_local(self.last_check_at)
        
        # Se l'ultima esecuzione è prima dello slot corrente, dobbiamo eseguire
        if last_check_local < current_slot:
            return True, "Slot corrente non ancora eseguito"
        
        return False, f"Prossima esecuzione: {next_slot.strftime('%H:%M')}"

    def __repr__(self):
        return f'<MonitoredQuery {self.name}>'


class DatabaseConnection(db.Model):
    """Connessione a database esterno."""
    __tablename__ = 'database_connections'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    db_type = db.Column(db.String(20), nullable=False)
    host = db.Column(db.String(255))
    port = db.Column(db.Integer)
    database = db.Column(db.String(255))  # database name o service_name per Oracle
    username = db.Column(db.String(100))
    password = db.Column(db.String(255))  # TODO: criptare
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    # Relationship
    queries = db.relationship('MonitoredQuery', backref='db_connection', lazy='dynamic')
    
    def get_driver(self):
        """Restituisce il driver per questo tipo di DB."""
        from db_drivers import get_driver
        return get_driver(self.db_type)
    
    def test_connection(self) -> dict:
        """Testa la connessione."""
        driver = self.get_driver()
        return driver.test_connection(
            self.host, self.port, self.database, self.username, self.password
        )
    
    def execute_query(self, sql: str) -> tuple:
        """Esegue una query su questa connessione."""
        driver = self.get_driver()
        conn = driver.connect(
            self.host, self.port, self.database, self.username, self.password
        )
        try:
            return driver.execute_query(conn, sql)
        finally:
            conn.close()

    def __repr__(self):
            return f'<DatabaseConnection {self.name} ({self.db_type})>'


# Tabella associativa per query-canali notifica (many-to-many)
query_notification_channels = db.Table('query_notification_channels',
    db.Column('query_id', db.Integer, db.ForeignKey('monitored_queries.id'), primary_key=True),
    db.Column('channel_id', db.Integer, db.ForeignKey('notification_channels.id'), primary_key=True)
)


class NotificationChannel(db.Model):
    """
    Canale di notifica configurabile (Webhook, Telegram, Teams).
    """
    __tablename__ = 'notification_channels'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    channel_type = db.Column(db.String(20), nullable=False)  # webhook, telegram, teams
    
    # Configurazione JSON specifica per tipo
    config = db.Column(db.Text, nullable=False, default='{}')
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    # Statistiche
    total_sent = db.Column(db.Integer, default=0)
    last_sent_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    
    def get_config(self):
        import json
        return json.loads(self.config) if self.config else {}
    
    def set_config(self, config_dict):
        import json
        self.config = json.dumps(config_dict, ensure_ascii=False)
    
    def __repr__(self):
        return f'<NotificationChannel {self.name} ({self.channel_type})>'


class RoutingRule(db.Model):
    """
    Regola di routing con condizioni multiple su campi arbitrari.
    Permette di indirizzare errori a destinatari diversi in base ai dati.
    """
    __tablename__ = 'routing_rules'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    # Nome descrittivo (opzionale, per UI)
    name = db.Column(db.String(100))
    
    # Logica tra condizioni multiple
    condition_logic = db.Column(db.String(5), default='AND')  # 'AND' / 'OR'
    
    # Azione: destinatari se match (separati da virgola)
    recipients = db.Column(db.Text, nullable=False)
    
    # Priorità (ordine di valutazione, più basso = prima)
    priority = db.Column(db.Integer, default=0)
    
    # Se True, non valuta regole successive dopo match
    stop_on_match = db.Column(db.Boolean, default=False)
    
    is_active = db.Column(db.Boolean, default=True)
    
    # Relazione con le condizioni
    conditions = db.relationship('RoutingCondition', backref='rule',
                                cascade='all, delete-orphan',
                                order_by='RoutingCondition.id')
    
    def get_recipients_list(self):
        """Restituisce la lista dei destinatari"""
        if not self.recipients:
            return []
        return [r.strip() for r in self.recipients.split(',') if r.strip()]
    
    def __repr__(self):
        return f'<RoutingRule {self.id} "{self.name or "unnamed"}">'


class RoutingCondition(db.Model):
    """
    Singola condizione di una regola di routing.
    Il campo è completamente dinamico (qualsiasi colonna della query).
    """
    __tablename__ = 'routing_conditions'
    
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('routing_rules.id'), nullable=False)
    
    # Condizione generica su qualsiasi campo
    field_name = db.Column(db.String(100), nullable=False)
    operator = db.Column(db.String(20), nullable=False)
    value = db.Column(db.String(500))  # Può essere vuoto per is_empty/is_not_empty
    
    # Case sensitivity per confronti testo
    case_sensitive = db.Column(db.Boolean, default=False)
    
    # Operatori supportati (per documentazione):
    # equals, not_equals, contains, not_contains, startswith, endswith,
    # in, not_in, gt, gte, lt, lte, is_empty, is_not_empty, regex
    
    def __repr__(self):
        return f'<RoutingCondition {self.field_name} {self.operator} {self.value}>'


class ErrorRecord(db.Model):
    """
    Record degli errori rilevati dalle consultazioni.
    Ogni errore viene identificato da un hash univoco basato sui campi chiave.
    """
    __tablename__ = 'error_records'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    # Hash univoco dell'errore (calcolato dai campi chiave)
    error_hash = db.Column(db.String(64), nullable=False, index=True)
    
    # Dati dell'errore in formato JSON
    error_data = db.Column(db.Text, nullable=False)
    
    # Stato notifica iniziale
    email_sent = db.Column(db.Boolean, default=False)
    email_sent_at = db.Column(db.DateTime)
    
    # Reminder
    last_reminder_at = db.Column(db.DateTime)
    reminder_count = db.Column(db.Integer, default=0)
    
    # Timestamps
    first_seen_at = db.Column(db.DateTime, default=get_utc_now)
    last_seen_at = db.Column(db.DateTime, default=get_utc_now)
    resolved_at = db.Column(db.DateTime)
    
    # Contatore: quante volte è stato visto
    occurrence_count = db.Column(db.Integer, default=1)
    
    # Indice composto per ricerche efficienti
    __table_args__ = (
        db.Index('ix_error_query_hash', 'query_id', 'error_hash'),
    )
    
    def get_error_data(self):
        """Deserializza i dati dell'errore"""
        return json.loads(self.error_data) if self.error_data else {}
    
    def set_error_data(self, data):
        """Serializza i dati dell'errore"""
        self.error_data = json.dumps(data, default=str, ensure_ascii=False)
    
    def needs_reminder(self, query):
        """Verifica se l'errore necessita di un reminder"""
        if not query.reminder_enabled:
            return False
        if not self.email_sent:
            return False  # Prima email non ancora inviata
        if self.resolved_at:
            return False  # Già risolto
        if self.reminder_count >= query.reminder_max_count:
            return False  # Raggiunto limite reminder
        
        # Calcola se è passato abbastanza tempo
        last_notification = self.last_reminder_at or self.email_sent_at
        if not last_notification:
            return False
        
        elapsed_minutes = (get_utc_now() - last_notification).total_seconds() / 60
        return elapsed_minutes >= query.reminder_interval_minutes
    
    @staticmethod
    def calculate_hash(data: dict, key_fields: list) -> str:
        """Calcola l'hash univoco dell'errore basato sui campi chiave."""
        key_values = []
        for field in key_fields:
            # Cerca il campo case-insensitive
            value = None
            for k, v in data.items():
                if k.upper() == field.upper():
                    value = v
                    break
            key_values.append(str(value) if value is not None else '')
        
        hash_string = '|'.join(key_values)
        return hashlib.sha256(hash_string.encode()).hexdigest()
    
    def __repr__(self):
        return f'<ErrorRecord {self.error_hash[:8]}... query={self.query_id}>'


class QueryLog(db.Model):
    """Log delle esecuzioni delle query per debugging e monitoraggio."""
    __tablename__ = 'query_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    executed_at = db.Column(db.DateTime, default=get_utc_now)
    
    # Risultato esecuzione
    status = db.Column(db.String(20))  # 'success', 'error', 'skipped'
    
    # Statistiche esecuzione
    rows_returned = db.Column(db.Integer, default=0)
    new_errors = db.Column(db.Integer, default=0)
    resolved_errors = db.Column(db.Integer, default=0)
    reminders_sent = db.Column(db.Integer, default=0)
    emails_sent = db.Column(db.Integer, default=0)
    execution_time_ms = db.Column(db.Integer)
    
    # Eventuale messaggio di errore o note
    error_message = db.Column(db.Text)
    
    def __repr__(self):
        return f'<QueryLog {self.id} status={self.status}>'


class EmailLog(db.Model):
    """Log delle email inviate per tracciabilità."""
    __tablename__ = 'email_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'))
    
    sent_at = db.Column(db.DateTime, default=get_utc_now)
    recipients = db.Column(db.Text)
    subject = db.Column(db.String(200))
    
    # Tipo di email
    email_type = db.Column(db.String(20), default='new_errors')  # new_errors, reminder
    
    # Numero di errori inclusi nell'email
    error_count = db.Column(db.Integer, default=0)
    
    # Stato invio
    status = db.Column(db.String(20))  # 'sent', 'failed'
    error_message = db.Column(db.Text)
    
    def __repr__(self):
        return f'<EmailLog {self.id} status={self.status}>'
