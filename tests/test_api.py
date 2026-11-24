"""
API: queries, routing rules, errors, statistics, cleanup, health, the test endpoints.
"""
from datetime import timedelta

import pytest

from models import db, ErrorRecord, MonitoredQuery, DatabaseConnection
from utils import get_utc_now
from version import __version__


class TestQueries:

    def test_list_and_status(self, client, sample_query):
        assert [q['name'] for q in client.get('/api/queries').get_json()] == ['Test Query']
        status = client.get(f'/api/queries/{sample_query.id}/status').get_json()
        assert status['name'] == 'Test Query' and {'active_errors', 'is_active'} <= set(status)

    def test_an_empty_list(self, client):
        assert client.get('/api/queries').get_json() == [] and client.get('/api/errors').get_json() == []

    def test_toggle(self, client, sample_query):
        url = f'/api/queries/{sample_query.id}/toggle'
        assert client.post(url).get_json()['is_active'] is False
        assert client.post(url).get_json()['is_active'] is True

    def test_what_does_not_exist_is_404(self, client):
        assert client.get('/api/queries/99999/status').status_code == 404
        assert client.post('/api/errors/99999/resolve').status_code == 404

    def test_a_failure_is_json_under_api_and_a_page_elsewhere(self, app, client, monkeypatch):
        for method, url, code, name in (('get', '/api/queries/99999/status', 404, 'not_found'),
                                        ('get', '/api/nothing-here', 404, 'not_found'),
                                        ('delete', '/api/queries', 405, 'method_not_allowed'),
                                        ('get', '/api/stats/timeline?days=0', 400, 'bad_request'),
                                        ('get', '/api/stats/overview?days=366', 400, 'bad_request')):
            response = getattr(client, method)(url)
            assert (response.status_code, response.get_json()['error']) == (code, name), url
        assert client.get('/api/stats/timeline?days=0').get_json()['message'] == 'days: from 1 to 365'
        # The headers of the error stay
        assert set(client.delete('/api/queries').headers['Allow'].split(', ')) >= {'GET'}
        assert client.get('/api/stats/timeline?days=365').status_code == 200
        page = client.get('/queries/99999')
        assert page.status_code == 404 and page.get_json(silent=True) is None
        # An error of ErrorEngine: the details only in the log
        monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
        monkeypatch.setattr('routes.api.errors_per_local_day', lambda *args: 1 / 0)
        response = client.get('/api/stats/timeline')
        assert response.status_code == 500 and response.get_json()['error'] == 'internal_server_error'
        assert 'ZeroDivisionError' not in response.get_data(as_text=True)


class TestRoutingRules:
    RULE = {'name': 'Test Rule', 'recipients': 'test@example.com', 'condition_logic': 'AND', 'priority': 0,
            'conditions': [{'field_name': 'STATUS', 'operator': 'equals', 'value': 'ERROR'}]}

    def test_create_and_delete(self, client, sample_query):
        url = f'/api/queries/{sample_query.id}/routing/rules'
        assert client.get(url).get_json()['rules'] == []
        created = client.post(url, json=self.RULE).get_json()
        assert created['success'] is True
        assert client.delete(f"{url}/{created['rule_id']}").get_json()['success'] is True
        assert client.get(url).get_json()['rules'] == []

    @pytest.mark.parametrize('changes, says', [
        ({'recipients': 'not-an-email'}, ''),
        ({'recipients': ''}, ''),
        ({'conditions': [{'field_name': 'STATUS', 'operator': ['equals'], 'value': 'X'}]},
         'Condition 1: unknown operator'),
        ({'conditions': [{'field_name': ['STATUS'], 'operator': 'equals', 'value': 'X'}]},
         'Condition 1: field is required'),
        ({'conditions': [{'field_name': 'STATUS', 'operator': 'equals', 'value': {'X': 1}}]},
         'Condition 1: the value is text or a number'),
        # As text, "false" would be read as true
        ({'stop_on_match': 'false'}, 'stop_on_match: true or false'),
        ({'recipients': 'andrea.bianchì@example.com'}, 'Invalid email format'),
        ({'is_active': 'false'}, 'is_active: true or false'),
        ({'conditions': [{'field_name': 'STATUS', 'operator': 'equals', 'value': 'X', 'case_sensitive': 'false'}]},
         'Condition 1: case_sensitive is true or false'),
    ])
    def test_refused(self, client, sample_query, changes, says):
        response = client.post(f'/api/queries/{sample_query.id}/routing/rules', json={**self.RULE, **changes})
        assert response.status_code == 400 and response.get_json()['success'] is False
        assert says in response.get_json()['message']

    def test_numbers_are_kept_as_the_page_sends_them(self, client, sample_query):
        url = f'/api/queries/{sample_query.id}/routing/rules'
        for operator, value in (('regex', 5), ('gt', 0)):
            response = client.post(url, json={'recipients': 'ops@example.com', 'conditions': [
                {'field_name': 'QTY', 'operator': operator, 'value': value}]})
            assert response.status_code == 200, response.get_json()
        assert [c['value'] for rule in client.get(url).get_json()['rules'] for c in rule['conditions']] == ['5', '0']


def test_resolving_again_keeps_when_it_was_resolved(client, sample_query):
    resolved_at = get_utc_now() - timedelta(days=2)
    error = ErrorRecord(query_id=sample_query.id, error_hash='h1', error_data='{"ID": 1}', resolved_at=resolved_at)
    db.session.add(error)
    db.session.commit()
    response = client.post(f'/api/errors/{error.id}/resolve')
    assert response.status_code == 200 and response.get_json()['resolved_at'] == resolved_at.isoformat()
    db.session.expire_all()
    assert db.session.get(ErrorRecord, error.id).resolved_at == resolved_at


def test_statistics_cleanup_and_health(client):
    assert {'total_queries', 'active_queries', 'total_active_errors'} <= set(client.get('/api/stats').get_json())
    assert {'retention_config', 'counts'} <= set(client.get('/api/cleanup/stats').get_json())
    cleanup = client.post('/api/cleanup/run').get_json()
    assert cleanup['success'] is True and 'result' in cleanup
    health = client.get('/api/health').get_json()
    assert (health['status'], health['checks']['database'], health['version']) == ('healthy', 'ok', __version__)


class TestConnectionTest:

    def test_the_edit_form_with_the_password_empty_tests_the_fields_typed(self, client, monkeypatch):
        from db_drivers.postgres import PostgresDriver
        seen = []

        def test_connection(self, host, port, database, username, password):
            seen.append((host, port, database, username, password))
            return {'status': 'ok', 'message': 'Connection successful'}
        monkeypatch.setattr(PostgresDriver, 'test_connection', test_connection)
        conn = DatabaseConnection(name='PG', db_type='postgres', host='db.example.com', port=5432,
                                  database='erp', username='reader')
        conn.password = 'saved-secret'
        db.session.add(conn)
        db.session.commit()
        typed = {'db_type': 'postgres', 'host': 'DB.example.com', 'port': '5432', 'database': 'erp_test',
                 'username': 'auditor', 'password': ''}
        assert client.post(f'/api/connections/{conn.id}/test', json=typed).get_json()['status'] == 'ok'
        assert seen == [('DB.example.com', 5432, 'erp_test', 'auditor', 'saved-secret')]
        # The saved password goes only to the server it was saved for
        for moved in ({'host': 'elsewhere.example.com'}, {'port': '5433'}):
            result = client.post(f'/api/connections/{conn.id}/test', json={**typed, **moved}).get_json()
            assert result == {'status': 'error', 'message': 'Password: the server changed, type it again'}
        assert len(seen) == 1
        # Without the fields, as a script asks: the connection as saved
        assert client.post(f'/api/connections/{conn.id}/test').get_json()['status'] == 'ok'
        assert seen[-1] == ('db.example.com', 5432, 'erp', 'reader', 'saved-secret')


class TestTestEndpoints:

    def test_the_test_email_needs_a_recipient(self, client):
        for body in ({}, {'recipient': 'not-an-address'}, {'recipient': 'a@example.com, b@example.com'}):
            response = client.post('/api/test/email', json=body)
            assert response.status_code == 400 and response.get_json()['success'] is False
        response = client.post('/api/test/email', data='not json', content_type='text/plain')
        assert response.status_code == 400 and response.get_json()['success'] is False

    def test_the_sql_test_needs_the_sql(self, client, sample_connection):
        response = client.post(f'/api/connections/{sample_connection.id}/test-query', json={})
        assert response.status_code == 400 and response.get_json()['error'] == 'SQL query required'

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
