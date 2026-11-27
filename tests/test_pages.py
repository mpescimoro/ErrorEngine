"""
The errors list and the run log in pages of 50: the page asked for, the newest first, the filters kept in the links
to the other pages, the totals of the whole list.
"""
import re
from datetime import timedelta

from models import db, ErrorRecord, QueryLog
from tests.helpers import make_query
from utils import get_utc_now


def add_errors(query, count, **fields):
    """count active errors, E-0 the newest."""
    now = get_utc_now()
    db.session.add_all(ErrorRecord(query_id=query.id, error_hash=f'{query.id}-{i}', error_data=f'{{"ID": "E-{i}"}}',
                                   first_seen_at=now - timedelta(minutes=i), **fields) for i in range(count))
    db.session.commit()


def add_logs(query, count):
    """count runs, R-0 the latest (in error_message, to find them on the page)."""
    now = get_utc_now()
    db.session.add_all(QueryLog(query_id=query.id, executed_at=now - timedelta(minutes=i), status='error',
                                error_message=f'R-{i}.') for i in range(count))
    db.session.commit()


def shown(html, prefix):
    """The numbers of the rows on the page, in their order."""
    return list(dict.fromkeys(int(n) for n in re.findall(rf'{prefix}-(\d+)', html)))


class TestErrors:

    def test_pages_of_fifty_the_newest_first(self, app, client):
        add_errors(make_query(), 120)
        first = client.get('/errors').get_data(as_text=True)
        assert shown(first, 'E') == list(range(50))
        assert 'Page 1 of 3' in first and 'badge-danger">120<' in first
        assert shown(client.get('/errors?page=3').get_data(as_text=True), 'E') == list(range(100, 120))

    def test_the_filters_stay_in_the_links(self, app, client):
        orders, stock = make_query('Orders'), make_query('Stock')
        add_errors(orders, 60, notify_pending=True)
        add_errors(stock, 5)
        page = client.get(f'/errors?query_id={orders.id}&delivery=pending').get_data(as_text=True)
        link = re.search(r'href="([^"]*page=2[^"]*)"', page).group(1).replace('&amp;', '&')
        assert f'query_id={orders.id}' in link and 'delivery=pending' in link
        assert 'Page 1 of 2' in page

    def test_a_page_past_the_end_shows_the_last_one(self, app, client):
        add_errors(make_query(), 60)
        assert shown(client.get('/errors?page=99').get_data(as_text=True), 'E') == list(range(50, 60))
        assert shown(client.get('/errors?page=0').get_data(as_text=True), 'E') == list(range(50))

    def test_one_page_has_no_links(self, app, client):
        add_errors(make_query(), 10)
        page = client.get('/errors').get_data(as_text=True)
        assert len(shown(page, 'E')) == 10 and 'Page 1 of' not in page


class TestLogs:

    def test_pages_of_fifty_the_latest_first(self, app, client):
        add_logs(make_query(), 130)
        first = client.get('/logs').get_data(as_text=True)
        assert shown(first, 'R') == list(range(50)) and 'Page 1 of 3' in first
        assert shown(client.get('/logs?page=3').get_data(as_text=True), 'R') == list(range(100, 130))

    def test_the_query_filter_stays_in_the_links(self, app, client):
        orders = make_query('Orders')
        add_logs(orders, 70)
        add_logs(make_query('Stock'), 70)
        page = client.get(f'/logs?query_id={orders.id}').get_data(as_text=True)
        assert 'Page 1 of 2' in page
        assert f'query_id={orders.id}' in re.search(r'href="([^"]*page=2[^"]*)"', page).group(1)
