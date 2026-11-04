"""
Time windows: past midnight, the last slot of the window, the days of the week.
"""
import re
from datetime import datetime, time as dtime, timedelta

from models import db, MonitoredQuery


def make_query(name='Orders', **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    query = MonitoredQuery(name=name, **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


class TestTimeWindows:
    FRIDAY = datetime(2026, 10, 30)          # a Friday

    def at(self, day_offset, hh, mm, ss=0):
        return self.FRIDAY + timedelta(days=day_offset, hours=hh, minutes=mm, seconds=ss)

    def test_window_past_midnight(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0))
        assert q.is_in_schedule(self.at(0, 23, 30))
        assert q.is_in_schedule(self.at(1, 2, 0))
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
