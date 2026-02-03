"""
Data Sources - Strategy Pattern per sorgenti dati multiple.
Supporta Database (via db_drivers) e HTTP/REST.
"""
import logging
import re
import requests
from abc import ABC, abstractmethod
from datetime import datetime
from flask import current_app

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """Interfaccia base per tutte le sorgenti dati."""
    
    @abstractmethod
    def execute(self, config: dict) -> tuple:
        """
        Esegue la query/richiesta e restituisce i dati.
        
        Args:
            config: Configurazione specifica per la sorgente
            
        Returns:
            tuple: (columns: list[str], rows: list[dict])
        """
        pass
    
    @abstractmethod
    def test(self, config: dict) -> dict:
        """
        Testa la connessione/configurazione.
        
        Returns:
            dict: {'success': bool, 'message': str, 'details': dict}
        """
        pass
    
    @abstractmethod
    def get_fields(self, config: dict) -> list:
        """
        Restituisce i campi disponibili (per aiutare la configurazione routing).
        
        Returns:
            list: [{'name': str, 'type': str, 'sample': str}, ...]
        """
        pass


class HttpDataSource(DataSource):
    """
    Sorgente dati HTTP/REST generica.
    Supporta GET/POST con autenticazione e parsing risposta JSON.
    """
    
    def execute(self, config: dict) -> tuple:
        url = config.get('url')
        if not url:
            raise ValueError("URL non specificato")
        
        method = config.get('method', 'GET').upper()
        headers = config.get('headers', {})
        body = config.get('body')
        timeout = config.get('timeout', 30)
        
        # Autenticazione
        auth = None
        auth_type = config.get('auth_type')
        if auth_type == 'basic':
            auth = (config.get('auth_username', ''), config.get('auth_password', ''))
        elif auth_type == 'bearer':
            headers['Authorization'] = f"Bearer {config.get('auth_token', '')}"
        elif auth_type == 'api_key':
            key_name = config.get('api_key_name', 'X-API-Key')
            key_value = config.get('api_key_value', '')
            if config.get('api_key_in') == 'query':
                # Aggiungi come query parameter
                separator = '&' if '?' in url else '?'
                url = f"{url}{separator}{key_name}={key_value}"
            else:
                # Default: header
                headers[key_name] = key_value
        
        # Esegui richiesta
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=body if method in ('POST', 'PUT', 'PATCH') else None,
                params=body if method == 'GET' and body else None,
                auth=auth,
                timeout=timeout
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Estrai dati dal path specificato (es. "data.items" o "results")
            response_path = config.get('response_path', '')
            if response_path:
                for key in response_path.split('.'):
                    if key:
                        data = data.get(key, data)
            
            # Normalizza a lista di dict
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                raise ValueError(f"Risposta non valida: attesa lista, ricevuto {type(data)}")
            
            if not data:
                return [], []
            
            # Estrai colonne dal primo elemento
            columns = list(data[0].keys()) if data else []
            
            return columns, data
            
        except requests.RequestException as e:
            raise RuntimeError(f"Errore HTTP: {e}")
    
    def test(self, config: dict) -> dict:
        try:
            columns, rows = self.execute(config)
            return {
                'success': True,
                'message': f'Connessione riuscita, restituite {len(rows)} righe',
                'details': {
                    'columns': columns,
                    'row_count': len(rows),
                    'sample_rows': rows[:5]
                }
            }
        except Exception as e:
            return {
                'success': False,
                'message': str(e)
            }
    
    def get_fields(self, config: dict) -> list:
        try:
            columns, rows = self.execute(config)
            
            if not rows:
                return [{'name': col, 'type': 'text', 'sample': None} for col in columns]
            
            sample_row = rows[0]
            fields = []
            
            for col in columns:
                value = sample_row.get(col)
                field_type = 'text'
                if isinstance(value, (int, float)):
                    field_type = 'number'
                elif isinstance(value, datetime):
                    field_type = 'date'
                
                fields.append({
                    'name': col,
                    'type': field_type,
                    'sample': str(value)[:100] if value is not None else None
                })
            
            return fields
        except Exception as e:
            logger.error(f"Errore get_fields HTTP: {e}")
            return []


# === FACTORY ===

class DataSourceFactory:
    """Factory per creare l'istanza corretta di DataSource."""
    
    _sources = {
        'http': HttpDataSource,
    }
    
    @classmethod
    def get_source(cls, source_type: str) -> DataSource:
        """
        Restituisce l'istanza di DataSource appropriata.
        
        Args:
            source_type: 'http'
            
        Returns:
            DataSource instance
        """
        source_class = cls._sources.get(source_type.lower())
        if not source_class:
            raise ValueError(f"Tipo sorgente non supportato: {source_type}")
        return source_class()
    
    @classmethod
    def register_source(cls, name: str, source_class: type):
        """Registra una nuova sorgente dati."""
        cls._sources[name.lower()] = source_class


# === HELPER FUNCTIONS ===

def execute_query_source(query) -> tuple:
    """
    Esegue la query sulla sorgente configurata.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        tuple: (columns, rows)
    """
    # Se ha una connessione database associata, usala
    if query.db_connection_id:
        from models import DatabaseConnection
        conn = DatabaseConnection.query.get(query.db_connection_id)
        if not conn:
            raise ValueError(f"Connessione database {query.db_connection_id} non trovata")
        return conn.execute_query(query.sql_query)
    
    # Altrimenti usa HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.execute(config)
    
    raise ValueError(f"Configurazione sorgente non valida per query {query.id}")


def test_query_source(query) -> dict:
    """
    Testa la sorgente dati di una query.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        dict: risultato test
    """
    # Se ha una connessione database, testa via quella
    if query.db_connection_id:
        from models import DatabaseConnection
        conn = DatabaseConnection.query.get(query.db_connection_id)
        if not conn:
            return {'success': False, 'message': 'Connessione database non trovata'}
        
        try:
            columns, rows = conn.execute_query(query.sql_query)
            return {
                'success': True,
                'message': f'Query valida, restituite {len(rows)} righe',
                'details': {
                    'columns': columns,
                    'row_count': len(rows),
                    'sample_rows': rows[:5]
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    # Altrimenti HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.test(config)
    
    return {'success': False, 'message': 'Nessuna sorgente configurata'}


def get_query_fields(query) -> list:
    """
    Restituisce i campi disponibili per una query.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        list: campi disponibili
    """
    # Se ha una connessione database, ottieni campi da quella
    if query.db_connection_id:
        from models import DatabaseConnection
        conn = DatabaseConnection.query.get(query.db_connection_id)
        if not conn:
            return []
        
        try:
            columns, rows = conn.execute_query(query.sql_query)
            
            if not rows:
                return [{'name': col, 'type': 'text', 'sample': None} for col in columns]
            
            sample_row = rows[0]
            fields = []
            
            for col in columns:
                value = sample_row.get(col)
                field_type = 'text'
                if isinstance(value, (int, float)):
                    field_type = 'number'
                elif isinstance(value, datetime):
                    field_type = 'date'
                
                fields.append({
                    'name': col,
                    'type': field_type,
                    'sample': str(value)[:100] if value is not None else None
                })
            
            return fields
        except Exception as e:
            logger.error(f"Errore get_fields database: {e}")
            return []
    
    # Altrimenti HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.get_fields(config)
    
    return []
