"""
Passwords and tokens saved in the internal database, encrypted with ERRORENGINE_ENCRYPTION_KEY (Fernet,
from cryptography). The key is in the configuration of the instance, with its other secrets: the database
can then be kept apart from the instance, and a copy of it does not reveal them without the key.

Every text of the saved settings is encrypted, except the fields listed as not secret (those the forms
show): a field nobody listed ends up encrypted, not in clear.

The setting can hold several keys separated by commas: the first encrypts, all of them decrypt, and at
every start what another key encrypted is encrypted again with the first (encrypt_saved_secrets). To change
the key: the new one first and the old one after it, start ErrorEngine, then remove the old one.
"""
import json
import logging
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)

# In front of every encrypted value: a value without it was saved in clear, before 2.5
PREFIX = 'enc:v1:'
# Next to the database file while it may still hold old values in clear: the rebuild is tried at every start
REBUILD_MARKER = '-rebuild'


class UnreadableSecret(ValueError):
    """A saved value that none of the keys decrypts: it was encrypted with another key."""


def unreadable(what: str, where: str) -> UnreadableSecret:
    """The error for a secret that cannot be read, saying where it is and what to do."""
    return UnreadableSecret(f"{what} saved in {where} cannot be read: it was encrypted with a key that is not "
                            f"in ERRORENGINE_ENCRYPTION_KEY. Put that key back in the setting, or enter it again "
                            f"in {where}")


def generate_command() -> str:
    """
    The command that generates a key, with the Python that runs ErrorEngine. From the folder of ErrorEngine
    when the Python is in it (venv): so written, cmd, PowerShell and bash all run it.
    """
    python = sys.executable
    try:
        relative = os.path.relpath(python)
        if not relative.startswith('..'):
            python = relative if os.sep in relative else os.path.join('.', relative)
    except ValueError:   # on another drive (Windows)
        pass
    code = '-c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    if ' ' in python:
        return f'"{python}" {code} (in PowerShell with & in front)'
    return f'{python} {code}'


def read_keys(text) -> list:
    """The keys of the setting, the one that encrypts first; ValueError with what is wrong."""
    keys = [key.strip() for key in (text or '').split(',') if key.strip()]
    if not keys:
        raise ValueError('ERRORENGINE_ENCRYPTION_KEY is missing: it encrypts the passwords and tokens '
                         'saved in the database')
    fernets = []
    for number, key in enumerate(keys, 1):
        try:
            fernets.append(Fernet(key))
        except ValueError:
            which = f'key {number} of ' if len(keys) > 1 else ''
            raise ValueError(f'{which}ERRORENGINE_ENCRYPTION_KEY is not a valid key') from None
    return fernets


def init_app(app):
    """Without a valid key ErrorEngine does not start, and the log says how to generate one."""
    try:
        app.extensions['encryption'] = read_keys(app.config.get('ENCRYPTION_KEY'))
    except ValueError as e:
        logger.critical(f"{e}. If the database holds passwords saved with a key, set that key; otherwise generate "
                        f"one, in the folder of ErrorEngine, with {generate_command()}. Put it in .env and keep "
                        "a copy of it apart: without it the passwords saved in the database cannot be read")
        raise SystemExit(1)


def _keys() -> list:
    return current_app.extensions['encryption']


def encrypt(value):
    """
    The value to save, encrypted with the first key. Empty stays empty, nothing to hide; a value already
    encrypted stays as it is: that is how a form keeps a saved secret without decrypting it.
    """
    if value is None or value == '' or (isinstance(value, str) and value.startswith(PREFIX)):
        return value
    return PREFIX + _keys()[0].encrypt(str(value).encode('utf-8')).decode('ascii')


def decrypt(value):
    """The saved value in clear, whichever key of the setting encrypted it; one saved before 2.5 as it is."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    token = value[len(PREFIX):].encode('utf-8')
    for key in _keys():
        try:
            return key.decrypt(token).decode('utf-8')
        except InvalidToken:
            continue
    raise UnreadableSecret('encrypted with a key that is not in ERRORENGINE_ENCRYPTION_KEY')


def each_text(value, change):
    """The value with change applied to each text in it, at any depth: of a JSON object the values, not the names."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, dict):
        return {name: each_text(item, change) for name, item in value.items()}
    if isinstance(value, list):
        return [each_text(item, change) for item in value]
    return value   # numbers and true/false keep their type


def encrypt_fields(config: dict, clear) -> dict:
    """A copy of a JSON setting with every text encrypted, except the fields in clear."""
    return {field: value if field in clear else each_text(value, encrypt) for field, value in (config or {}).items()}


def decrypt_fields(config: dict, clear) -> dict:
    """A copy of a JSON setting with every text in clear (UnreadableSecret if one cannot be read)."""
    return {field: value if field in clear else each_text(value, decrypt) for field, value in (config or {}).items()}


# --- At every start ---

def _state(value) -> str:
    """'clear' (saved before 2.5), 'current' (the first key), 'older' (another key), 'unreadable', '' if empty."""
    if value is None or value == '':
        return ''
    if not value.startswith(PREFIX):
        return 'clear'
    token = value[len(PREFIX):].encode('utf-8')
    for number, key in enumerate(_keys()):
        try:
            key.decrypt(token)
            return 'current' if number == 0 else 'older'
        except InvalidToken:
            continue
    return 'unreadable'


def _updated(text, found: dict, where: str):
    """The text as it is to be saved, with the first key; counted in found under its state."""
    state = _state(text)
    if state in ('clear', 'older'):
        found[state] += 1
        return encrypt(decrypt(text))
    if state == 'unreadable' and where not in found['unreadable']:
        found['unreadable'].append(where)
    return text


def _database_file():
    """The file of the internal database; None when it is not a SQLite file."""
    from models import db
    url = db.engine.url
    if url.get_backend_name() != 'sqlite' or url.database in (None, '', ':memory:'):
        return None
    return os.path.abspath(url.database)


def _rebuild(database: str) -> bool:
    """
    VACUUM, then the WAL written back: the old values stay in the free space of the file and in the WAL
    otherwise. Once done the marker goes; if it fails, it stays and the next start tries again.
    """
    from models import db
    try:
        with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql('VACUUM')
            connection.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception as e:
        # The WAL grows by the size of the database, and SQLite writes a temporary copy of it
        megabytes = 2 * os.path.getsize(database) // 2**20 + 1
        logger.warning(f"The database file may still hold old passwords in clear: rebuilding it failed ({e}). "
                       f"Free about {megabytes} MB, half on its disk and half in the temporary folder of the system: "
                       "ErrorEngine tries again at every start")
        return False
    try:
        os.remove(database + REBUILD_MARKER)
    except FileNotFoundError:
        pass
    return True


def encrypt_saved_secrets() -> dict:
    """
    At every start, in one transaction: the texts saved in clear (before 2.5) encrypted with the first key,
    those of another key encrypted again with it. Then the database file rebuilt without the old values.
    Returns {'clear': n, 'older': n, 'unreadable': [where], 'saved': bool, 'rebuilt': bool}.
    """
    from models import db, DatabaseConnection, MonitoredQuery, NotificationChannel, SOURCE_CLEAR, CHANNEL_CLEAR
    found = {'clear': 0, 'older': 0, 'unreadable': [], 'saved': False, 'rebuilt': False}
    changes = []
    for connection in DatabaseConnection.query.order_by(DatabaseConnection.id):
        value = _updated(connection._password, found, f'the connection {connection.name}')
        if value != connection._password:
            changes.append((connection, '_password', value))
    for objects, column, clear, kind in ((MonitoredQuery, 'source_config', SOURCE_CLEAR, 'query'),
                                         (NotificationChannel, 'config', CHANNEL_CLEAR, 'channel')):
        for item in objects.query.order_by(objects.id):
            text = getattr(item, column)
            try:
                stored = json.loads(text) if text else {}
            except ValueError:
                logger.warning(f"The settings of the {kind} {item.name} are not valid JSON: left as they are")
                continue
            if not isinstance(stored, dict):
                continue
            where = f'the {kind} {item.name}'
            config = {field: value if field in clear else each_text(value, lambda t: _updated(t, found, where))
                      for field, value in stored.items()}
            if config != stored:
                changes.append((item, column, json.dumps(config, ensure_ascii=False)))

    database = _database_file()
    marker = database + REBUILD_MARKER if database else None
    if changes:
        if marker:
            # Before the commit: stopped right after it, the next start still rebuilds the file
            try:
                with open(marker, 'w', encoding='utf-8') as file:
                    file.write('ErrorEngine rebuilds the database file at its next start: until then it may still '
                               'hold old passwords in clear\n')
            except OSError as e:
                logger.warning(f"Cannot write {marker} ({e}): if rebuilding the database file fails, the next "
                               "start does not try again")
        try:
            for item, column, value in changes:
                setattr(item, column, value)
            db.session.commit()
            found['saved'] = True
        except Exception:
            db.session.rollback()
            if marker and os.path.exists(marker):
                os.remove(marker)
            logger.exception("Passwords and tokens: the database was left as it was, nothing encrypted")
            return found
    if marker and (found['saved'] or os.path.exists(marker)):
        found['rebuilt'] = _rebuild(database)
    _log(found, database)
    return found


def _log(found: dict, database):
    from models import db
    if found['clear']:
        logger.info(f"Passwords and tokens saved in clear: {found['clear']} encrypted with ERRORENGINE_ENCRYPTION_KEY")
        if database is None and db.engine.url.get_backend_name() != 'sqlite':
            logger.warning("The internal database is not SQLite: the values in clear may stay in its files until "
                           "its own maintenance removes them")
    if found['older']:
        logger.info(f"Passwords and tokens of an older key: {found['older']} encrypted again with the first key of "
                    "ERRORENGINE_ENCRYPTION_KEY" + ('' if found['unreadable'] else ': the other keys can be removed'))
    if found['rebuilt']:
        logger.info("Database file rebuilt: the old values are no longer in it")
    if found['unreadable']:
        logger.warning(f"Passwords and tokens that ERRORENGINE_ENCRYPTION_KEY cannot read, in "
                       f"{', '.join(found['unreadable'])}: they were saved with another key. Put that key back in the "
                       "setting, after the current one and a comma, or enter them again in their forms")
