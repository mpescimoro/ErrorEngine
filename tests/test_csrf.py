"""
CSRF: the forms and the requests of the pages that change something carry a token
the server checks; scripts with the API token do not need it.
"""
import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from models import db, MonitoredQuery, DatabaseConnection, NotificationChannel

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
UNSAFE = {'POST', 'PUT', 'DELETE', 'PATCH'}


@pytest.fixture
def csrf_app(app):
    """The check on, as in production: TestingConfig turns it off for the other tests."""
    app.config['CSRF_DISABLED'] = False
    return app


def token_of(page) -> str:
    """The token a page carries, in a form or in the meta tag that app.js reads."""
    found = re.search(r'name="csrf_token" value="([^"]+)"|name="csrf-token" content="([^"]+)"',
                      page.get_data(as_text=True))
    return found.group(1) or found.group(2)


def test_every_form_that_posts_carries_the_token():
    forms = []
    for template in sorted(TEMPLATES.glob('*.html')):
        html = template.read_text(encoding='utf-8')
        for form in re.finditer(r'<form[^>]*method="post".*?</form>', html, re.S | re.I):
            forms.append((template.name, 'csrf_token()' in form.group(0)))
    assert len(forms) == 8 and all(has_token for _, has_token in forms), forms


def test_every_route_that_changes_something_refuses_a_request_without_the_token(csrf_app, client):
    conn = DatabaseConnection(name='Src', db_type='sqlite', database=':memory:')
    channel = NotificationChannel(name='Hook', channel_type='webhook')
    db.session.add_all([conn, channel])
    db.session.add(MonitoredQuery(name='Orders', sql_query='SELECT 1 AS ID', key_fields='ID',
                                  email_recipients='ops@example.com'))
    db.session.commit()
    checked = 0
    for rule in csrf_app.url_map.iter_rules():
        url = re.sub(r'<(?:int:)?\w+>', '1', rule.rule)
        for method in sorted(rule.methods & UNSAFE):
            response = client.open(url, method=method, json={}, headers={'Referer': 'http://localhost/queries'})
            if url.startswith('/api/'):
                assert response.status_code == 403 and response.get_json()['error'] == 'csrf', (method, url)
            else:
                # Back to the page with the form, which says what happened
                assert response.status_code == 302 and response.location.endswith('/queries'), (method, url)
            checked += 1
    assert checked == 26
    # Nothing was changed or deleted
    assert (MonitoredQuery.query.count(), DatabaseConnection.query.count(),
            NotificationChannel.query.count()) == (1, 1, 1)
    assert 'The page was out of date' in client.get('/queries').get_data(as_text=True)


def test_the_token_of_the_page_is_accepted_and_not_from_another_site(csrf_app, client, sample_query):
    token = token_of(client.get('/queries'))
    url = f'/api/queries/{sample_query.id}/toggle'
    assert client.post(url, json={}, headers={'X-CSRF-Token': token}).status_code == 200
    assert client.post(url, json={}, headers={'X-CSRF-Token': 'x' + token}).status_code == 403
    # The browser says where the request comes from (HTTPS and localhost only): same site is not enough
    for site, status in (('cross-site', 403), ('same-site', 403), ('same-origin', 200), ('none', 200)):
        headers = {'X-CSRF-Token': token, 'Sec-Fetch-Site': site}
        assert client.post(url, json={}, headers=headers).status_code == status, site
    # A form sends it as a field
    response = client.post(f'/queries/{sample_query.id}/delete', data={'csrf_token': token})
    assert response.status_code == 302 and MonitoredQuery.query.count() == 0


def test_scripts_with_the_api_token_do_not_need_it(csrf_app, client, sample_query):
    csrf_app.config.update(LOGIN_DISABLED=False, API_TOKEN='t' * 40, LOGIN_USERNAME='admin',
                           LOGIN_PASSWORD_HASH=generate_password_hash('secret', method='pbkdf2:sha256:1000'),
                           SECRET_KEY='k' * 40)
    url = f'/api/queries/{sample_query.id}/toggle'
    assert client.post(url, headers={'Authorization': 'Bearer ' + 't' * 40}).status_code == 200
    assert client.post(url, headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_the_login_needs_the_token_and_gets_a_new_one(csrf_app, client):
    csrf_app.config.update(LOGIN_DISABLED=False, LOGIN_USERNAME='admin', SECRET_KEY='k' * 40,
                           LOGIN_PASSWORD_HASH=generate_password_hash('secret', method='pbkdf2:sha256:1000'))
    credentials = {'username': 'admin', 'password': 'secret'}
    refused = client.post('/login', data=credentials, headers={'Referer': 'http://localhost/login'})
    assert refused.location.endswith('/login')
    assert client.get('/').status_code == 302          # not signed in
    before = token_of(client.get('/login'))
    assert client.post('/login', data=dict(credentials, csrf_token=before)).location.endswith('/')
    after = token_of(client.get('/'))
    assert after != before
    assert client.post('/logout', data={'csrf_token': after}).location.endswith('/login')
