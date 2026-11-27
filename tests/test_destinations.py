"""
HTTP sources, webhooks and Teams do not call link-local addresses, where cloud servers have their metadata
service (169.254.169.254), which gives the credentials of the machine. Loopback and private networks, where the
APIs ErrorEngine reads usually are, stay allowed.
"""
import socket
from unittest.mock import patch

import pytest

from models import db, MonitoredQuery
from utils import forbidden_destination

REFUSED = 'link-local or a cloud metadata service'


def resolving(*addresses):
    """getaddrinfo answering these addresses, whatever the name."""
    def answer(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ':' in a else socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port))
                for a in addresses]
    return patch('utils.socket.getaddrinfo', side_effect=answer)


class TestForbiddenDestination:

    @pytest.mark.parametrize('url', [
        'http://169.254.169.254/latest/meta-data/',
        'http://169.254.170.2/v2/credentials',           # AWS ECS
        'http://[fe80::1]/x',
        'http://[fe80::1%25eth0]/x',
        'http://[::ffff:169.254.169.254]/',
        'http://100.100.100.200/latest/meta-data/',       # Alibaba Cloud
        'http://[fd00:ec2::254]/latest/meta-data/',       # AWS, over IPv6
        'http://2852039166/',                             # 169.254.169.254 written as a number
        'HTTP://169.254.169.254:80/',
    ])
    def test_refused(self, url):
        assert REFUSED in (forbidden_destination(url) or '')

    @pytest.mark.parametrize('url', ['http://127.0.0.1:8080/api', 'http://localhost/x', 'http://10.1.2.3/api',
                                     'http://192.168.1.10/api', 'https://[::1]/x', 'http://172.16.0.1/'])
    def test_loopback_and_private_networks_allowed(self, url):
        assert forbidden_destination(url) is None

    def test_a_name_that_leads_there(self):
        with resolving('169.254.169.254'):
            assert REFUSED in forbidden_destination('http://metadata.google.internal/computeMetadata/v1/')
        with resolving('10.0.0.5', '169.254.169.254'):   # one of its addresses is enough
            assert REFUSED in forbidden_destination('https://api.example.com/x')
        with resolving('93.184.216.34'):
            assert forbidden_destination('https://api.example.com/x') is None

    def test_what_cannot_be_read_or_resolved_is_left_to_requests(self):
        with patch('utils.socket.getaddrinfo', side_effect=socket.gaierror('Name or service not known')):
            assert forbidden_destination('https://nowhere.invalid/x') is None
        assert forbidden_destination('not an address') is None
        assert forbidden_destination('') is None


class TestCallers:

    def test_an_http_source_is_not_called(self, app):
        from data_sources import HttpDataSource
        with patch('data_sources.requests.request') as request:
            result = HttpDataSource().test({'url': 'http://169.254.169.254/latest/meta-data/'})
        assert result['success'] is False and REFUSED in result['message']
        request.assert_not_called()

    def test_the_check_fails_as_a_source_that_cannot_be_read(self, app):
        from monitor_service import monitor_service
        query = MonitoredQuery(name='Metadata', source_type='http', key_fields='id')
        query.set_source_config({'url': 'http://169.254.169.254/latest/meta-data/iam/', 'method': 'GET'})
        db.session.add(query)
        db.session.commit()
        with patch('data_sources.requests.request') as request:
            result = monitor_service.check_query(query, force=True)
        assert result['status'] == 'error' and REFUSED in result['error_message']
        assert query.consecutive_failures == 1
        request.assert_not_called()

    def test_a_webhook_and_teams_are_not_called(self, app):
        from notification_service import notification_service
        with patch('notification_service.requests.request') as request, \
                patch('notification_service.requests.post') as post:
            results = [notification_service._post_json('http://169.254.169.254/hook', {'a': 1}, {}),
                       notification_service._post_teams('http://[fe80::1]/teams', {'a': 1})]
        for result in results:
            # A refusal: retrying does not help
            assert result['success'] is False and result['permanent'] is True and REFUSED in result['message']
        request.assert_not_called()
        post.assert_not_called()
