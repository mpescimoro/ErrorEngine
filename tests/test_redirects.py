"""
HTTP sources and webhooks do not follow redirects: followed, they would take the saved token and headers to the
server the redirect names. The request stops, and the error names that address.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from data_sources import HttpDataSource
from models import db, MonitoredQuery, NotificationChannel
from notification_service import notification_service


@pytest.fixture
def servers(monkeypatch):
    """The saved server, which redirects every request to another one; and what that other one received."""
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    received = []

    class Other(BaseHTTPRequestHandler):
        def answer(self):
            received.append({'path': self.path, 'headers': dict(self.headers)})
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'[]')
        do_GET = do_POST = do_PUT = answer

        def log_message(self, *args):
            pass

    other = HTTPServer(('127.0.0.1', 0), Other)

    class Saved(BaseHTTPRequestHandler):
        def redirect(self):
            self.send_response(307)
            self.send_header('Location', f'http://127.0.0.1:{other.server_port}/collect')
            self.send_header('Content-Length', '0')
            self.end_headers()
        do_GET = do_POST = do_PUT = redirect

        def log_message(self, *args):
            pass

    saved = HTTPServer(('127.0.0.1', 0), Saved)
    for server in (other, saved):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{saved.server_port}', f'http://127.0.0.1:{other.server_port}/collect', received
    for server in (other, saved):
        server.shutdown()
        server.server_close()


def test_an_http_source_does_not_follow_a_redirect(app, servers):
    saved, target, received = servers
    with pytest.raises(RuntimeError, match=f'HTTP 307: the server redirects to {target}. Put that address in the URL'):
        HttpDataSource().execute({'url': saved + '/errors', 'auth_type': 'api_key', 'auth_token': 'K-1',
                                  'headers': {'X-Key': 'H-1'}})
    assert received == []


def test_a_webhook_does_not_follow_a_redirect(app, servers):
    saved, target, received = servers
    query = MonitoredQuery(name='Orders', sql_query='SELECT 1', key_fields='ID')
    channel = NotificationChannel(name='Hook', channel_type='webhook')
    channel.set_config({'url': saved + '/hook', 'headers': {'X-Key': 'H-1'}})
    db.session.add_all([query, channel])
    db.session.commit()
    result = notification_service.send_to_channel(channel, query, [{'ID': 1}])
    assert result['success'] is False and result['permanent'] is True
    assert f'the webhook redirects to {target}. Put that address in the channel' in result['message']
    assert received == []
