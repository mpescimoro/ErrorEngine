"""
Login tests (auth.py): protected pages and APIs, login/logout, token for scripts.
"""
import pytest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from flask import Config
from werkzeug.security import generate_password_hash
from config import config, DEFAULT_SECRET_KEY, INSECURE_SECRET_KEYS


@pytest.fixture
def auth_app(app):
    """Test app with login enabled (user admin / password secret)."""
    app.config.update(
        LOGIN_DISABLED=False,
        LOGIN_USERNAME='admin',
        LOGIN_PASSWORD_HASH=generate_password_hash('secret', method='pbkdf2:sha256:1000'),
        SECRET_KEY='test-key',
        API_TOKEN='',
    )
    with patch('auth.time.sleep'):  # no wait after a wrong login
        yield app


@pytest.fixture
def client(auth_app):
    return auth_app.test_client()


def login(client, username='admin', password='secret', next_url=None):
    data = {'username': username, 'password': password}
    if next_url is not None:
        data['next'] = next_url
    return client.post('/login', data=data)


class TestProtection:
    """Without login nothing is reachable, except login, static files and health."""

    def test_page_redirects_to_login(self, client):
        response = client.get('/errors?query_id=3')
        assert response.status_code == 302
        location = urlsplit(response.headers['Location'])
        assert location.path == '/login'
        assert parse_qs(location.query)['next'] == ['/errors?query_id=3']

    def test_api_without_login_401(self, client):
        response = client.get('/api/queries')
        assert response.status_code == 401
        assert response.get_json()['error'] == 'unauthorized'

    def test_api_changes_blocked(self, client):
        assert client.post('/api/cleanup/run').status_code == 401

    def test_health_is_public(self, client):
        assert client.get('/api/health').status_code == 200

    def test_static_files_are_public(self, client):
        assert client.get('/static/css/app.css').status_code == 200

    def test_login_page_is_public(self, client):
        response = client.get('/login')
        assert response.status_code == 200
        assert 'name="password"' in response.get_data(as_text=True)


class TestLogin:
    def test_correct_login_goes_back_to_requested_page(self, client):
        response = login(client, next_url='/errors?query_id=3')
        assert response.status_code == 302
        assert response.headers['Location'] == '/errors?query_id=3'
        assert client.get('/errors').status_code == 200
        assert client.get('/api/queries').status_code == 200

    def test_session_cookie_is_protected(self, client):
        response = login(client)
        cookie = response.headers['Set-Cookie']
        assert 'HttpOnly' in cookie
        assert 'SameSite=Lax' in cookie
        assert 'Expires=' in cookie  # session with an expiry

    def test_wrong_password(self, client):
        response = login(client, password='wrong')
        assert response.status_code == 200
        assert 'Wrong username or password' in response.get_data(as_text=True)
        assert client.get('/').status_code == 302

    def test_wrong_user(self, client):
        response = login(client, username='root')
        assert 'Wrong username or password' in response.get_data(as_text=True)
        assert client.get('/').status_code == 302

    @pytest.mark.parametrize('target', ['https://example.com/x', '//example.com', '/\\example.com', 'javascript:alert(1)'])
    def test_next_to_other_sites_ignored(self, client, target):
        response = login(client, next_url=target)
        assert response.headers['Location'] == '/'

    def test_user_and_logout_on_page(self, client):
        login(client)
        html = client.get('/').get_data(as_text=True)
        assert 'class="sidebar-user-name" title="admin"' in html
        assert 'action="/logout"' in html

    def test_logout(self, client):
        login(client)
        response = client.post('/logout')
        assert response.headers['Location'] == '/login'
        assert client.get('/').status_code == 302

    def test_password_change_ends_sessions(self, client, auth_app):
        login(client)
        assert client.get('/').status_code == 200
        auth_app.config['LOGIN_PASSWORD_HASH'] = generate_password_hash('new', method='pbkdf2:sha256:1000')
        assert client.get('/').status_code == 302


class TestConfiguration:
    def test_without_hash_nobody_gets_in(self, client, auth_app):
        auth_app.config['LOGIN_PASSWORD_HASH'] = ''
        html = client.get('/login').get_data(as_text=True)
        assert 'Sign-in is not configured yet' in html
        assert 'ERRORENGINE_PASSWORD_HASH' in html
        login(client)
        assert client.get('/').status_code == 302
        assert client.get('/api/queries').status_code == 401

    def test_every_published_key_is_public(self):
        """Default keys of past versions and the .env.example placeholder stay blocked after an upgrade."""
        example = (Path(__file__).resolve().parent.parent / '.env.example').read_text(encoding='utf-8')
        placeholder = next(line.split('=', 1)[1].strip() for line in example.splitlines()
                           if line.startswith('SECRET_KEY='))
        for key in (DEFAULT_SECRET_KEY, 'chiave-segreta-da-cambiare-in-produzione', placeholder):
            assert key in INSECURE_SECRET_KEYS, key

    @pytest.mark.parametrize('key', sorted(INSECURE_SECRET_KEYS))
    def test_public_secret_key_blocks_login(self, client, auth_app, key):
        """With the default key or the .env.example one, the cookie could be forged."""
        auth_app.config['SECRET_KEY'] = key
        assert 'SECRET_KEY' in client.get('/login').get_data(as_text=True)
        login(client)
        assert client.get('/').status_code == 302

    def test_invalid_hash(self, client, auth_app):
        auth_app.config['LOGIN_PASSWORD_HASH'] = 'not-a-hash'
        assert 'Wrong username or password' in login(client).get_data(as_text=True)

    def test_production_secret_key_read_from_property(self, monkeypatch):
        """from_object gets an instance: SECRET_KEY is the value, not the property object."""
        monkeypatch.setenv('SECRET_KEY', 'production-key')
        cfg = Config('.')
        cfg.from_object(config['production']())
        assert cfg['SECRET_KEY'] == 'production-key'


class TestTokenApi:
    def test_valid_token(self, client, auth_app):
        auth_app.config['API_TOKEN'] = 'token-script'
        headers = {'Authorization': 'Bearer token-script'}
        assert client.get('/api/queries', headers=headers).status_code == 200

    def test_wrong_token(self, client, auth_app):
        auth_app.config['API_TOKEN'] = 'token-script'
        headers = {'Authorization': 'Bearer other'}
        assert client.get('/api/queries', headers=headers).status_code == 401

    def test_token_does_not_open_pages(self, client, auth_app):
        auth_app.config['API_TOKEN'] = 'token-script'
        headers = {'Authorization': 'Bearer token-script'}
        assert client.get('/', headers=headers).status_code == 302

    def test_token_not_configured(self, client):
        headers = {'Authorization': 'Bearer '}
        assert client.get('/api/queries', headers=headers).status_code == 401
