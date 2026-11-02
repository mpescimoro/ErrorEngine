"""
Tests for the API routes.
"""
import json


class TestDashboard:
    """Dashboard tests."""
    
    def test_dashboard_loads(self, client):
        """The dashboard should load correctly."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Dashboard' in response.data or b'dashboard' in response.data


class TestQueriesAPI:
    """Tests for the queries API."""
    
    def test_list_queries_empty(self, client):
        """Empty query list."""
        response = client.get('/api/queries')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_list_queries_with_data(self, client, sample_query):
        """Query list with data."""
        response = client.get('/api/queries')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]['name'] == 'Test Query'
    
    def test_toggle_query(self, client, sample_query):
        """Toggles the active state of a query."""
        # First deactivate
        response = client.post(f'/api/queries/{sample_query.id}/toggle')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['is_active'] is False
        
        # Then reactivate
        response = client.post(f'/api/queries/{sample_query.id}/toggle')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['is_active'] is True
    
    def test_query_status(self, client, sample_query):
        """Gets the query state."""
        response = client.get(f'/api/queries/{sample_query.id}/status')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['name'] == 'Test Query'
        assert 'active_errors' in data
        assert 'is_active' in data
    
    def test_query_not_found(self, client):
        """A query that does not exist returns 404."""
        response = client.get('/api/queries/99999/status')
        assert response.status_code == 404


class TestRoutingRulesAPI:
    """Tests for the routing rules API."""
    
    def test_get_routing_rules_empty(self, client, sample_query):
        """Empty routing rule list."""
        response = client.get(f'/api/queries/{sample_query.id}/routing/rules')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'rules' in data
        assert len(data['rules']) == 0
    
    def test_create_routing_rule(self, client, sample_query):
        """Creates a new routing rule."""
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
        """Creates a rule with an invalid email."""
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
        """Creates a rule without recipients."""
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
        """Deletes a routing rule."""
        # First get the rules
        response = client.get(f'/api/queries/{sample_query_with_routing.id}/routing/rules')
        data = json.loads(response.data)
        rule_id = data['rules'][0]['id']
        
        # Delete the rule
        response = client.delete(
            f'/api/queries/{sample_query_with_routing.id}/routing/rules/{rule_id}'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
    
    def test_get_operators(self, client):
        """Gets the list of available operators."""
        response = client.get('/api/routing/operators')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) > 0
        
        # Check the structure
        for op in data:
            assert 'value' in op
            assert 'label' in op


class TestErrorsAPI:
    """Tests for the errors API."""
    
    def test_list_errors_empty(self, client):
        """Empty error list."""
        response = client.get('/api/errors')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_resolve_error_not_found(self, client):
        """Resolving an error that does not exist."""
        response = client.post('/api/errors/99999/resolve')
        assert response.status_code == 404


class TestStatsAPI:
    """Tests for the statistics API."""
    
    def test_get_stats(self, client):
        """Gets the general statistics."""
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'total_queries' in data
        assert 'active_queries' in data
        assert 'total_active_errors' in data


class TestCleanupAPI:
    """Tests for the cleanup API."""
    
    def test_cleanup_stats(self, client):
        """Gets the cleanup statistics."""
        response = client.get('/api/cleanup/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'retention_config' in data
        assert 'counts' in data
    
    def test_run_cleanup(self, client):
        """Runs a manual cleanup."""
        response = client.post('/api/cleanup/run')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['success'] is True
        assert 'result' in data


class TestHealthAPI:
    """Health check tests."""
    
    def test_health_check(self, client):
        """Health check base."""
        response = client.get('/api/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'healthy'
        assert 'checks' in data
        assert data['checks']['database'] == 'ok'


class TestTestEndpoints:
    """Tests for the test endpoints."""
    
    def test_test_email_missing_recipient(self, client):
        """Test email without a recipient."""
        response = client.post(
            '/api/test/email',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
    
    def test_test_query_missing_sql(self, client, sample_query):
        """Test query on connection without SQL."""
        # Use a dummy connection (does not exist, 404)
        response = client.post(
            '/api/connections/99999/test-query',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 404
