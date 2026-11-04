"""
HTTP sources: the API key from the form, the JSON path to the rows.
"""
import json
from unittest.mock import patch

import pytest
import requests

from models import db, MonitoredQuery, ErrorRecord


def make_query(name='Orders', **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    query = MonitoredQuery(name=name, **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


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
