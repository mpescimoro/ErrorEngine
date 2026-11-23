"""
Models: the identity of an error, its data, reminders, key fields.
"""
import hashlib
from datetime import timedelta

import pytest

from models import MonitoredQuery, ErrorRecord
from utils import get_utc_now


def test_the_hash_is_the_identity_of_an_error():
    """Only the key fields count, their names in any case, a missing one as empty. The formula never changes: a
    different hash would make every open error look new after an upgrade."""
    def h(data):
        return ErrorRecord.calculate_hash(data, ['ID', 'CODE'])
    assert h({'ID': '001', 'CODE': 'ERR001'}) == hashlib.sha256(b'001|ERR001').hexdigest()
    assert h({'id': '001', 'code': 'ERR001', 'MESSAGE': 'B'}) == h({'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'A'})
    assert h({'ID': '001'}) == h({'ID': '001', 'CODE': ''})
    assert h({'ID': '002', 'CODE': 'ERR001'}) != h({'ID': '001', 'CODE': 'ERR001'})


def test_the_data_of_an_error_come_back_as_saved():
    error = ErrorRecord(error_hash='h')
    assert error.get_error_data() == {}
    data = {'ID': '001', 'NUM': 42, 'NAME': 'Pescimoro à è ì ò ù', 'DESC': '日本語'}
    error.set_error_data(data)
    assert error.get_error_data() == data


@pytest.mark.parametrize('changes, due', [
    ({}, True),
    ({'sent_minutes_ago': 10}, False),        # the interval is not over
    ({'reminder_enabled': False}, False),
    ({'email_sent': False}, False),           # the first notification has not gone out
    ({'resolved': True}, False),
    ({'reminder_count': 5}, False),           # the limit is reached
])
def test_a_reminder_is_due(changes, due):
    query = MonitoredQuery(reminder_enabled=changes.get('reminder_enabled', True), reminder_interval_minutes=60,
                           reminder_max_count=5)
    error = ErrorRecord(email_sent=changes.get('email_sent', True),
                        email_sent_at=get_utc_now() - timedelta(minutes=changes.get('sent_minutes_ago', 120)),
                        reminder_count=changes.get('reminder_count', 0),
                        resolved_at=get_utc_now() if changes.get('resolved') else None)
    assert error.needs_reminder(query) is due


def test_key_fields_without_spaces_and_empty_names():
    assert MonitoredQuery(key_fields='  ID  , CODE,STATUS ,').get_key_fields_list() == ['ID', 'CODE', 'STATUS']
