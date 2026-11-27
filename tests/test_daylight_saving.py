"""
The slots across the days and the nights the clock changes (Europe/Rome, 2026): up to an hour a query keeps its
rhythm in real time, without an hour of pause when the clock goes back and without runs in a row; longer intervals
keep their local times; after the last slot of a day the next run is the first of the next day. The scheduler is
run minute by minute, as it does, with the local time it gets from the clock.
"""
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

import pytest

from models import MonitoredQuery
from tests.helpers import make_query
from utils import utc_to_local


def runs(app, query, start, end):
    """The UTC minutes in which the query runs, from start to end (UTC)."""
    app.config['TIMEZONE'] = 'Europe/Rome'
    done, moment = [], start
    while moment < end:
        if query.should_run_now(utc_to_local(moment))[0]:
            done.append(moment)
            query.last_check_at = moment
        moment += timedelta(minutes=1)
    return done


def gaps(moments):
    return {int((b - a).total_seconds() // 60) for a, b in zip(moments, moments[1:])}


@pytest.mark.parametrize('interval', [5, 15, 60])
def test_the_night_the_clock_goes_back(app, interval):
    """25 October: 02:00-03:00 comes twice. It was 75 minutes without runs every 15, 2 hours every 60."""
    query = MonitoredQuery(name='Orders', check_interval_minutes=interval)
    done = runs(app, query, datetime(2026, 10, 24, 22, 0), datetime(2026, 10, 25, 4, 0))
    assert gaps(done) == {interval}
    assert len(done) == 6 * 60 // interval


@pytest.mark.parametrize('interval', [5, 15, 60])
def test_the_night_the_clock_goes_forward(app, interval):
    """29 March: 02:00-03:00 does not exist."""
    query = MonitoredQuery(name='Orders', check_interval_minutes=interval)
    done = runs(app, query, datetime(2026, 3, 28, 23, 0), datetime(2026, 3, 29, 3, 0))
    assert gaps(done) == {interval}


def test_a_daily_query_keeps_its_local_time(app):
    query = MonitoredQuery(name='Orders', check_interval_minutes=1440, schedule_reference_time=time(8, 0),
                           last_check_at=datetime(2026, 10, 23, 0, 0))
    done = runs(app, query, datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 28, 0, 0))
    assert [utc_to_local(t).strftime('%d %H:%M') for t in done] == [
        '23 08:00', '24 08:00', '25 08:00', '26 08:00', '27 08:00']


def test_a_daily_query_in_the_hours_that_change(app):
    """At 02:30: once the night the hour comes twice, the first time round; the night it does not exist, at 03:30."""
    query = MonitoredQuery(name='Orders', check_interval_minutes=1440, schedule_reference_time=time(2, 30),
                           last_check_at=datetime(2026, 10, 24, 6, 0))
    done = runs(app, query, datetime(2026, 10, 24, 6, 0), datetime(2026, 10, 26, 6, 0))
    assert [utc_to_local(t).strftime('%d %H:%M') for t in done] == ['25 02:30', '26 02:30']
    query.last_check_at = datetime(2026, 3, 28, 6, 0)
    done = runs(app, query, datetime(2026, 3, 28, 6, 0), datetime(2026, 3, 30, 6, 0))
    assert [utc_to_local(t).strftime('%d %H:%M') for t in done] == ['29 03:30', '30 02:30']


def test_the_next_run_in_the_hour_that_comes_twice(app):
    """After the run at 02:45 summer time, the next one is at 02:00 winter time, 15 minutes later."""
    app.config['TIMEZONE'] = 'Europe/Rome'
    query = MonitoredQuery(name='Orders', check_interval_minutes=15, last_check_at=datetime(2026, 10, 25, 0, 45))
    following = query.get_next_run_time(utc_to_local(datetime(2026, 10, 25, 0, 50)))
    assert following.strftime('%H:%M') == '02:00' and following.fold == 1


def test_every_six_hours_keeps_its_local_times(app):
    """As a daily query: from 00:00 to 06:00 there are 7 hours the night the clock goes back, 5 the other."""
    query = MonitoredQuery(name='Orders', check_interval_minutes=360, last_check_at=datetime(2026, 10, 24, 12, 0))
    done = runs(app, query, datetime(2026, 10, 24, 12, 0), datetime(2026, 10, 25, 12, 0))
    assert [utc_to_local(t).strftime('%d %H:%M') for t in done] == ['24 18:00', '25 00:00', '25 06:00', '25 12:00']
    query.last_check_at = datetime(2026, 3, 28, 12, 0)
    done = runs(app, query, datetime(2026, 3, 28, 12, 0), datetime(2026, 3, 29, 12, 0))
    assert [utc_to_local(t).strftime('%d %H:%M') for t in done] == ['28 18:00', '29 00:00', '29 06:00', '29 12:00']


def test_a_failing_source_in_the_hour_that_comes_twice(app):
    """Its wait ends at 02:55 summer time: at 02:00 winter time, 5 minutes later, it is over."""
    app.config['TIMEZONE'] = 'Europe/Rome'
    query = MonitoredQuery(name='Orders', check_interval_minutes=15, last_check_at=datetime(2026, 10, 25, 0, 45),
                           failure_retry_at=datetime(2026, 10, 25, 0, 55))
    following = query.get_next_run_time(utc_to_local(datetime(2026, 10, 25, 0, 50)))
    assert following.strftime('%H:%M') == '02:00' and following.fold == 1
    assert query.should_run_now(utc_to_local(datetime(2026, 10, 25, 1, 0))) == (True, 'Current slot not run yet')


def test_the_countdown_in_the_hour_that_comes_twice(app, client):
    """At 02:50 summer time the next check is at 02:00 winter time: in 10 minutes, not 0."""
    app.config['TIMEZONE'] = 'Europe/Rome'
    moment = datetime(2026, 10, 25, 0, 50, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    make_query(check_interval_minutes=15, last_check_at=datetime(2026, 10, 25, 0, 45))
    with patch('utils.datetime', Clock):
        assert client.get('/api/scheduler/next').get_json()['seconds_remaining'] == 600


@pytest.mark.parametrize('interval, last_slot', [(7 * 60, time(21, 0)), (50, time(23, 20))])
def test_the_next_run_after_the_last_slot_of_the_day(app, interval, last_slot):
    """Every 7 hours the slots are 00, 07, 14 and 21, every 50 minutes the last is 23:20: then 00:00, the first of
    the next day, and not 04:00 or 00:10."""
    app.config['TIMEZONE'] = 'UTC'
    friday = datetime(2026, 10, 30)
    query = MonitoredQuery(name='Orders', check_interval_minutes=interval,
                           last_check_at=datetime.combine(friday, last_slot))
    midnight = friday + timedelta(days=1)
    assert query.get_next_run_time(datetime.combine(friday, last_slot) + timedelta(minutes=5)) == midnight
    assert query.should_run_now(midnight)[0] is True
