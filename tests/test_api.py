"""
Test per le API routes.
"""
import pytest
import json


class TestDashboard:
    """Test per la dashboard."""
    
    def test_dashboard_loads(self, client):
        """La dashboard dovrebbe caricarsi correttamente."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Dashboard' in response.data or b'dashboard' in response.data


class TestQueriesAPI:
    """Test per le API delle queries."""
    
    def test_list_queries_empty(self, client):
        """Lista queries vuota."""
        response = client.get('/api/queries')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_list_queries_with_data(self, client, sample_query):
        """Lista queries con dati."""
        response = client.get('/api/queries')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]['name'] == 'Test Query'
    
    def test_toggle_query(self, client, sample_query):
        """Toggle stato attivo query."""
        # Prima disattiva
        response = client.post(f'/api/queries/{sample_query.id}/toggle')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['is_active'] is False
        
        # Poi riattiva
        response = client.post(f'/api/queries/{sample_query.id}/toggle')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['is_active'] is True
    
    def test_query_status(self, client, sample_query):
        """Ottiene stato query."""
        response = client.get(f'/api/queries/{sample_query.id}/status')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['name'] == 'Test Query'
        assert 'active_errors' in data
        assert 'is_active' in data
    
    def test_query_not_found(self, client):
        """Query non esistente ritorna 404."""
        response = client.get('/api/queries/99999/status')
        assert response.status_code == 404


class TestRoutingRulesAPI:
    """Test per le API delle regole di routing."""
    
    def test_get_routing_rules_empty(self, client, sample_query):
        """Lista regole routing vuota."""
        response = client.get(f'/api/queries/{sample_query.id}/routing/rules')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'rules' in data
        assert len(data['rules']) == 0
    
    def test_create_routing_rule(self, client, sample_query):
        """Crea una nuova regola di routing."""
        rule_data = {
            'name': 'Test Rule',
            'recipients': 'test@example.com',
            'condition_logic': 'AND',
            'priority': 0,
            'conditions': [
                {
                    'field_name': 'STATUS',
                    'operator': 'equals',
                    'value': 'ERROR'
                }
            ]
        }
        
        response = client.post(
            f'/api/queries/{sample_query.id}/routing/rules',
            data=json.dumps(rule_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert 'rule_id' in data
    
    def test_create_routing_rule_invalid_email(self, client, sample_query):
        """Crea regola con email non valida."""
        rule_data = {
            'name': 'Test Rule',
            'recipients': 'not-an-email',
            'condition_logic': 'AND'
        }
        
        response = client.post(
            f'/api/queries/{sample_query.id}/routing/rules',
            data=json.dumps(rule_data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
    
    def test_create_routing_rule_missing_recipients(self, client, sample_query):
        """Crea regola senza destinatari."""
        rule_data = {
            'name': 'Test Rule',
            'recipients': '',
            'condition_logic': 'AND'
        }
        
        response = client.post(
            f'/api/queries/{sample_query.id}/routing/rules',
            data=json.dumps(rule_data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
    
    def test_delete_routing_rule(self, client, sample_query_with_routing):
        """Elimina una regola di routing."""
        # Prima ottieni le regole
        response = client.get(f'/api/queries/{sample_query_with_routing.id}/routing/rules')
        data = json.loads(response.data)
        rule_id = data['rules'][0]['id']
        
        # Elimina la regola
        response = client.delete(
            f'/api/queries/{sample_query_with_routing.id}/routing/rules/{rule_id}'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
    
    def test_get_operators(self, client):
        """Ottiene lista operatori disponibili."""
        response = client.get('/api/routing/operators')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) > 0
        
        # Verifica struttura
        for op in data:
            assert 'value' in op
            assert 'label' in op


class TestErrorsAPI:
    """Test per le API degli errori."""
    
    def test_list_errors_empty(self, client):
        """Lista errori vuota."""
        response = client.get('/api/errors')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_resolve_error_not_found(self, client):
        """Risolvi errore non esistente."""
        response = client.post('/api/errors/99999/resolve')
        assert response.status_code == 404


class TestStatsAPI:
    """Test per le API delle statistiche."""
    
    def test_get_stats(self, client):
        """Ottiene statistiche generali."""
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'total_queries' in data
        assert 'active_queries' in data
        assert 'total_active_errors' in data


class TestCleanupAPI:
    """Test per le API di cleanup."""
    
    def test_cleanup_stats(self, client):
        """Ottiene statistiche cleanup."""
        response = client.get('/api/cleanup/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'retention_config' in data
        assert 'counts' in data
    
    def test_run_cleanup(self, client):
        """Esegue cleanup manuale."""
        response = client.post('/api/cleanup/run')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['success'] is True
        assert 'result' in data


class TestHealthAPI:
    """Test per health check."""
    
    def test_health_check(self, client):
        """Health check base."""
        response = client.get('/api/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'healthy'
        assert 'checks' in data
        assert data['checks']['database'] == 'ok'


class TestTestEndpoints:
    """Test per gli endpoint di test."""
    
    def test_test_email_missing_recipient(self, client):
        """Test email senza destinatario."""
        response = client.post(
            '/api/test/email',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
    
    def test_test_sql_missing_query(self, client):
        """Test SQL senza query."""
        response = client.post(
            '/api/test/sql',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
