"""
HTTP sources: the API key from the form, the JSON path to the rows, rows that are not objects.
"""
import json
from unittest.mock import patch

import pytest
import requests

from models import db, ErrorRecord
from tests.helpers import make_query


def http_response(body, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def test_api_key_from_the_form_is_sent(app):
    from data_sources import HttpDataSource
    with patch('data_sources.requests.request', return_value=http_response([{'ID': 1}])) as req:
        HttpDataSource().execute({'url': 'https://api.x.com/items', 'auth_type': 'api_key', 'auth_token': 'K-123'})
    assert req.call_args.kwargs['headers']['X-API-Key'] == 'K-123'


def test_api_key_in_the_query_string_is_a_parameter(app):
    """Encoded by requests, and not part of the URL that the form shows."""
    from data_sources import HttpDataSource
    with patch('data_sources.requests.request', return_value=http_response([{'ID': 1}])) as req:
        HttpDataSource().execute({'url': 'https://api.x.com/items?page=1', 'auth_type': 'api_key',
                                  'auth_token': 'K&1 2', 'api_key_name': 'key', 'api_key_in': 'query'})
    sent = req.call_args.kwargs
    assert (sent['url'], sent['params']) == ('https://api.x.com/items?page=1', {'key': 'K&1 2'})
    assert 'key' not in sent['headers'] and 'X-API-Key' not in sent['headers']
    prepared = requests.Request('GET', sent['url'], params=sent['params']).prepare()
    assert prepared.url == 'https://api.x.com/items?page=1&key=K%261+2'


def test_rows_that_are_not_objects_are_a_source_failure(app):
    from data_sources import HttpDataSource
    for body in (['A-1', 'A-2'], [{'ID': 1}, 2]):
        with patch('data_sources.requests.request', return_value=http_response(body)):
            with pytest.raises(ValueError, match='not objects with fields'):
                HttpDataSource().execute({'url': 'https://api.x.com/items'})


def test_a_single_object_is_a_source_failure_not_one_error(app):
    # {"status": "ok"} would be an error that never resolves
    from data_sources import HttpDataSource
    with patch('data_sources.requests.request', return_value=http_response({'status': 'ok'})):
        with pytest.raises(ValueError, match='an object, not a list of rows: set the JSON path'):
            HttpDataSource().execute({'url': 'https://api.x.com/health'})


def test_the_columns_are_the_fields_of_every_row(app):
    from data_sources import HttpDataSource
    body = [{'ID': 1, 'STATE': 'late'}, {'ID': 2, 'STATE': 'late', 'NOTE': 'only here'}]
    with patch('data_sources.requests.request', return_value=http_response(body)):
        columns, rows = HttpDataSource().execute({'url': 'https://api.x.com/items'})
    assert columns == ['ID', 'STATE', 'NOTE'] and rows == body


class TestJsonPath:

    def test_missing_step_is_a_source_failure(self, app):
        from data_sources import HttpDataSource
        with patch('data_sources.requests.request',
                   return_value=http_response({'status': 'error', 'message': 'rate limit exceeded'})):
            with pytest.raises(ValueError, match="'data' not found"):
                HttpDataSource().execute({'url': 'https://api.x.com', 'response_path': 'data.items'})

    def test_list_index_in_the_path(self, app):
        from data_sources import HttpDataSource
        body = {'data': [{'rows': [{'ID': 1}, {'ID': 2}]}]}
        with patch('data_sources.requests.request', return_value=http_response(body)):
            columns, rows = HttpDataSource().execute({'url': 'https://api.x.com', 'response_path': 'data.0.rows'})
        assert [r['ID'] for r in rows] == [1, 2]

    def test_error_body_does_not_resolve_the_real_errors(self, app):
        query = make_query(source_type='http', email_recipients='ops@x.it')
        query.set_source_config({'url': 'https://api.x.com', 'response_path': 'data.items'})
        db.session.commit()
        from monitor_service import monitor_service
        good = http_response({'data': {'items': [{'ID': 1}, {'ID': 2}]}})
        bad = http_response({'status': 'error', 'message': 'rate limit exceeded'})
        with patch('monitor_service.email_service.send_error_notification', return_value={'success': True}) as send:
            with patch('data_sources.requests.request', return_value=good):
                monitor_service.check_query(query, force=True)
            with patch('data_sources.requests.request', return_value=bad):
                result = monitor_service.check_query(query, force=True)
            with patch('data_sources.requests.request', return_value=good):
                monitor_service.check_query(query, force=True)
        assert result['status'] == 'error'
        assert ErrorRecord.query.filter_by(query_id=query.id).count() == 2
        assert ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count() == 0
        assert send.call_count == 1


def test_http_query_with_a_leftover_connection_reads_http(app):
    """Queries switched to HTTP before 2.4 kept their database connection: HTTP wins."""
    from data_sources import execute_query_source
    from models import DatabaseConnection
    connection = DatabaseConnection(name='ERP', db_type='sqlite', database=':memory:')
    db.session.add(connection)
    db.session.flush()
    query = make_query(source_type='http', db_connection_id=connection.id)
    query.set_source_config({'url': 'https://api.x.it/errors'})
    db.session.commit()
    with patch('data_sources.requests.request', return_value=http_response([{'ID': 1}])) as get, \
            patch('models.DatabaseConnection.execute_query') as sql:
        assert execute_query_source(query) == (['ID'], [{'ID': 1}])
    assert get.called and not sql.called
