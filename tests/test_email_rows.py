"""
EMAIL_MAX_ROWS: a notification email lists at most that many errors and says how many more there are;
the subject, the count and the email log keep the total.
"""
import re
from unittest.mock import patch

from models import EmailLog
from tests.helpers import make_query


def errors(count):
    return [{'ID': f'E-{i}', 'MSG': 'Order stuck'} for i in range(count)]


def notify(query, rows, email_type='new_errors'):
    """Sends the notification without a mail server; returns (result, subject, HTML)."""
    from email_service import email_service
    with patch.object(email_service, '_deliver', return_value={}) as deliver:
        result = email_service.send_error_notification(query, rows, ['ID', 'MSG'], email_type=email_type)
    _, subject, html, _ = deliver.call_args.args
    return result, subject, html


def listed(html):
    """The numbers of the errors in the email, in order."""
    return [int(n) for n in re.findall(r'\bE-(\d+)\b', html)]


def test_the_email_lists_the_first_rows_and_says_how_many_more(app):
    app.config['EMAIL_MAX_ROWS'] = 3
    query = make_query(email_recipients='ops@example.com', email_subject='{error_count} errors in {query_name}')
    result, subject, html = notify(query, errors(10))
    assert result['success'] is True
    assert listed(html) == [0, 1, 2]
    assert '7 more not listed here' in html
    assert subject == '10 errors in Orders'
    assert EmailLog.query.one().error_count == 10


def test_up_to_the_limit_every_row_and_nothing_more(app):
    app.config['EMAIL_MAX_ROWS'] = 3
    _, _, html = notify(make_query(email_recipients='ops@example.com'), errors(3))
    assert listed(html) == [0, 1, 2] and 'not listed' not in html


def test_zero_lists_every_row(app):
    app.config['EMAIL_MAX_ROWS'] = 0
    _, _, html = notify(make_query(email_recipients='ops@example.com'), errors(10))
    assert listed(html) == list(range(10)) and 'not listed' not in html


def test_reminders_too(app):
    app.config['EMAIL_MAX_ROWS'] = 3
    _, subject, html = notify(make_query(email_recipients='ops@example.com'), errors(10), email_type='reminder')
    assert subject.startswith('[REMINDER]')
    assert listed(html) == [0, 1, 2] and '7 more not listed here' in html


def test_custom_templates_see_how_many_are_not_listed(app):
    app.config['EMAIL_MAX_ROWS'] = 3
    query = make_query(email_recipients='ops@example.com',
                       email_template='<p>{{ error_count }}|{{ errors|length }}|{{ errors_not_listed }}</p>')
    _, _, html = notify(query, errors(10))
    assert '<p>10|3|7</p>' in html


def test_by_default_a_hundred_rows_in_an_email_that_mail_programs_do_not_cut(app):
    assert app.config['EMAIL_MAX_ROWS'] == 100
    _, _, html = notify(make_query(email_recipients='ops@example.com'), errors(2000))
    assert listed(html) == list(range(100)) and '1900 more not listed here' in html
    assert len(html.encode()) < 102 * 1024   # Gmail cuts the longer ones
