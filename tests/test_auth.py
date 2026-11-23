"""
Login tests (auth.py): protected pages and APIs, login/logout, token for scripts.
"""
import re
import pytest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from werkzeug.security import generate_password_hash


# The API token of the tests: 32 characters at least, as the real ones
TOKEN = 'token-for-scripts-' + 'x' * 22


# Login enabled: user admin, password secret
LOGIN = dict(
    LOGIN_DISABLED=False,
    LOGIN_USERNAME='admin',
    LOGIN_PASSWORD_HASH=generate_password_hash('secret', method='pbkdf2:sha256:1000'),
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
    """Without login: pages go to the login page, the API answers 401. Static files and the public health check:
    test_offline_assets.py and test_public_health_check_shows_only_the_state."""

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

    @pytest.mark.parametrize('username, password', [('admin', 'wrong'), ('root', 'secret')])
    def test_wrong_username_or_password(self, client, username, password):
        response = login(client, username=username, password=password)
        assert response.status_code == 200 and 'Wrong username or password' in response.get_data(as_text=True)
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
        monkeypatch.setattr(auth, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))
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


class TestSessions:
    """
    A session ends after ERRORENGINE_SESSION_HOURS without the user doing anything, and the requests a page
    makes by itself do not count. Signing out ends every copy of it, and so does a restart.
    """

    @pytest.fixture
    def clock(self, auth_app, monkeypatch):
        import auth
        now = [1_000_000.0]
        monkeypatch.setattr(auth, 'time', SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))
        auth_app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
        return now

    def test_what_a_page_asks_by_itself_does_not_keep_it_alive(self, client, clock):
        login(client)
        clock[0] += 3000
        counter = client.get('/api/errors/summary')
        assert counter.status_code == 200 and 'Set-Cookie' not in counter.headers
        clock[0] += 700
        assert client.get('/api/errors/summary').status_code == 401 and client.get('/').status_code == 302

    def test_what_the_user_does_keeps_it_alive(self, client, clock):
        login(client)
        for _ in range(3):
            clock[0] += 3000
            assert client.get('/').status_code == 200
        clock[0] += 3000
        assert client.post('/api/queries/999/toggle').status_code == 404   # a change counts too
        clock[0] += 3000
        assert client.get('/').status_code == 200

    def test_the_reload_of_a_live_page_is_not_done_by_the_user(self, client, clock):
        login(client)
        clock[0] += 3000
        client.set_cookie('errorengine_background', '1')
        page = client.get('/errors')
        assert page.status_code == 200
        assert any(c.startswith('errorengine_background=;') for c in page.headers.getlist('Set-Cookie'))
        assert client.get_cookie('errorengine_background') is None
        clock[0] += 700
        assert client.get('/').status_code == 302
        js = (Path(__file__).resolve().parent.parent / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
        reload = js[js.index('    reload() {'):]
        assert "document.cookie = 'errorengine_background=1" in reload[:reload.index('location.reload()')]

    def test_signing_out_ends_every_copy(self, auth_app):
        browser, copy = auth_app.test_client(), auth_app.test_client()
        login(browser)
        copy.set_cookie('session', browser.get_cookie('session').value)
        assert copy.get('/').status_code == 200
        assert browser.post('/logout').headers['Location'] == '/login'
        assert browser.get('/').status_code == 302
        assert copy.get('/').status_code == 302 and copy.get('/api/queries').status_code == 401

    def test_only_who_is_signed_in_can_sign_everyone_out(self, auth_app):
        browser, stranger = auth_app.test_client(), auth_app.test_client()
        login(browser)
        assert stranger.post('/logout').status_code == 302
        assert browser.get('/').status_code == 200

    def test_a_restart_ends_them(self, auth_app):
        from app import create_app
        from models import db
        browser = auth_app.test_client()
        login(browser)
        restarted = create_app('testing')
        restarted.config.update(LOGIN)
        copy = restarted.test_client()
        copy.set_cookie('session', browser.get_cookie('session').value)
        assert copy.get('/').status_code == 302
        with restarted.app_context():
            db.engine.dispose()


class TestConfiguration:
    def test_without_hash_nobody_gets_in(self, client, auth_app):
        auth_app.config['LOGIN_PASSWORD_HASH'] = ''
        html = client.get('/login').get_data(as_text=True)
        assert 'Sign-in is not configured yet' in html
        assert 'ERRORENGINE_PASSWORD_HASH' in html
        login(client)
        assert client.get('/').status_code == 302
        assert client.get('/api/queries').status_code == 401

    def test_invalid_hash(self, client, auth_app):
        auth_app.config['LOGIN_PASSWORD_HASH'] = 'not-a-hash'
        assert 'Wrong username or password' in login(client).get_data(as_text=True)


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

    def test_the_token_reads_and_runs_only_the_actions_of_the_api_reference(self, client, auth_app, caplog):
        """Every route of the API, those added later included: changing the configuration, SQL or servers typed
        in a form, test emails and the cleanup need the session."""
        auth_app.config['API_TOKEN'] = TOKEN
        headers = {'Authorization': f'Bearer {TOKEN}'}
        refused = set()
        for rule in auth_app.url_map.iter_rules():
            if rule.rule.startswith('/api/'):
                url = re.sub(r'<(?:\w+:)?\w+>', '999999', rule.rule)
                for method in rule.methods - {'HEAD', 'OPTIONS'}:
                    if client.open(url, method=method, headers=headers).status_code == 403:
                        refused.add(rule.endpoint)
        assert refused == {'api.api_toggle_query', 'api.api_create_routing_rule', 'api.api_update_routing_rule',
                           'api.api_delete_routing_rule', 'api.api_connection_test_new',
                           'api.api_connection_test_query', 'api.api_test_email', 'api.api_sample_email',
                           'api.api_run_cleanup'}
        assert 'the API token only reads and runs the actions listed in docs/api-reference.md' in caplog.text

    def test_the_session_keeps_what_the_token_cannot_do(self, client, auth_app, sample_query):
        auth_app.config['API_TOKEN'] = TOKEN
        url = f'/api/queries/{sample_query.id}/toggle'
        answer = client.post(url, headers={'Authorization': f'Bearer {TOKEN}'})
        assert answer.status_code == 403 and answer.get_json()['error'] == 'forbidden'
        login(client)
        assert client.post(url).get_json()['is_active'] is False

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
