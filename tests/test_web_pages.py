"""
Pages: dashboard order and counters, tag filter, query detail, deleting a query,
empty values in the forms, Settings, statistics in local time.
"""
import json
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

from models import db, MonitoredQuery, ErrorRecord, EmailLog, DatabaseConnection, RoutingRule
from utils import get_utc_now, get_local_now, local_day_start_utc, format_local
from version import __version__

ROOT = Path(__file__).resolve().parent.parent


def make_query(name, **kwargs):
    query = MonitoredQuery(name=name, sql_query='SELECT 1', key_fields='ID',
                           email_recipients='ops@x.it', **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


def add_errors(query, count, first_seen=None, resolved=False):
    for i in range(count):
        db.session.add(ErrorRecord(query_id=query.id, error_hash=f'{query.id}-{i}-{first_seen}',
                                   error_data=json.dumps({'ID': i}),
                                   first_seen_at=first_seen or get_utc_now(),
                                   resolved_at=get_utc_now() if resolved else None))
    db.session.commit()


class FormValues(HTMLParser):
    """Values a browser would submit for the form (inputs, textareas, selects)."""

    def __init__(self):
        super().__init__()
        self.values, self._textarea, self._select, self._option = {}, None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get('name')
        if tag == 'input' and name:
            kind = a.get('type', 'text')
            if kind in ('checkbox', 'radio'):
                if 'checked' in a:
                    self.values.setdefault(name, []).append(a.get('value', 'on'))
            elif kind not in ('submit', 'button'):
                self.values.setdefault(name, []).append(a.get('value', ''))
        elif tag == 'textarea' and name:
            self._textarea = name
            self.values.setdefault(name, []).append('')
        elif tag == 'select' and name:
            self._select = name
        elif tag == 'option' and self._select:
            if 'selected' in a or self._select not in self.values:
                self.values[self._select] = [a.get('value', '')]

    def handle_endtag(self, tag):
        if tag == 'textarea':
            self._textarea = None
        elif tag == 'select':
            self._select = None

    def handle_data(self, data):
        if self._textarea:
            self.values[self._textarea][-1] += data


# --- Dashboard ---

class TestDashboard:

    def test_problems_first(self, app, client):
        tomorrow = str(get_local_now().isoweekday() % 7 + 1)
        make_query('Gamma ok')
        add_errors(make_query('Alpha one error'), 1)
        add_errors(make_query('Beta three errors'), 3)
        make_query('Delta outside schedule', schedule_days=tomorrow)
        make_query('Epsilon inactive', is_active=False)
        make_query('Zeta failing', consecutive_failures=2, failing_since=get_utc_now(),
                   last_failure_message='no such table')

        html = client.get('/').get_data(as_text=True)
        order = ['Zeta failing', 'Beta three errors', 'Alpha one error', 'Gamma ok',
                 'Delta outside schedule', 'Epsilon inactive']
        positions = [html.index(name) for name in order]
        assert positions == sorted(positions)
        assert 'Failing since' in html

    def test_emails_sent_today_counts_sent_emails_from_local_midnight(self, app, client):
        app.config['TIMEZONE'] = 'Europe/Rome'
        query = make_query('Orders')
        midnight = local_day_start_utc()
        for minutes, status in ((20, 'sent'), (60, 'sent'), (-20, 'sent'), (30, 'failed')):
            db.session.add(EmailLog(query_id=query.id, sent_at=midnight + timedelta(minutes=minutes),
                                    recipients='ops@x.it', status=status))
        db.session.commit()

        html = client.get('/').get_data(as_text=True)
        card = html[html.index('Emails sent today'):]
        assert re.search(r'stat-value">\s*2\s*<', card)       # 00:20 and 01:00 local, not the failed one
        assert '1 failed' in card

    def test_switch_icons_instead_of_pause(self, app, client):
        make_query('On')
        make_query('Off', is_active=False)
        html = client.get('/').get_data(as_text=True)
        assert 'bi-toggle-on' in html and 'bi-toggle-off' in html
        assert 'bi-pause"' not in html


# --- Queries list ---

def test_tag_filter_is_exact(app, client):
    make_query('Production orders', tags='erp,prod')
    make_query('Pre-production orders', tags='erp,preprod')
    html = client.get('/queries?tag=prod').get_data(as_text=True)
    assert 'Production orders' in html
    assert 'Pre-production orders' not in html
    assert 'Pre-production orders' in client.get('/queries?tag=preprod').get_data(as_text=True)


# --- Query detail ---

class TestQueryDetail:

    def test_real_number_of_active_errors(self, app, client):
        query = make_query('Movements')
        add_errors(query, 120)
        html = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert re.search(r'stat-label">Active errors</div>\s*<div class="stat-value">120<', html)
        assert 'View all 120 errors' in html
        assert html.count('errorManager.resolve(') == 10           # the 10 most recent on the page

    def test_no_empty_tag_badge(self, app, client):
        for tags in ('', None):
            query = make_query(f'No tags {tags!r}', tags=tags)
            html = client.get(f'/queries/{query.id}').get_data(as_text=True)
            assert '/queries?tag=' not in html

    def test_tags_link_to_the_filter(self, app, client):
        query = make_query('Tagged', tags='erp, prod')
        html = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert '/queries?tag=erp' in html and '/queries?tag=prod' in html

    def test_failing_query_badge(self, app, client):
        query = make_query('Shipments', consecutive_failures=3, failing_since=get_utc_now())
        html = client.get(f'/queries/{query.id}').get_data(as_text=True)
        title = html[html.index('<h1'):html.index('</h1>')]
        assert 'Failing' in title and 'badge-success' not in title

    def test_activate_button_has_a_label(self, app, client):
        query = make_query('Orders')
        html = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert re.search(r'queryManager\.toggle\(\d+\)">\s*<i class="bi bi-pause-circle"></i>\s*Deactivate', html)

    def test_paused_rules_are_marked(self, app, client):
        query = make_query('Routed', routing_enabled=True)
        db.session.add(RoutingRule(query_id=query.id, name='Paused rule', recipients='a@x.it', is_active=False))
        db.session.commit()
        html = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert 'Paused' in html and '0 conditions' in html and '1 rule<' in html


# --- Deleting a query ---

def test_delete_query_with_email_history(app, client):
    from sqlalchemy import event
    engine = db.engine

    @event.listens_for(engine, 'connect')
    def foreign_keys_on(dbapi_connection, record):
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
    try:
        with engine.connect() as c:
            c.exec_driver_sql('PRAGMA foreign_keys=ON')
        query = make_query('Orders')
        db.session.add(EmailLog(query_id=query.id, recipients='ops@x.it', status='sent'))
        db.session.commit()
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        response = client.post(f'/queries/{query.id}/delete')
        assert response.status_code == 302
        assert db.session.get(MonitoredQuery, query.id) is None
        log = EmailLog.query.one()
        assert log.query_id is None
    finally:
        event.remove(engine, 'connect', foreign_keys_on)


# --- Forms: an empty value must not come back as the text "None" ---

class TestEmptyValues:

    def test_query_form_round_trip_keeps_empty_values_empty(self, app, client):
        connection = DatabaseConnection(name='ERP', db_type='sqlite', database=':memory:')
        db.session.add(connection)
        db.session.commit()
        query = make_query('Orders', db_connection_id=connection.id, description=None, email_template=None,
                           routing_default_recipients=None, schedule_days=None)

        html = client.get(f'/queries/{query.id}/edit').get_data(as_text=True)
        assert 'value="None"' not in html and '>None</textarea>' not in html

        form = FormValues()
        form.feed(html)
        response = client.post(f'/queries/{query.id}/edit', data=form.values)
        assert response.status_code == 302
        db.session.refresh(query)
        for field in ('description', 'email_template', 'routing_default_recipients'):
            assert getattr(query, field) in (None, ''), field
        # No days stored means every day: the form shows them all and saves them all
        assert query.schedule_days == '1,2,3,4,5,6,7'

    def test_connection_pages_do_not_show_none(self, app, client):
        connection = DatabaseConnection(name='Local file', db_type='sqlite', database='/data/erp.db')
        db.session.add(connection)
        db.session.commit()
        assert 'value="None"' not in client.get(f'/connections/{connection.id}/edit').get_data(as_text=True)
        assert '>None<' not in client.get('/connections').get_data(as_text=True)


# --- Settings ---

def test_settings_show_version_and_internal_database(app, client):
    html = client.get('/settings').get_data(as_text=True)
    assert f'v{__version__}' in html
    assert re.search(r'Internal database</td>\s*<td>\s*SQLite', html)


# --- Statistics in local time ---

class TestStatisticsLocalTime:

    def test_timeline_ends_today_and_uses_local_days(self, app, client):
        app.config['TIMEZONE'] = 'Europe/Rome'
        query = make_query('Orders')
        add_errors(query, 2, first_seen=local_day_start_utc() + timedelta(minutes=20))   # 00:20 local
        add_errors(query, 1, first_seen=local_day_start_utc(3) + timedelta(hours=12))

        timeline = client.get('/api/stats/timeline?days=7').get_json()
        assert len(timeline) == 7
        today = str(get_local_now().date())
        assert timeline[-1] == {'date': today, 'label': get_local_now().strftime('%d/%m'), 'count': 2}
        assert timeline[-4]['count'] == 1
        assert sum(day['count'] for day in timeline) == 3

    def test_errors_today_from_local_midnight(self, app, client):
        app.config['TIMEZONE'] = 'Europe/Rome'
        query = make_query('Orders')
        add_errors(query, 1, first_seen=local_day_start_utc() + timedelta(minutes=20))
        add_errors(query, 1, first_seen=local_day_start_utc() - timedelta(minutes=20))
        assert client.get('/api/stats/overview').get_json()['errors_today'] == 1

    def test_last_error_in_local_time(self, app, client):
        app.config['TIMEZONE'] = 'Europe/Rome'
        query = make_query('Orders')
        first_seen = get_utc_now().replace(microsecond=0) - timedelta(hours=2)
        add_errors(query, 1, first_seen=first_seen)
        row = client.get('/api/stats/all-queries?days=30').get_json()[0]
        assert row['last_error'] == format_local(first_seen, '%d/%m/%y %H:%M')
        assert row['last_error'] != first_seen.strftime('%d/%m/%y %H:%M')     # Rome is never UTC


# --- Scripts and styles, checked in their source ---

class TestSourceChecks:

    def test_routing_rule_keeps_its_state(self):
        js = (ROOT / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
        assert "is_active: document.getElementById('ruleIsActive').checked" in js
        assert 'is_active: true,' not in js
        form = (ROOT / 'templates' / 'query_form.html').read_text(encoding='utf-8')
        assert 'id="ruleIsActive"' in form and 'max="1000"' in form

    def test_sql_warning_answers_before_closing(self):
        form = (ROOT / 'templates' / 'query_form.html').read_text(encoding='utf-8')
        handler = form[form.index("fresh.addEventListener('click'"):]
        assert handler.index('resolve(true)') < handler.index('modal.close()')

    def test_css_rules(self):
        css = (ROOT / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        assert '.btn-warning {' in css
        assert '.table th.text-right' in css
        assert '.form-switch .form-check-input:checked::after' in css
        header = css[css.index('.card-header {'):]
        assert 'space-between' not in header[:header.index('}')]
