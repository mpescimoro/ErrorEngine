"""
Data Sources - Strategy pattern for multiple data sources.
Supports databases (through db_drivers) and HTTP/REST.
"""
import logging
import requests
from abc import ABC, abstractmethod
from datetime import datetime
from flask import current_app
from utils import redact_secrets

logger = logging.getLogger(__name__)


def _default_http_timeout() -> int:
    """Timeout of HTTP calls: HTTP_TIMEOUT_SECONDS from the configuration (default 30 s)."""
    try:
        return current_app.config.get('HTTP_TIMEOUT_SECONDS', 30)
    except RuntimeError:  # outside the Flask context
        return 30


class DataSource(ABC):
    """Base interface for all data sources."""
    
    @abstractmethod
    def execute(self, config: dict) -> tuple:
        """
        Runs the query/request and returns the data.
        
        Args:
            config: Source-specific configuration
            
        Returns:
            tuple: (columns: list[str], rows: list[dict])
        """
        pass
    
    @abstractmethod
    def test(self, config: dict) -> dict:
        """
        Tests the connection/configuration.
        
        Returns:
            dict: {'success': bool, 'message': str, 'details': dict}
        """
        pass
    
    @abstractmethod
    def get_fields(self, config: dict) -> list:
        """
        Returns the available fields (to help configure routing).
        
        Returns:
            list: [{'name': str, 'type': str, 'sample': str}, ...]
        """
        pass


class HttpDataSource(DataSource):
    """
    Generic HTTP/REST data source.
    Supports GET/POST with authentication and JSON response parsing.
    """
    
    def execute(self, config: dict) -> tuple:
        url = config.get('url')
        if not url:
            raise ValueError("URL not specified")
        
        method = config.get('method', 'GET').upper()
        headers = config.get('headers', {})
        body = config.get('body')
        timeout = config.get('timeout') or _default_http_timeout()
        
        # Authentication
        auth = None
        auth_type = config.get('auth_type')
        if auth_type == 'basic':
            auth = (config.get('auth_username', ''), config.get('auth_password', ''))
        elif auth_type == 'bearer':
            headers['Authorization'] = f"Bearer {config.get('auth_token', '')}"
        elif auth_type == 'api_key':
            key_name = config.get('api_key_name') or 'X-API-Key'
            # The query form saves the key in auth_token
            key_value = config.get('api_key_value') or config.get('auth_token', '')
            if config.get('api_key_in') == 'query':
                # Add as a query parameter
                separator = '&' if '?' in url else '?'
                url = f"{url}{separator}{key_name}={key_value}"
            else:
                # Default: header
                headers[key_name] = key_value
        
        # Run the request
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
            
            # Extract the data at the given path (e.g. "data.items", "results" or "data.0.rows").
            # A missing step is a source failure, not data: an error body answered with
            # HTTP 200 would otherwise become a "new error" and resolve the real ones.
            response_path = config.get('response_path', '')
            if response_path:
                for key in response_path.split('.'):
                    if not key:
                        continue
                    if isinstance(data, dict) and key in data:
                        data = data[key]
                    elif isinstance(data, list) and key.isdigit() and int(key) < len(data):
                        data = data[int(key)]
                    else:
                        raise ValueError(f"JSON response path '{response_path}': '{key}' not found in the response")
                if not isinstance(data, list):
                    raise ValueError(f"JSON response path '{response_path}' does not lead to a list "
                                     f"(found {type(data).__name__})")

            # Normalize to a list of dicts
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                raise ValueError(f"Invalid response: expected a list, got {type(data).__name__}")
            
            if not data:
                return [], []
            
            # Columns from the first item
            columns = list(data[0].keys()) if data else []
            
            return columns, data
            
        except requests.RequestException as e:
            raise RuntimeError(f"HTTP error: {redact_secrets(str(e))}")
    
    def test(self, config: dict) -> dict:
        try:
            columns, rows = self.execute(config)
            return {
                'success': True,
                'message': f'Connection successful, {len(rows)} rows returned',
                'details': {
                    'columns': columns,
                    'row_count': len(rows),
                    'sample_rows': rows[:5]
                }
            }
        except Exception as e:
            return {
                'success': False,
                'message': redact_secrets(str(e))
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
            logger.error(f"HTTP get_fields error: {e}")
            return []


# === FACTORY ===

class DataSourceFactory:
    """Factory that creates the right DataSource instance."""
    
    _sources = {
        'http': HttpDataSource,
    }
    
    @classmethod
    def get_source(cls, source_type: str) -> DataSource:
        """
        Returns the appropriate DataSource instance.
        
        Args:
            source_type: 'http'
            
        Returns:
            DataSource instance
        """
        source_class = cls._sources.get(source_type.lower())
        if not source_class:
            raise ValueError(f"Unsupported source type: {source_type}")
        return source_class()


# === HELPER FUNCTIONS ===

def execute_query_source(query) -> tuple:
    """
    Runs the query on the configured source.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        tuple: (columns, rows)
    """
    # With a database connection, use it
    if query.db_connection_id:
        from models import DatabaseConnection
        conn = DatabaseConnection.query.get(query.db_connection_id)
        if not conn:
            raise ValueError(f"Database connection {query.db_connection_id} not found")
        return conn.execute_query(query.sql_query)
    
    # Otherwise HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.execute(config)
    
    raise ValueError(f"Invalid source configuration for query {query.id}")


def test_query_source(query) -> dict:
    """
    Tests the data source of a query.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        dict: test result
    """
    # With a database connection, test through it
    if query.db_connection_id:
        from models import DatabaseConnection
        conn = DatabaseConnection.query.get(query.db_connection_id)
        if not conn:
            return {'success': False, 'message': 'Database connection not found'}
        
        try:
            columns, rows = conn.execute_query(query.sql_query)
            return {
                'success': True,
                'message': f'Valid query, {len(rows)} rows returned',
                'details': {
                    'columns': columns,
                    'row_count': len(rows),
                    'sample_rows': rows[:5]
                }
            }
        except Exception as e:
            return {'success': False, 'message': redact_secrets(str(e))}
    
    # Otherwise HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.test(config)
    
    return {'success': False, 'message': 'No source configured'}


def get_query_fields(query) -> list:
    """
    Returns the fields available for a query.
    
    Args:
        query: MonitoredQuery instance
        
    Returns:
        list: available fields
    """
    # With a database connection, get the fields from it
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
            logger.error(f"Database get_fields error: {e}")
            return []
    
    # Otherwise HTTP
    if query.source_type == 'http':
        source = DataSourceFactory.get_source('http')
        config = query.get_source_config()
        return source.get_fields(config)
    
    return []
