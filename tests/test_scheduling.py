"""
Time windows: within a day, past midnight, the last slot of the window, the days of the week.
"""
import re
from datetime import datetime, time as dtime, timedelta

import pytest

from tests.helpers import make_query


class TestTimeWindows:
    FRIDAY = datetime(2026, 10, 30)          # a Friday

    def at(self, day_offset, hh, mm, ss=0):
        return self.FRIDAY + timedelta(days=day_offset, hours=hh, minutes=mm, seconds=ss)

    @pytest.mark.parametrize('start, end, days, day, hour, inside', [
        (None, None, '', 0, 10, True),                         # no window: always
        (dtime(8, 0), dtime(18, 0), '', 0, 12, True),
        (dtime(8, 0), dtime(18, 0), '', 0, 6, False),          # before the start
        (dtime(8, 0), dtime(18, 0), '', 0, 20, False),         # after the end
        (None, None, '1,2,3,4,5', 0, 12, True),                # Friday
        (None, None, '1,2,3,4,5', 1, 12, False),               # Saturday
    ])
    def test_window_within_a_day(self, app, start, end, days, day, hour, inside):
        q = make_query(schedule_start_time=start, schedule_end_time=end, schedule_days=days)
        assert q.is_in_schedule(self.at(day, hour, 0)) is inside

    def test_the_first_run_is_at_once_and_never_outside_the_window(self, app):
        q = make_query(schedule_start_time=dtime(8, 0), schedule_end_time=dtime(18, 0))
        assert q.should_run_now(self.at(0, 10, 0)) == (True, 'First run')
        assert q.should_run_now(self.at(0, 22, 0))[0] is False

    def test_window_past_midnight(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0))
        assert q.is_in_schedule(self.at(0, 23, 30))
        assert q.is_in_schedule(self.at(1, 6, 0, 20))    # the last slot, after midnight
        assert not q.is_in_schedule(self.at(1, 6, 1))
        assert not q.is_in_schedule(self.at(0, 12, 0))

    def test_night_belongs_to_the_day_it_starts(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0),
                       schedule_days='1,2,3,4,5')
        assert q.is_in_schedule(self.at(1, 2, 0))       # Saturday 02:00: Friday night
        assert not q.is_in_schedule(self.at(1, 23, 0))  # Saturday night
        assert not q.is_in_schedule(self.at(3, 2, 0))   # Monday 02:00: Sunday night

    def test_last_slot_of_the_window_runs(self, app):
        q = make_query(schedule_start_time=dtime(8, 0), schedule_end_time=dtime(18, 0),
                       check_interval_minutes=60)
        assert q.is_in_schedule(self.at(0, 18, 0, 20))
        assert not q.is_in_schedule(self.at(0, 18, 1))
        q.last_check_at = self.at(0, 17, 0, 30) - timedelta(hours=1)   # UTC, Rome is +1
        with app.app_context():
            app.config['TIMEZONE'] = 'Europe/Rome'
            should, reason = q.should_run_now(self.at(0, 18, 0, 20))
        assert should, reason

    def test_next_run_of_a_night_window(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0),
                       check_interval_minutes=60)
        assert q.get_next_run_time(self.at(0, 12, 0)) == self.at(0, 22, 0)

    def test_no_days_means_every_day(self, app):
        assert make_query(schedule_days='').get_schedule_days_list() == [1, 2, 3, 4, 5, 6, 7]

    def test_form_warns_when_no_day_is_selected(self, app, client):
        q = make_query(schedule_days='')
        html = client.get(f'/queries/{q.id}/edit').get_data(as_text=True)
        assert 'id="noDaysModal"' in html and 'scheduleCheck.noDays()' in html
        # An empty value saved earlier shows every day, so it is not taken for "no days"
        assert re.search(r'name="schedule_days" id="schedule_days"\s+value="1,2,3,4,5,6,7"', html)
