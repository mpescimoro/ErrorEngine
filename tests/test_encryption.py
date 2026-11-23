"""
Passwords and tokens saved in the database: every text of the settings encrypted with ERRORENGINE_ENCRYPTION_KEY,
except the fields the forms show; those saved in clear before 2.5 encrypted at the start, and the file rebuilt
without them; never sent back to the browser, and kept only for the server they were typed for.
"""
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

import config
import data_sources
from app import create_app
from encryption import PREFIX, REBUILD_MARKER, UnreadableSecret, encrypt_saved_secrets, read_keys
from models import db, DatabaseConnection, ErrorRecord, MonitoredQuery, NotificationChannel
from notification_service import notification_service

SECRETS = ('db-password-1', 'api-token-2', 'header-secret-3', 'https://hooks.example.com/secret-4',
           'hook-header-5', '123456:telegram-token-6', 'https://teams.example.com/secret-7')
# Read by the HTTP source, written by no form: a field nobody listed ends up encrypted
HIDDEN = ('basic-user-8', 'basic-password-9', 'api-key-value-10', 'body-secret-11')


def save_everything():
    """One of each place where a secret is saved, through the models."""
    connection = DatabaseConnection(name='ERP', db_type='sqlite', database=':memory:', password=SECRETS[0])
    query = MonitoredQuery(name='API', source_type='http', key_fields='ID')
    query.set_source_config({'url': 'https://api.example.com/errors', 'auth_type': 'bearer',
                             'auth_token': SECRETS[1], 'headers': {'X-Key': SECRETS[2], 'Accept': 'text/csv'},
                             'auth_username': HIDDEN[0], 'auth_password': HIDDEN[1], 'api_key_value': HIDDEN[2],
                             'body': {'login': HIDDEN[3], 'page': 2}, 'timeout': 30})
    hook = NotificationChannel(name='Hook', channel_type='webhook')
    hook.set_config({'url': SECRETS[3], 'method': 'PUT', 'headers': {'Authorization': SECRETS[4]}})
    bot = NotificationChannel(name='Bot', channel_type='telegram')
    bot.set_config({'bot_token': SECRETS[5], 'chat_id': '-1001'})
    teams = NotificationChannel(name='Teams', channel_type='teams')
    teams.set_config({'webhook_url': SECRETS[6]})
    db.session.add_all([connection, query, hook, bot, teams])
    db.session.commit()
    return connection, query, hook, bot, teams


def raw_database() -> str:
    """Everything saved in the three tables, as the database holds it."""
    with db.engine.connect() as connection:
        rows = [connection.execute(text(f'SELECT {column} FROM {table}')).scalars().all()
                for table, column in (('database_connections', 'password'), ('monitored_queries', 'source_config'),
                                      ('notification_channels', 'config'))]
    return json.dumps(rows)


def database_bytes() -> bytes:
    """The file of the database and its WAL, as a copy of them would have them."""
    database = db.engine.url.database
    return b''.join(Path(path).read_bytes() for path in (database, database + '-wal') if os.path.exists(path))


# Long, it goes to pages of its own, which the conversion frees: SQLite keeps their bytes, unless zeroed
LONG_SECRET = 'x' * 3000 + 'long-secret-12' + 'y' * 3000


def save_in_clear():
    """What a database of 2.4.2 holds: the same secrets, in clear."""
    with db.engine.begin() as connection:
        connection.execute(text("INSERT INTO database_connections (name, db_type, database, password) "
                                "VALUES ('ERP', 'sqlite', ':memory:', :p)"), {'p': SECRETS[0]})
        connection.execute(text("INSERT INTO monitored_queries (name, source_type, key_fields, source_config) "
                                "VALUES ('API', 'http', 'ID', :c)"),
                           {'c': json.dumps({'url': 'https://api.example.com/errors', 'auth_type': 'bearer',
                                             'auth_token': SECRETS[1],
                                             'headers': {'X-Key': SECRETS[2], 'X-Long': LONG_SECRET}})})
        connection.execute(text("INSERT INTO notification_channels (name, channel_type, config) VALUES "
                                "('Hook', 'webhook', :c)"),
                           {'c': json.dumps({'url': SECRETS[3], 'headers': {'Authorization': SECRETS[4]}})})


def use_keys(app, keys: str):
    app.extensions['encryption'] = read_keys(keys)


@pytest.fixture
def file_kept_as_is(file_app):
    """
    The database file as SQLite leaves it by default: the space it frees keeps the old bytes. The build of
    Ubuntu zeroes them, so that the tests would pass there without the rebuild.
    """
    def keep_freed_bytes(dbapi_connection, record):
        dbapi_connection.execute('PRAGMA secure_delete = OFF')
    event.listen(db.engine, 'connect', keep_freed_bytes)
    db.engine.dispose()
    db.create_all()
    yield file_app
    event.remove(db.engine, 'connect', keep_freed_bytes)


class TestKey:

    @pytest.mark.parametrize('key, says', [
        ('', 'ERRORENGINE_ENCRYPTION_KEY is missing'),
        ('not-a-key', 'ERRORENGINE_ENCRYPTION_KEY is not a valid key'),
        (f'{Fernet.generate_key().decode()}, short', 'key 2 of ERRORENGINE_ENCRYPTION_KEY is not a valid key'),
    ])
    def test_without_a_valid_key_errorengine_does_not_start(self, monkeypatch, caplog, key, says):
        monkeypatch.setattr(config.TestingConfig, 'ENCRYPTION_KEY', key)
        with caplog.at_level(logging.CRITICAL, logger='encryption'), pytest.raises(SystemExit):
            create_app('testing')
        assert says in caplog.text and 'Fernet.generate_key()' in caplog.text
        assert 'If the database holds passwords saved with a key, set that key' in caplog.text


class TestSaved:

    def test_every_text_is_saved_encrypted_but_the_fields_the_forms_show(self, app):
        save_everything()
        raw = raw_database()
        assert not [secret for secret in SECRETS + HIDDEN if secret in raw]
        assert 'text/csv' not in raw   # every header value, not only those that look like secrets
        # What the forms show stays readable: names of the headers, chat ID, method, HTTP address
        assert all(part in raw for part in ('X-Key', 'Accept', '-1001', 'PUT', 'https://api.example.com/errors'))
        # Numbers keep their type
        stored = MonitoredQuery.query.one().stored_source_config()
        assert stored['timeout'] == 30 and stored['body']['page'] == 2

    def test_they_come_back_in_clear(self, app):
        connection, query, hook, bot, teams = save_everything()
        db.session.expire_all()
        assert connection.password == SECRETS[0]
        config_in_clear = query.get_source_config()
        assert (config_in_clear['auth_token'], config_in_clear['auth_password']) == (SECRETS[1], HIDDEN[1])
        assert config_in_clear['headers'] == {'X-Key': SECRETS[2], 'Accept': 'text/csv'}
        assert config_in_clear['body'] == {'login': HIDDEN[3], 'page': 2} and config_in_clear['timeout'] == 30
        assert hook.get_config() == {'url': SECRETS[3], 'method': 'PUT', 'headers': {'Authorization': SECRETS[4]}}
        assert bot.get_config() == {'bot_token': SECRETS[5], 'chat_id': '-1001'}
        assert teams.get_config() == {'webhook_url': SECRETS[6]}

    def test_an_empty_password_stays_empty(self, app):
        connection = DatabaseConnection(name='Local', db_type='sqlite', database=':memory:', password='')
        db.session.add(connection)
        db.session.commit()
        assert connection._password == '' and connection.password == ''


class TestAtStart:

    def test_those_saved_in_clear_are_encrypted_and_leave_no_trace(self, file_kept_as_is, tmp_path, caplog):
        save_in_clear()
        with caplog.at_level(logging.INFO, logger='encryption'):
            found = encrypt_saved_secrets()
        assert (found['clear'], found['older'], found['unreadable'], found['rebuilt']) == (6, 0, [], True)
        raw = raw_database()
        assert not [secret for secret in SECRETS[:5] if secret in raw] and 'long-secret-12' not in raw
        db.session.expire_all()
        assert DatabaseConnection.query.one().password == SECRETS[0]
        assert MonitoredQuery.query.one().get_source_config()['headers']['X-Long'] == LONG_SECRET
        assert NotificationChannel.query.one().get_config()['url'] == SECRETS[3]
        assert '6 encrypted with ERRORENGINE_ENCRYPTION_KEY' in caplog.text and 'Database file rebuilt' in caplog.text
        # Not even in the space of the file the conversion freed, nor in its WAL
        files = database_bytes()
        assert not [secret for secret in SECRETS[:5] + ('long-secret-12',) if secret.encode() in files]
        # No copy of the database in clear, nor the marker of a rebuild to do
        assert sorted(path.name for path in tmp_path.iterdir() if 'errorengine' in path.name) in (
            ['errorengine.db'], ['errorengine.db', 'errorengine.db-shm'],
            ['errorengine.db', 'errorengine.db-shm', 'errorengine.db-wal'])

    def test_the_messages_saved_before_no_longer_show_the_channel(self, file_kept_as_is, caplog):
        """As 2.4.2 saved them: the path of the address was left in clear when it did not look like a token."""
        save_in_clear()
        failed = ("HTTPSConnectionPool(host='hooks.example.com', port=443): Max retries exceeded with url: "
                  "/secret-4 (Caused by NameResolutionError)")
        hook = NotificationChannel.query.one()
        hook.last_error = failed
        teams = NotificationChannel(name='Teams', channel_type='teams')   # the error names the other channel
        teams.set_config({'webhook_url': SECRETS[6]})
        db.session.add_all([teams, ErrorRecord(query_id=MonitoredQuery.query.one().id, error_hash='h', error_data='{}',
                                               last_notify_error=f'Not delivered: ops@x.it; Hook: {failed}')])
        db.session.commit()
        with caplog.at_level(logging.INFO, logger='encryption'):
            found = encrypt_saved_secrets()
        assert (found['messages'], found['rebuilt']) == (2, True) and '2 hidden' in caplog.text
        db.session.expire_all()
        hidden = failed.replace('/secret-4', '/***')
        assert hook.last_error == hidden and teams.last_error is None
        assert ErrorRecord.query.one().last_notify_error == f'Not delivered: ops@x.it; Hook: {hidden}'
        assert b'secret-4' not in database_bytes()
        assert encrypt_saved_secrets()['messages'] == 0

    def test_the_next_start_changes_nothing(self, file_app):
        db.create_all()
        save_in_clear()
        encrypt_saved_secrets()
        before = raw_database()
        found = encrypt_saved_secrets()
        assert (found['clear'], found['older'], found['saved'], found['rebuilt']) == (0, 0, False, False)
        assert raw_database() == before

    def test_a_rebuild_that_fails_is_tried_at_every_start(self, file_kept_as_is, caplog):
        save_in_clear()
        original = Connection.exec_driver_sql

        def disk_full(self, statement, *args, **kwargs):
            if statement == 'VACUUM':
                raise OSError('database or disk is full')
            return original(self, statement, *args, **kwargs)

        with patch.object(Connection, 'exec_driver_sql', disk_full), caplog.at_level(logging.WARNING, 'encryption'):
            found = encrypt_saved_secrets()
        assert (found['saved'], found['rebuilt']) == (True, False)
        assert 'disk is full' in caplog.text and 'Free about' in caplog.text
        assert 'ErrorEngine tries again at every start' in caplog.text
        marker = db.engine.url.database + REBUILD_MARKER
        assert os.path.exists(marker)
        # The next start: nothing left to encrypt, the rebuild still to do
        found = encrypt_saved_secrets()
        assert (found['saved'], found['rebuilt']) == (False, True) and not os.path.exists(marker)
        assert b'long-secret-12' not in database_bytes()

    def test_all_or_nothing(self, file_app, caplog):
        db.create_all()
        save_in_clear()
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER refuse BEFORE UPDATE ON notification_channels "
                                    "BEGIN SELECT RAISE(ABORT, 'refused'); END"))
        with caplog.at_level(logging.ERROR, logger='encryption'):
            found = encrypt_saved_secrets()
        assert found['saved'] is False and 'left as it was' in caplog.text
        assert not os.path.exists(db.engine.url.database + REBUILD_MARKER)
        # The connection came before the channel: its password too is still as it was
        with db.engine.connect() as connection:
            assert connection.execute(text('SELECT password FROM database_connections')).scalar() == SECRETS[0]

    def test_a_new_key_first_and_the_old_one_after(self, file_kept_as_is, caplog):
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        use_keys(file_kept_as_is, old)
        connection, query, hook, bot, teams = save_everything()
        encrypted_with_the_old_one = connection._password
        with db.engine.connect() as written_back:   # as SQLite does by itself, in time
            written_back.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)')
        assert encrypted_with_the_old_one.encode() in Path(db.engine.url.database).read_bytes()
        use_keys(file_kept_as_is, f'{new}, {old}')
        with caplog.at_level(logging.INFO, logger='encryption'):
            found = encrypt_saved_secrets()
        assert (found['clear'], found['unreadable'], found['rebuilt']) == (0, [], True) and found['older'] > 0
        assert 'the other keys can be removed' in caplog.text
        use_keys(file_kept_as_is, new)
        db.session.expire_all()
        assert connection.password == SECRETS[0] and teams.get_config() == {'webhook_url': SECRETS[6]}
        # A key changed because it got out: what it encrypted is not left in the file
        assert encrypted_with_the_old_one.encode() not in database_bytes()


class TestWrongKey:
    """Saved with a key that is not in the setting: ErrorEngine starts, and says where and what to do."""

    @pytest.fixture
    def saved(self, app):
        everything = save_everything()
        use_keys(app, Fernet.generate_key().decode())
        db.session.expire_all()
        return everything

    def test_the_start_says_where(self, saved, caplog):
        before = raw_database()
        with caplog.at_level(logging.WARNING, logger='encryption'):
            found = encrypt_saved_secrets()
        assert found['unreadable'] == ['the connection ERP', 'the query API', 'the channel Hook', 'the channel Bot',
                                       'the channel Teams']
        assert raw_database() == before
        assert 'Put that key back in the setting, after the current one and a comma' in caplog.text

    def test_each_use_says_what_to_do(self, saved):
        connection, query, hook, bot, teams = saved
        with pytest.raises(UnreadableSecret, match='The password saved in the connection ERP cannot be read'):
            connection.password
        assert 'enter it again in the connection ERP' in connection.test_connection()['message']
        with pytest.raises(ValueError, match='connection ERP'):
            connection.execute_query('SELECT 1')
        assert 'saved in the query API cannot be read' in data_sources.test_query_source(query)['message']
        result = notification_service.send_to_channel(teams, query, [{'ID': 1}])
        assert result['success'] is False and 'saved in the channel Teams cannot be read' in result['message']

    def test_the_forms_still_open(self, saved, client):
        connection, query, hook, bot, teams = saved
        for page in (f'/connections/{connection.id}/edit', f'/queries/{query.id}/edit',
                     f'/channels/{hook.id}/edit', f'/channels/{bot.id}/edit', f'/channels/{teams.id}/edit'):
            assert client.get(page).status_code == 200, page


def http_form(**changes):
    form = {'name': 'API', 'source_type': 'http', 'source_url': 'https://api.example.com/errors',
            'source_method': 'GET', 'source_auth_type': 'bearer', 'source_auth_token': '', 'source_api_key_name': '',
            'source_api_key_in': '', 'source_headers': '', 'source_response_path': '', 'key_fields': 'ID',
            'check_interval_value': '15', 'check_interval_unit': 'minutes', 'reminder_interval_minutes': '60',
            'reminder_max_count': '5'}
    form.update(changes)
    return form


SAVED_HEADERS = '{"X-Key": "(saved)", "Accept": "(saved)"}'


class TestQueryForm:
    """The saved token and header values are not in the page; left as they are, they stay, for that server."""

    @pytest.fixture
    def query(self, app):
        return save_everything()[1]

    def test_the_page_has_no_secret(self, client, query):
        html = client.get(f'/queries/{query.id}/edit').get_data(as_text=True)
        assert not [secret for secret in SECRETS + HIDDEN + ('text/csv',) if secret in html]
        assert '(saved: leave empty to keep it)' in html
        assert '{&#34;X-Key&#34;: &#34;(saved)&#34;, &#34;Accept&#34;: &#34;(saved)&#34;}' in html

    @pytest.mark.parametrize('url', ['https://api.example.com/errors', 'https://API.example.com:443/other?x=1'])
    def test_left_as_they_are_they_stay_on_the_same_server(self, client, query, url):
        form = http_form(source_url=url, source_headers=SAVED_HEADERS)
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        config_in_clear = query.get_source_config()
        assert (config_in_clear['auth_token'], config_in_clear['headers']) == (
            SECRETS[1], {'X-Key': SECRETS[2], 'Accept': 'text/csv'})
        assert config_in_clear['auth_password'] == HIDDEN[1]

    @pytest.mark.parametrize('url', ['https://collector.example.net/errors', 'https://api.example.com:8443/errors',
                                     'http://api.example.com/errors',
                                     # urlsplit reads api.example.com, requests sends it to collector.example.net
                                     'https://collector.example.net\\@api.example.com/errors'])
    def test_another_server_does_not_get_them(self, client, query, url):
        before = raw_database()
        html = client.post(f'/queries/{query.id}/edit',
                           data=http_form(source_url=url, source_headers=SAVED_HEADERS)).get_data(as_text=True)
        assert 'Token/Key: the URL is on another server, type it again' in html
        assert 'Header X-Key: the address is on another server, type its value again' in html
        assert raw_database() == before

    def test_another_server_with_everything_typed_again(self, client, query):
        form = http_form(source_url='https://collector.example.net/errors', source_auth_token='new-token',
                         source_headers='{"X-Key": "new-value"}')
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        config_in_clear = query.get_source_config()
        assert (config_in_clear['auth_token'], config_in_clear['headers']) == ('new-token', {'X-Key': 'new-value'})
        # What the form does not show, and so cannot be typed again, does not go there either; numbers stay
        assert not [field for field in ('auth_username', 'auth_password', 'api_key_value', 'body')
                    if field in config_in_clear]
        assert config_in_clear['timeout'] == 30

    def test_typed_ones_replace_and_a_line_removed_removes(self, client, query):
        form = http_form(source_auth_token='new-token', source_headers='{"X-Key": "new-value"}')
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        config_in_clear = query.get_source_config()
        assert (config_in_clear['auth_token'], config_in_clear['headers']) == ('new-token', {'X-Key': 'new-value'})

    def test_authentication_none_removes_the_token(self, client, query):
        form = http_form(source_auth_type='', source_headers=SAVED_HEADERS)
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        assert query.get_source_config()['auth_token'] == ''
        assert '(saved: leave empty to keep it)' not in client.get(f'/queries/{query.id}/edit').get_data(as_text=True)

    def test_saved_with_no_value_behind_it(self, client, query):
        html = client.post(f'/queries/{query.id}/edit',
                           data=http_form(source_headers='{"X-Other": "(saved)"}')).get_data(as_text=True)
        assert 'Header X-Other: no value saved under this name, type it' in html
        db.session.expire_all()
        assert query.get_source_config()['headers'] == {'X-Key': SECRETS[2], 'Accept': 'text/csv'}

    def test_with_a_problem_the_token_typed_is_to_be_typed_again(self, client, query):
        form = http_form(source_auth_token='typed-token', check_interval_value='0',
                         source_headers='{"X-Key": "(saved)", "X-New": "typed-value"}')
        html = client.post(f'/queries/{query.id}/edit', data=form).get_data(as_text=True)
        assert 'Interval:' in html and SECRETS[1] not in html and SECRETS[2] not in html
        assert 'typed-token' not in html and '(type it again)' in html and '(saved: leave empty' not in html
        # The headers as typed, for what is wrong to be fixed
        assert 'typed-value' in html

    def test_with_a_problem_and_nothing_typed_the_saved_one_stays(self, client, query):
        html = client.post(f'/queries/{query.id}/edit', data=http_form(check_interval_value='0',
                                                                         source_headers=SAVED_HEADERS))
        assert '(saved: leave empty to keep it)' in html.get_data(as_text=True)

    def test_a_new_query_says_nothing_is_saved(self, client, app):
        html = client.post('/queries/new', data=http_form(source_auth_token='typed-token',
                                                          check_interval_value='0')).get_data(as_text=True)
        assert 'Interval:' in html and '(saved: leave empty' not in html and 'typed-token' not in html

    def test_the_api_key_in_the_query_string(self, client, query):
        form = http_form(source_auth_type='api_key', source_api_key_name='key', source_api_key_in='query',
                         source_headers=SAVED_HEADERS)
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        stored = query.stored_source_config()
        assert (stored['api_key_in'], stored['api_key_name']) == ('query', 'key')
        assert stored['auth_token'].startswith(PREFIX)
        html = client.get(f'/queries/{query.id}/edit').get_data(as_text=True)
        assert '<option value="query" selected>' in html


def channel_form(channel_type, **changes):
    form = {'name': 'Edited', 'channel_type': channel_type, 'is_active': 'on', 'webhook_url': '',
            'webhook_method': 'PUT', 'webhook_headers': '', 'telegram_bot_token': '', 'telegram_chat_id': '-1001',
            'teams_webhook_url': ''}
    form.update(changes)
    return form


class TestChannelForm:

    @pytest.fixture
    def channels(self, app):
        return save_everything()[2:]

    def test_the_pages_have_no_secret(self, client, channels):
        for channel in channels:
            html = client.get(f'/channels/{channel.id}/edit').get_data(as_text=True)
            assert not [secret for secret in SECRETS if secret in html], channel.name
            assert '(saved: leave empty to keep it)' in html, channel.name
        html = client.get(f'/channels/{channels[0].id}/edit').get_data(as_text=True)
        assert '{&#34;Authorization&#34;: &#34;(saved)&#34;}' in html and 'value="-1001"' not in html
        assert 'value="-1001"' in client.get(f'/channels/{channels[1].id}/edit').get_data(as_text=True)

    def test_left_empty_they_stay(self, client, channels):
        hook, bot, teams = channels
        client.post(f'/channels/{hook.id}/edit', data=channel_form('webhook',
                                                                   webhook_headers='{"Authorization": "(saved)"}'))
        client.post(f'/channels/{bot.id}/edit', data=channel_form('telegram'))
        client.post(f'/channels/{teams.id}/edit', data=channel_form('teams'))
        db.session.expire_all()
        assert hook.get_config() == {'url': SECRETS[3], 'method': 'PUT', 'headers': {'Authorization': SECRETS[4]}}
        assert bot.get_config() == {'bot_token': SECRETS[5], 'chat_id': '-1001'}
        assert teams.get_config() == {'webhook_url': SECRETS[6]}

    def test_typed_ones_replace(self, client, channels):
        hook, bot, teams = channels
        client.post(f'/channels/{hook.id}/edit', data=channel_form('webhook', webhook_url='https://new.example.com/x'))
        client.post(f'/channels/{bot.id}/edit', data=channel_form('telegram', telegram_bot_token='42:new'))
        db.session.expire_all()
        assert hook.get_config() == {'url': 'https://new.example.com/x', 'method': 'PUT', 'headers': {}}
        assert bot.get_config()['bot_token'] == '42:new'

    def test_a_webhook_keeps_its_headers_on_its_server_only(self, client, channels):
        hook = channels[0]
        saved_header = '{"Authorization": "(saved)"}'
        for elsewhere in ('https://collector.example.net/x', 'https://collector.example.net\\@hooks.example.com/x'):
            html = client.post(f'/channels/{hook.id}/edit', data=channel_form(
                'webhook', webhook_url=elsewhere, webhook_headers=saved_header)).get_data(as_text=True)
            assert 'Header Authorization: the address is on another server, type its value again' in html
        client.post(f'/channels/{hook.id}/edit', data=channel_form(
            'webhook', webhook_url='https://hooks.example.com/another-path', webhook_headers=saved_header))
        db.session.expire_all()
        assert hook.get_config() == {'url': 'https://hooks.example.com/another-path', 'method': 'PUT',
                                     'headers': {'Authorization': SECRETS[4]}}

    def test_another_type_needs_its_own(self, client, channels):
        hook = channels[0]
        html = client.post(f'/channels/{hook.id}/edit', data=channel_form('teams')).get_data(as_text=True)
        assert 'Teams: the webhook URL, which starts with https://' in html
        # Only the URL of the webhook is saved, and the page says so in its part
        assert html.count('(saved: leave empty to keep it)') == 1
        db.session.expire_all()
        assert hook.channel_type == 'webhook' and hook.get_config()['url'] == SECRETS[3]

    def test_with_a_problem_what_was_typed_is_to_be_typed_again(self, client, channels):
        bot = channels[1]
        html = client.post(f'/channels/{bot.id}/edit', data=channel_form(
            'telegram', telegram_bot_token='42:typed', telegram_chat_id='')).get_data(as_text=True)
        assert 'Telegram: bot token and chat ID are both required' in html
        assert '42:typed' not in html and '(type it again)' in html


def connection_form(**changes):
    return {'name': 'ERP', 'db_type': 'postgres', 'host': 'db.example.com', 'port': '5432', 'database': 'erp',
            'username': 'reader', 'password': '', 'is_active': 'on', **changes}


class TestConnectionForm:
    """The saved password goes only to the server it was typed for."""

    @pytest.fixture
    def connection(self, app):
        connection = DatabaseConnection(name='ERP', db_type='postgres', host='db.example.com', port=5432,
                                        database='erp', username='reader', password=SECRETS[0])
        db.session.add(connection)
        db.session.commit()
        return connection

    def test_left_empty_it_stays_on_the_same_server(self, client, connection):
        assert client.post(f'/connections/{connection.id}/edit',
                           data=connection_form(database='erp2', host='DB.example.com')).status_code == 302
        db.session.expire_all()
        assert (connection.database, connection.password) == ('erp2', SECRETS[0])

    @pytest.mark.parametrize('changes', [{'host': 'collector.example.net'}, {'port': '6543'}])
    def test_another_server_does_not_get_it(self, client, connection, changes):
        html = client.post(f'/connections/{connection.id}/edit',
                           data=connection_form(**changes)).get_data(as_text=True)
        assert 'Password: the server changed, type it again' in html and SECRETS[0] not in html
        db.session.expire_all()
        assert (connection.host, connection.port) == ('db.example.com', 5432)

    def test_typed_again_it_goes(self, client, connection):
        assert client.post(f'/connections/{connection.id}/edit', data=connection_form(
            host='db2.example.com', password='new-password')).status_code == 302
        db.session.expire_all()
        assert (connection.host, connection.password) == ('db2.example.com', 'new-password')

    def test_the_hint_says_what_to_do(self, client, connection):
        assert '(saved: leave empty to keep it)' in client.get(
            f'/connections/{connection.id}/edit').get_data(as_text=True)
        html = client.post(f'/connections/{connection.id}/edit',
                           data=connection_form(password='typed-password', port='x')).get_data(as_text=True)
        assert 'typed-password' not in html and '(type it again)' in html
