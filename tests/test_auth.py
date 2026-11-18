"""
Login tests (auth.py): protected pages and APIs, login/logout, token for scripts.
"""
import pytest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from flask import Config
from werkzeug.security import generate_password_hash
from config import config, DEFAULT_SECRET_KEY, INSECURE_SECRET_KEYS


# The API token of the tests: 32 characters at least, as the real ones
TOKEN = 'token-for-scripts-' + 'x' * 22


# Login enabled: user admin, password secret
LOGIN = dict(
    LOGIN_DISABLED=False,
    LOGIN_USERNAME='admin',
    LOGIN_PASSWORD_HASH=generate_password_hash('secret', method='pbkdf2:sha256:1000'),
    SECRET_KEY='test-key-' + 'k' * 23,
    API_TOKEN='',
)


@pytest.fixture
def auth_app(app):
    """Test app with login enabled (user admin / password secret)."""
    app.config.update(LOGIN)
    return app


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
        with client.get('/static/css/app.css') as response:
            assert response.status_code == 200

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

    def test_wrong_passwords_make_the_address_wait_without_holding_a_thread(self, client, auth_app, monkeypatch):
        import time
        import auth
        monkeypatch.setattr(auth, 'LOGIN_BLOCK_SECONDS', 0.3)
        start = time.monotonic()
        for _ in range(auth.LOGIN_ATTEMPTS):
            assert login(client, password='wrong').status_code == 200
        assert time.monotonic() - start < 2.5      # no second of wait inside the request any more
        blocked = login(client)                    # the right password, but too soon
        assert blocked.status_code == 429 and 'Too many wrong passwords' in blocked.get_data(as_text=True)
        assert client.get('/').status_code == 302
        # Another address is not blocked; a port added by a proxy does not make another address
        right, wrong = {'username': 'admin', 'password': 'secret'}, {'username': 'admin', 'password': 'wrong'}
        assert auth_app.test_client().post('/login', data=right,
                                           environ_base={'REMOTE_ADDR': '10.0.0.2'}).status_code == 302
        ipv6 = auth_app.test_client()
        for port in range(auth.LOGIN_ATTEMPTS):
            ipv6.post('/login', data=wrong, environ_base={'REMOTE_ADDR': f'[2001:db8::1]:{50000 + port}'})
        assert ipv6.post('/login', data=right,
                         environ_base={'REMOTE_ADDR': '[2001:db8::1]:50099'}).status_code == 429
        # Once the wait is over, the right password gets in
        time.sleep(0.35)
        assert login(client).status_code == 302 and client.get('/').status_code == 200

    def test_tries_that_arrive_together_are_counted_before_the_password_is_checked(self, file_app, monkeypatch,
                                                                                    caplog):
        import threading
        import time
        import auth
        # On a file: the pages read the database, and the in-memory one is no place for threads
        file_app.config.update(LOGIN)
        checked, real_check = [], auth.check_password_hash

        def slow_check(hashed, password):
            checked.append(password)
            time.sleep(0.1)          # as scrypt: long enough for the other tries to arrive
            return real_check(hashed, password)
        monkeypatch.setattr(auth, 'check_password_hash', slow_check)
        together, codes = threading.Barrier(12), []

        def try_wrong():
            client = file_app.test_client()
            together.wait()
            codes.append(login(client, password='wrong').status_code)
        threads = [threading.Thread(target=try_wrong) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(checked) == auth.LOGIN_ATTEMPTS and sorted(codes) == [200] * 5 + [429] * 7
        assert caplog.text.count('paused for') == 1

    def test_a_right_password_starts_the_count_again(self, client, auth_app):
        import auth
        for _ in range(auth.LOGIN_ATTEMPTS - 1):
            login(client, password='wrong')
        assert login(client).status_code == 302
        client.post('/logout')
        # Had the count gone on, it would be at the limit and this try would wait
        assert login(client, password='wrong').status_code == 200

    def test_after_a_quarter_of_an_hour_without_wrong_passwords_the_count_starts_again(self, client, auth_app,
                                                                                         monkeypatch):
        import auth
        clock = [1000.0]
        monkeypatch.setattr(auth, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
        for _ in range(auth.LOGIN_ATTEMPTS):
            login(client, password='wrong')
        assert login(client, password='wrong').status_code == 429
        clock[0] += auth.LOGIN_FORGET_SECONDS + 1
        # After a wait one try passes anyway: the second one shows whether the count went on
        assert [login(client, password='wrong').status_code for _ in range(2)] == [200, 200]

    def test_a_user_name_cannot_add_lines_to_the_log(self, client, auth_app, caplog):
        login(client, username='nobody\n2026-01-01 00:00:00,000 - auth - INFO - Login: admin', password='wrong')
        failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Failed login')]
        assert len(failed) == 1 and '\n' not in failed[0] and 'nobody\\n2026' in failed[0]


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

    @pytest.mark.parametrize('key', sorted(INSECURE_SECRET_KEYS) + ['k' * 31])
    def test_public_or_short_secret_key_blocks_login(self, client, auth_app, key):
        """With the default key, the .env.example one or a short one, the cookie could be forged."""
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
        auth_app.config['API_TOKEN'] = TOKEN
        headers = {'Authorization': f'Bearer {TOKEN}'}
        assert client.get('/api/queries', headers=headers).status_code == 200

    def test_short_token_refused_and_named_at_startup(self, client, auth_app, monkeypatch, caplog):
        import config
        from app import create_app
        from models import db
        auth_app.config['API_TOKEN'] = 'x' * 31
        assert client.get('/api/queries', headers={'Authorization': 'Bearer ' + 'x' * 31}).status_code == 401
        monkeypatch.setattr(config.TestingConfig, 'API_TOKEN', 'x' * 31)
        app = create_app('testing')
        with app.app_context():
            db.engine.dispose()
        assert 'ERRORENGINE_API_TOKEN is shorter than 32 characters' in caplog.text

    def test_wrong_token(self, client, auth_app):
        auth_app.config['API_TOKEN'] = TOKEN
        headers = {'Authorization': 'Bearer other'}
        assert client.get('/api/queries', headers=headers).status_code == 401

    def test_token_does_not_open_pages(self, client, auth_app):
        auth_app.config['API_TOKEN'] = TOKEN
        headers = {'Authorization': f'Bearer {TOKEN}'}
        assert client.get('/', headers=headers).status_code == 302

    def test_token_not_configured(self, client):
        headers = {'Authorization': 'Bearer '}
        assert client.get('/api/queries', headers=headers).status_code == 401

    def test_public_health_check_shows_only_the_state(self, client, auth_app):
        auth_app.config['API_TOKEN'] = TOKEN
        public = client.get('/api/health').get_json()
        assert 'version' not in public and set(public['checks']) == {'database', 'scheduler'}
        detailed = client.get('/api/health', headers={'Authorization': f'Bearer {TOKEN}'}).get_json()
        assert 'version' in detailed and 'queries_failing' in detailed['checks']
