"""
EMAIL_MAX_ROWS: a notification email lists at most that many errors, says how many more there are and
attaches them all as CSV; the subject, the count and the email log keep the total. The plain-text part
lists the same rows as the HTML.
"""
import base64
import email
import re
from unittest.mock import patch

import pytest

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


def sent_by_smtp(query, rows, email_type='new_errors'):
    """The email as the SMTP server gets it: (message, plain text, CSV attached or None, its file name)."""
    from email_service import email_service
    with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
        assert email_service.send_error_notification(query, rows, ['ID', 'MSG'], email_type=email_type)['success']
    message = email.message_from_string(smtp_ssl.return_value.sendmail.call_args.args[2])
    parts = {part.get_content_type(): part for part in message.walk()}
    text = parts['text/plain'].get_payload(decode=True).decode('utf-8')
    attached = parts.get('text/csv')
    return message, text, attached and attached.get_payload(decode=True), attached and attached.get_filename()


class TestAttachment:

    def test_past_the_limit_every_row_in_a_csv(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        message, text, data, name = sent_by_smtp(make_query(email_recipients='ops@example.com'), errors(10))
        assert message.get_content_type() == 'multipart/mixed' and name == 'Orders.csv'
        assert data.startswith(b'\xef\xbb\xbf')   # UTF-8, as Excel reads it
        lines = data.decode('utf-8-sig').splitlines()
        assert lines[0] == 'ID,MSG' and lines[1:] == [f'E-{i},Order stuck' for i in range(10)]
        assert '+ 7 more not listed here: all 10 are in the attached file Orders.csv' in text

    def test_up_to_the_limit_no_file(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        message, _, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), errors(3))
        assert message.get_content_type() == 'multipart/alternative' and data is None

    def test_reminders_too(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        _, text, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), errors(10), 'reminder')
        assert text.startswith('[REMINDER]') and len(data.decode('utf-8-sig').splitlines()) == 11

    def test_the_separator_of_the_settings(self, app):
        app.config.update(EMAIL_MAX_ROWS=1, CSV_SEPARATOR=';')
        rows = [{'ID': 'E-1', 'MSG': 'Qty 1,5; late'}, {'ID': 'E-2', 'MSG': 'Città'}]
        _, _, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), rows)
        assert data.decode('utf-8-sig').splitlines() == ['ID;MSG', 'E-1;"Qty 1,5; late"', 'E-2;Città']

    def test_a_text_that_starts_like_a_formula_is_not_run(self, app):
        app.config['EMAIL_MAX_ROWS'] = 1
        rows = [{'ID': value, 'MSG': None} for value in ('=HYPERLINK("http://x.example")', '+39 02 1234', '@SUM(A1)',
                                                         '-5', '-2.5', -7, 'A=B')]
        _, _, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), [{'ID': 'E-0'}] + rows)
        assert data.decode('utf-8-sig').splitlines()[2:] == [
            '"\'=HYPERLINK(""http://x.example"")",', "'+39 02 1234,", "'@SUM(A1),", '-5,', '-2.5,', '-7,', 'A=B,']

    def test_with_semicolons_the_decimals_get_the_comma(self, app):
        app.config.update(EMAIL_MAX_ROWS=1, CSV_SEPARATOR=';')
        rows = [{'ID': 'E-0', 'MSG': value}
                for value in ('1.500', '0.5', '-2.25', 12.345, 1500, '1.2.3', 'v1.5', '1e-07', 'Qty 1.5')]
        _, _, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), rows)
        assert [line.split(';', 1)[1] for line in data.decode('utf-8-sig').splitlines()[1:]] == [
            '1,500', '0,5', '-2,25', '12,345', '1500', '1.2.3', 'v1.5', '1e-07', 'Qty 1.5']

    def test_a_column_name_that_starts_like_a_formula_is_not_run(self):
        from email_service import errors_csv
        data = errors_csv([{'=HYPERLINK("x")': 1}], ['=HYPERLINK("x")'], ',')
        assert data.decode('utf-8-sig').splitlines() == ['"\'=HYPERLINK(""x"")"', '1']

    def test_too_large_for_an_attachment(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        with patch('email_service.CSV_MAX_BYTES', 100):
            message, text, data, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), errors(10))
        assert message.get_content_type() == 'multipart/alternative' and data is None
        assert '+ 7 more not listed here, too many for an attachment: the dashboard shows them all.' in text
        other = make_query(name='Other', email_recipients='ops@example.com')
        _, _, html = notify(other, errors(10))
        assert 'all 10 are in the attached file Other.csv' in html
        with patch('email_service.CSV_MAX_BYTES', 100):
            _, _, html = notify(other, errors(10))
        assert 'too many for an attachment' in html and 'attached file' not in html

    @pytest.mark.parametrize('query_name, file_name', [('Orders', 'Orders.csv'), ('Late > 2h', 'Late-2h.csv'),
                                                       ('Città/€', 'Citta.csv'), ('***', 'errors.csv')])
    def test_the_file_is_named_after_the_query(self, query_name, file_name):
        from email_service import attachment_name
        assert attachment_name(query_name) == file_name

    def test_through_microsoft_365(self, app):
        from email_service import email_service, graph_mailer
        app.config.update(EMAIL_MAX_ROWS=3, M365_TENANT_ID='t', M365_CLIENT_ID='c', M365_CLIENT_SECRET='s')
        graph_mailer._token = None
        answers = iter([{'access_token': 'token', 'expires_in': 3600}, None])

        def post(url, **kwargs):
            body = next(answers)
            return type('Answer', (), {'status_code': 200 if body else 202, 'json': lambda self: body or {}})()
        with patch('email_service.requests.post', side_effect=post) as sent:
            assert email_service.send_error_notification(make_query(email_recipients='ops@example.com'),
                                                         errors(10), ['ID', 'MSG'])['success']
        graph_mailer._token = None
        attachment, = sent.call_args.kwargs['json']['message']['attachments']
        assert attachment['name'] == 'Orders.csv' and attachment['contentType'] == 'text/csv'
        assert len(base64.b64decode(attachment['contentBytes']).decode('utf-8-sig').splitlines()) == 11


class TestPlainText:

    def test_the_rows_of_the_html(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        rows = [{'ID': 'E-0', 'MSG': 'Order stuck'}, {'ID': 'E-1', 'MSG': None}]
        _, text, _, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), rows)
        assert 'Query: Orders' in text and 'New errors: 2' in text
        assert 'ID: E-0 | MSG: Order stuck\nID: E-1 | MSG: —\n' in text and 'not listed' not in text

    def test_the_first_ones_past_the_limit(self, app):
        app.config['EMAIL_MAX_ROWS'] = 3
        _, text, _, _ = sent_by_smtp(make_query(email_recipients='ops@example.com'), errors(10))
        assert re.findall(r'^ID: E-(\d+) \| MSG: Order stuck$', text, re.M) == ['0', '1', '2']


def test_csv_separator_from_the_settings(monkeypatch, caplog):
    from config import _csv_separator
    for value, expected in (('', ','), (' ; ', ';'), (',', ',')):
        monkeypatch.setenv('CSV_SEPARATOR', value)
        assert _csv_separator() == expected
    monkeypatch.setenv('CSV_SEPARATOR', 'tab')
    with pytest.raises(SystemExit):
        _csv_separator()
    assert 'CSV_SEPARATOR=tab in .env is not , or ;' in caplog.text
