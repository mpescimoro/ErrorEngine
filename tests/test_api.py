"""
Tests for the API routes.
"""
import json
from datetime import timedelta

from models import db, ErrorRecord, MonitoredQuery
from utils import get_utc_now
from version import __version__


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

    def test_resolving_again_keeps_when_it_was_resolved(self, app, client, sample_query):
        resolved_at = get_utc_now() - timedelta(days=2)
        error = ErrorRecord(query_id=sample_query.id, error_hash='h1', error_data='{"ID": 1}',
                            resolved_at=resolved_at)
        db.session.add(error)
        db.session.commit()
        response = client.post(f'/api/errors/{error.id}/resolve')
        assert response.status_code == 200 and response.get_json()['resolved_at'] == resolved_at.isoformat()
        db.session.expire_all()
        assert db.session.get(ErrorRecord, error.id).resolved_at == resolved_at


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
        assert data['version'] == __version__


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
    
    def test_test_reads_the_source_of_a_query_and_records_nothing(self, client, sample_query, sample_connection):
        query = db.session.get(MonitoredQuery, sample_query.id)
        query.db_connection_id = sample_connection.id
        query.sql_query = "SELECT 1 AS ID, 'late' AS STATUS"
        db.session.commit()
        data = client.post(f'/api/queries/{query.id}/test').get_json()
        assert data['success'] is True
        assert data['details'] == {'columns': ['ID', 'STATUS'], 'row_count': 1,
                                   'sample_rows': [{'ID': '1', 'STATUS': 'late'}]}
        assert ErrorRecord.query.count() == 0

    def test_routing_test_with_the_errors_sent_or_the_active_ones(self, client, sample_query_with_routing):
        url = f'/api/queries/{sample_query_with_routing.id}/routing/test'
        sent = [{'SEVERITY': 'CRITICAL'}, {'SEVERITY': 'WARNING'}, {'SEVERITY': 'INFO'}]
        summary = client.post(url, json={'errors': sent}).get_json()['summary']
        assert summary['recipients'] == {'critical@example.com': 1, 'warning@example.com': 1,
                                         'default@example.com': 1}
        assert summary['total_errors'] == 3 and summary['unmatched'] == 0   # no match: the default recipients
        # Without a body: the active errors of the query
        assert client.post(url).get_json()['message'] == 'No errors to test'
        db.session.add(ErrorRecord(query_id=sample_query_with_routing.id, error_hash='h1',
                                   error_data='{"SEVERITY": "critical"}'))
        db.session.commit()
        assert client.post(url).get_json()['summary']['recipients'] == {'critical@example.com': 1}
        # Anything else is refused with the shape expected, not a server error
        for body in ({'errors': 'CRITICAL'}, {'errors': ['CRITICAL']}, sent):
            response = client.post(url, json=body)
            assert response.status_code == 400 and '{"errors": [' in response.get_json()['message']
        assert client.post(url, data='errors=CRITICAL').status_code == 400

    def test_test_query_missing_sql(self, client, sample_connection):
        """Test query on a connection without SQL."""
        response = client.post(
            f'/api/connections/{sample_connection.id}/test-query',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        assert json.loads(response.data)['error'] == 'SQL query required'
