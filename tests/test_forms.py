"""
Query and connection forms: what they refuse and what they keep. A form that cannot be saved comes
back as it was sent, passwords and tokens excepted, and says what to fix; nothing of it is saved.
"""
import re

import pytest

from models import db, MonitoredQuery, DatabaseConnection, NotificationChannel


@pytest.fixture
def source(app):
    connection = DatabaseConnection(name='ERP', db_type='sqlite', database=':memory:')
    db.session.add(connection)
    db.session.commit()
    return connection


def query_form(connection, **changes):
    """What the browser sends for a new query that can be saved, with these changes."""
    form = {'name': 'Orders', 'source_type': 'database', 'db_connection_id': str(connection.id),
            'sql_query': 'SELECT id AS ID FROM orders', 'key_fields': 'ID', 'is_active': 'on',
            'description': 'Typed description', 'check_interval_value': '15', 'check_interval_unit': 'minutes',
            'schedule_days': '1,2,3,4,5', 'reminder_interval_minutes': '60', 'reminder_max_count': '5',
            'email_recipients': 'ops@example.com'}
    form.update(changes)
    return form


class TestQueryForm:

    def test_a_form_without_problems_is_saved(self, client, source):
        assert client.post('/queries/new', data=query_form(source)).status_code == 302
        query = MonitoredQuery.query.one()
        assert (query.name, query.check_interval_minutes, query.schedule_days) == ('Orders', 15, '1,2,3,4,5')

    @pytest.mark.parametrize('changes, says', [
        ({'name': '  '}, 'Name: required'),
        ({'key_fields': ' , ,'}, 'Key fields: at least one column name'),
        ({'check_interval_value': '0'}, 'Interval: a whole number, from 1 minute to 24 hours'),
        ({'check_interval_value': '-5'}, 'Interval: a whole number, from 1 minute to 24 hours'),
        ({'check_interval_value': '25', 'check_interval_unit': 'hours'}, 'Interval: a whole number'),
        ({'check_interval_value': 'often'}, 'Interval: a whole number'),
        ({'schedule_days': '1,x'}, 'Days: numbers from 1 (Monday) to 7 (Sunday)'),
        ({'schedule_days': '0,8'}, 'Days: numbers from 1 (Monday) to 7 (Sunday)'),
        ({'reminder_interval_minutes': 'hourly'}, 'Reminders: whole numbers from 1'),
        ({'reminder_max_count': '0'}, 'Reminders: whole numbers from 1'),
    ])
    def test_what_cannot_be_saved_comes_back_as_typed(self, client, source, changes, says):
        response = client.post('/queries/new', data=query_form(source, **changes))
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and says in html
        assert 'Typed description' in html and 'SELECT id AS ID FROM orders' in html and 'New query' in html
        assert MonitoredQuery.query.count() == 0

    def test_an_edit_that_cannot_be_saved_changes_nothing(self, client, source):
        client.post('/queries/new', data=query_form(source))
        query_id = MonitoredQuery.query.one().id
        html = client.post(f'/queries/{query_id}/edit',
                           data=query_form(source, name='Renamed', key_fields=',')).get_data(as_text=True)
        assert 'Key fields: at least one column name' in html
        assert 'value="Renamed"' in html and 'Edit query' in html
        assert (MonitoredQuery.query.one().name, MonitoredQuery.query.one().key_fields) == ('Orders', 'ID')

    def test_a_name_already_used_is_said_so(self, client, source):
        client.post('/queries/new', data=query_form(source))
        client.post('/queries/new', data=query_form(source, name='Stock'))
        html = client.post('/queries/new', data=query_form(source)).get_data(as_text=True)
        assert 'Name: another query is already called Orders' in html
        stock = MonitoredQuery.query.filter_by(name='Stock').one()
        html = client.post(f'/queries/{stock.id}/edit', data=query_form(source)).get_data(as_text=True)
        assert 'Name: another query is already called Orders' in html
        assert sorted(q.name for q in MonitoredQuery.query.all()) == ['Orders', 'Stock']
        # Its own name is not a problem
        assert client.post(f'/queries/{stock.id}/edit', data=query_form(source, name='Stock')).status_code == 302

    def test_a_channel_ticked_stays_ticked(self, client, source):
        # The new query is not saved: the page is prepared without the channel trying to save it along
        channel = NotificationChannel(name='Teams IT', channel_type='teams', config='{}')
        db.session.add(channel)
        db.session.commit()
        html = client.post('/queries/new', data=query_form(source, key_fields=',',
                                                          notification_channels=str(channel.id))).get_data(as_text=True)
        assert 'Key fields: at least one column name' in html
        assert re.search(rf'value="{channel.id}"\s+checked', html)
        assert MonitoredQuery.query.count() == 0

    def test_a_token_typed_is_not_sent_back(self, client):
        form = {'name': 'API', 'source_type': 'http', 'source_url': 'https://api.example.com/errors',
                'source_auth_type': 'bearer', 'source_auth_token': 'typed-secret-token', 'key_fields': 'ID',
                'check_interval_value': '0', 'check_interval_unit': 'minutes'}
        html = client.post('/queries/new', data=form).get_data(as_text=True)
        assert 'Interval:' in html and 'https://api.example.com/errors' in html
        assert 'typed-secret-token' not in html

    def test_days_that_are_not_numbers_do_not_break_the_pages(self, client, source):
        # Saved before the form checked them
        query = MonitoredQuery(name='Old', sql_query='SELECT 1', key_fields='ID', schedule_days='1,x,9',
                               db_connection_id=source.id)
        db.session.add(query)
        db.session.commit()
        for url in ('/', '/queries', f'/queries/{query.id}/edit'):
            assert client.get(url).status_code == 200, url
        assert query.get_schedule_days_list() == [1]


class TestStatementsThatChangeData:
    """A query that can change data runs only on a connection that allows it, confirmed at each save."""

    def test_a_connection_that_does_not_allow_them_refuses_the_save(self, client, source):
        html = client.post('/queries/new', data=query_form(source, sql_query='DELETE FROM orders')).get_data(
            as_text=True)
        assert 'The query contains DELETE, which can change data, and the connection ERP does not allow it' in html
        assert 'name="confirm_write"' not in html and MonitoredQuery.query.count() == 0

    def test_where_allowed_each_save_needs_the_confirmation(self, client, source):
        source.allow_writes = True
        db.session.commit()
        sql = 'WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone'
        html = client.post('/queries/new', data=query_form(source, sql_query=sql)).get_data(as_text=True)
        assert 'The query contains DELETE, which can change data: confirm it' in html
        assert 'name="confirm_write"' in html and MonitoredQuery.query.count() == 0
        assert client.post('/queries/new', data=query_form(source, sql_query=sql, confirm_write='on')).status_code == 302
        # Opened again, the confirmation is there from the start: unticked, the save is refused again
        query_id = MonitoredQuery.query.one().id
        assert 'name="confirm_write"' in client.get(f'/queries/{query_id}/edit').get_data(as_text=True)
        assert client.post(f'/queries/{query_id}/edit', data=query_form(source, sql_query=sql)).status_code == 200
        assert client.post(f'/queries/{query_id}/edit',
                           data=query_form(source, sql_query=sql, confirm_write='on')).status_code == 302

    def test_a_read_query_needs_nothing(self, client, source):
        page = client.post('/queries/new', data=query_form(source, sql_query="SELECT 'DELETE' AS ID FROM orders"))
        assert page.status_code == 302


class TestConnectionForm:

    @pytest.mark.parametrize('changes, says', [
        ({'name': ' '}, 'Name: required'),
        ({'port': 'abc'}, 'Port: a number from 1 to 65535, or empty for the usual one'),
        ({'port': '70000'}, 'Port: a number from 1 to 65535'),
        ({'db_type': 'nosuchdb'}, 'Type: one of those in the list'),
    ])
    def test_what_cannot_be_saved_comes_back_as_typed(self, client, changes, says):
        form = {'name': 'ERP', 'db_type': 'sqlite', 'database': '/data/typed.db', 'username': 'reader',
                'password': 'typed-password', **changes}
        response = client.post('/connections/new', data=form)
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and says in html
        assert '/data/typed.db' in html and 'value="reader"' in html and 'New connection' in html
        assert 'typed-password' not in html and DatabaseConnection.query.count() == 0

    def test_an_edit_that_cannot_be_saved_changes_nothing(self, client, source):
        html = client.post(f'/connections/{source.id}/edit',
                           data={'name': 'Renamed', 'db_type': 'sqlite', 'database': ':memory:',
                                 'port': 'x'}).get_data(as_text=True)
        assert 'Port: a number' in html and 'value="Renamed"' in html and 'Edit connection' in html
        assert DatabaseConnection.query.one().name == 'ERP'

    def test_statements_that_change_data_are_off_unless_allowed(self, client):
        client.post('/connections/new', data={'name': 'Reads', 'db_type': 'sqlite', 'database': ':memory:'})
        client.post('/connections/new', data={'name': 'Writes', 'db_type': 'sqlite', 'database': ':memory:',
                                              'allow_writes': 'on'})
        saved = DatabaseConnection.query.order_by(DatabaseConnection.name).all()
        assert [(c.name, c.allow_writes) for c in saved] == [('Reads', False), ('Writes', True)]
        writes = saved[1]
        assert 'name="allow_writes"' in client.get(f'/connections/{writes.id}/edit').get_data(as_text=True)
        client.post(f'/connections/{writes.id}/edit', data={'name': 'Writes', 'db_type': 'sqlite',
                                                           'database': ':memory:'})
        assert db.session.get(DatabaseConnection, writes.id).allow_writes is False
