"""
Statistics: what the database can count, it counts, and the errors are never read one by one, which with some
months of errors took seconds. The numbers stay the same: the average time to resolve, the errors of each
local day, days of 23 and 25 hours included.
"""
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event

from models import db, ErrorRecord
from tests.helpers import make_query
from utils import get_utc_now, local_to_utc

COUNT = {'n': 0}


def error(query, first_seen, resolved=None):
    COUNT['n'] += 1
    record = ErrorRecord(query_id=query.id, error_hash=f"h{COUNT['n']}", first_seen_at=first_seen,
                         last_seen_at=first_seen, resolved_at=resolved)
    record.set_error_data({'ID': COUNT['n']})
    db.session.add(record)
    db.session.commit()


@contextmanager
def statements():
    """The SQL sent to ErrorEngine's database meanwhile."""
    seen = []

    def before(connection, cursor, statement, parameters, context, executemany):
        seen.append(' '.join(statement.split()))
    event.listen(db.engine, 'before_cursor_execute', before)
    try:
        yield seen
    finally:
        event.remove(db.engine, 'before_cursor_execute', before)


def fixed_clock(local_moment, zone='Europe/Rome'):
    """utils.datetime with "now" at that local time."""
    from zoneinfo import ZoneInfo
    moment = local_moment.replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    return patch('utils.datetime', Clock)


def test_average_time_to_resolve(app, client):
    orders, stock, empty = make_query('Orders'), make_query('Stock'), make_query('Empty')
    now = get_utc_now()
    error(orders, now - timedelta(hours=5), now - timedelta(hours=3))     # 2 hours
    error(orders, now - timedelta(hours=10), now - timedelta(hours=6))    # 4 hours
    error(orders, now - timedelta(days=40), now - timedelta(days=31))     # resolved over a month ago: not counted
    error(orders, now - timedelta(hours=1))                               # still active: not counted
    error(stock, now - timedelta(hours=31), now - timedelta(hours=1))     # 30 hours

    def average(url):
        return client.get(url).get_json()['avg_resolution_hours']
    assert average(f'/api/stats/overview?query_id={orders.id}') == 3.0
    assert average(f'/api/stats/overview?query_id={stock.id}') == 30.0
    assert average('/api/stats/overview') == 12.0
    assert average(f'/api/stats/overview?query_id={empty.id}') == 0


def test_the_errors_are_not_read_one_by_one(app, client):
    """Every read of the errors is a count, an average or a maximum: a row per answer, per query or per quarter
    of an hour, never one per error."""
    query = make_query('Orders')
    now = get_utc_now()
    error(query, now - timedelta(hours=2), now - timedelta(hours=1))
    error(query, now - timedelta(hours=1))
    with statements() as seen:
        for url in ('/api/stats/overview?days=30', f'/api/stats/overview?days=7&query_id={query.id}',
                    '/api/stats/timeline?days=30', f'/api/stats/timeline?days=7&query_id={query.id}',
                    f'/api/stats/by-query?query_id={query.id}&days=30', '/api/stats/all-queries?days=30'):
            assert client.get(url).status_code == 200, url
    reads = [statement for statement in seen if 'error_records' in statement]
    assert reads
    for statement in reads:
        assert re.search(r'\b(count|avg|max)\(', statement.split(' FROM ')[0]), statement


def test_days_of_23_and_25_hours(app, client):
    """The days the clock changes still count from local midnight to local midnight."""
    app.config['TIMEZONE'] = 'Europe/Rome'
    query = make_query('Orders')
    for days in (((2026, 3, 28), (2026, 3, 29), (2026, 3, 30)), ((2026, 10, 24), (2026, 10, 25), (2026, 10, 26))):
        for day in days:
            for hour, minute in ((0, 1), (23, 59)):   # the first and the last minute of each day
                error(query, local_to_utc(datetime(*day, hour, minute)))
        expected = ['%04d-%02d-%02d' % day for day in days]
        with fixed_clock(datetime(*days[-1], 23, 59, 30)):
            timeline = client.get('/api/stats/timeline?days=3').get_json()
            daily = client.get(f'/api/stats/by-query?query_id={query.id}&days=3').get_json()['daily']
        assert [(day['date'], day['count']) for day in timeline] == [(date, 2) for date in expected]
        assert daily == [{'date': date, 'count': 2} for date in expected]
