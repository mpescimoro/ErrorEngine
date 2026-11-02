"""
Tests for the automatic page refresh (active errors summary)
and for the filter of the Errors page.
"""
import re
import pytest
from models import db, MonitoredQuery, ErrorRecord
from utils import get_utc_now


def add_query(name):
    query = MonitoredQuery(name=name, sql_query='SELECT 1', key_fields='ID')
    db.session.add(query)
    db.session.commit()
    return query


def add_error(query, n, resolved=False):
    error = ErrorRecord(query_id=query.id, error_hash=f'{query.id}-{n}')
    error.set_error_data({'ID': n})
    if resolved:
        error.resolved_at = get_utc_now()
    db.session.add(error)
    db.session.commit()
    return error


def summary(client):
    response = client.get('/api/errors/summary')
    assert response.status_code == 200
    return response.get_json()


class TestErrorSummary:
    def test_no_errors(self, client):
        assert summary(client)['active'] == 0

    def test_counts_only_active_ones(self, client):
        query = add_query('Q')
        add_error(query, 1)
        add_error(query, 2)
        add_error(query, 3, resolved=True)
        assert summary(client)['active'] == 2

    def test_signature_changes_with_a_new_error(self, client):
        query = add_query('Q')
        add_error(query, 1)
        before = summary(client)['signature']
        add_error(query, 2)
        assert summary(client)['signature'] != before

    def test_signature_changes_when_an_error_is_resolved(self, client):
        query = add_query('Q')
        error = add_error(query, 1)
        add_error(query, 2)
        before = summary(client)['signature']
        error.resolved_at = get_utc_now()
        db.session.commit()
        assert summary(client)['signature'] != before

    def test_signature_unchanged_when_an_error_is_just_seen_again(self, client):
        """Every check updates the counters of known errors: that must not reload the page."""
        query = add_query('Q')
        error = add_error(query, 1)
        before = summary(client)['signature']
        error.occurrence_count += 1
        error.last_seen_at = get_utc_now()
        db.session.commit()
        assert summary(client)['signature'] == before


class TestPageAttributes:
    def body_tag(self, client, path):
        return re.search(r'<body[^>]*>', client.get(path).get_data(as_text=True)).group(0)

    @pytest.mark.parametrize('path', ['/', '/errors'])
    def test_pages_that_reload(self, client, path):
        tag = self.body_tag(client, path)
        assert 'data-live-reload="true"' in tag
        assert f'data-errors-signature="{summary(client)["signature"]}"' in tag

    def test_other_pages_only_counter(self, client):
        tag = self.body_tag(client, '/settings')
        assert 'data-errors-signature=' in tag
        assert 'data-live-reload' not in tag

    def test_counter_in_the_menu(self, client):
        html = client.get('/settings').get_data(as_text=True)
        assert 'id="navErrorCount" style="display: none;">0<' in html
        add_error(add_query('Q'), 1)
        html = client.get('/settings').get_data(as_text=True)
        assert 'id="navErrorCount">1<' in html


class TestErrorsPageFilter:
    def options(self, client, qs=''):
        html = client.get('/errors' + qs).get_data(as_text=True)
        select = re.search(r'<select name="query_id".*?</select>', html, re.S)
        if not select:
            return None, html
        return [' '.join(t.split()) for t in re.findall(r'<option[^>]*>(.*?)</option>', select.group(0), re.S)], html

    def test_no_errors_no_menu_and_message(self, client):
        add_query('Q')
        options, html = self.options(client)
        assert options is None
        assert 'bi-emoji-smile' in html
        assert 'There are no errors to resolve' in html

    def test_only_failing_queries_with_count(self, client):
        a, b, c = add_query('Alpha'), add_query('Beta'), add_query('Gamma')
        add_error(a, 1)
        add_error(a, 2)
        add_error(c, 1)
        add_error(b, 1, resolved=True)
        options, _ = self.options(client)
        assert options == ['All queries (3)', 'Alpha (2)', 'Gamma (1)']

    def test_selected_query_just_resolved(self, client):
        a, b = add_query('Alpha'), add_query('Beta')
        add_error(a, 1)
        add_error(b, 1, resolved=True)
        options, html = self.options(client, f'?query_id={b.id}')
        assert 'Beta (0)' in options
        assert 'View all errors (1)' in html


class TestRecipientsColumn:
    """On the Errors page: who received the notification, readable even with many recipients."""

    def row_cell(self, client, error_id):
        html = client.get('/errors').get_data(as_text=True)
        for row in re.findall(r'<tr>(.*?)</tr>', html, re.S):
            if f"errorManager.resolve({error_id})" in row:
                return re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)[2]
        raise AssertionError('row not found')

    def test_header(self, client):
        add_error(add_query('Q'), 1)
        assert '<th>Recipients</th>' in client.get('/errors').get_data(as_text=True)

    def test_few_recipients_all_visible(self, client):
        error = add_error(add_query('Q'), 1)
        error.add_notified_to(['user@x.it', 'it@x.it'])
        db.session.commit()
        cell = self.row_cell(client, error.id)
        assert 'user@x.it' in cell and 'it@x.it' in cell
        assert 'expandable-trigger' not in cell

    def test_many_recipients_first_three_and_the_rest_expandable(self, client):
        error = add_error(add_query('Q'), 1)
        error.add_notified_to([f'user{i}@x.it' for i in range(1, 6)])
        db.session.commit()
        cell = self.row_cell(client, error.id)
        visible, hidden = cell.split('expandable-content')
        assert re.findall(r'title="([^"]+)"', visible) == ['user1@x.it', 'user2@x.it', 'user3@x.it']
        assert re.findall(r'title="([^"]+)"', hidden) == ['user4@x.it', 'user5@x.it']
        assert '+2 more' in visible
        assert f'aria-controls="error-details-r{error.id}"' in visible

    def test_a_single_hidden_recipient(self, client):
        error = add_error(add_query('Q'), 1)
        error.add_notified_to([f'user{i}@x.it' for i in range(1, 5)])
        db.session.commit()
        assert '+1 more' in self.row_cell(client, error.id)

    def test_no_recipient_recorded(self, client):
        error = add_error(add_query('Q'), 1)
        cell = self.row_cell(client, error.id)
        assert '—' in cell
        assert 'No email address recorded' in cell
