"""
Validators: email addresses, routing rules, text from the forms.
"""
import pytest

from validators import validate_email, validate_email_list, validate_routing_rule, sanitize_string


@pytest.mark.parametrize('address, problem', [
    ('test@example.com', ''), ('user@mail.example.com', ''),
    ('', 'empty'), ('not-an-email', 'format'), ('a' * 250 + '@example.com', 'long'),
])
def test_email_address(address, problem):
    valid, message = validate_email(address)
    if problem:
        assert not valid and problem in message.lower()
    else:
        assert valid and message == ''


def test_email_lists():
    for text, addresses in (('test@example.com', ['test@example.com']), ('', []),
                            ('a@test.com, b@test.com, c@test.com', ['a@test.com', 'b@test.com', 'c@test.com'])):
        valid, _, found = validate_email_list(text)
        assert valid and found == addresses, text
    assert validate_email_list('valid@test.com, invalid')[0] is False


def test_a_valid_rule():
    ok, _ = validate_routing_rule({'name': 'Test Rule', 'recipients': 'test@example.com', 'condition_logic': 'AND',
                                   'priority': 0,
                                   'conditions': [{'field_name': 'STATUS', 'operator': 'equals', 'value': 'ERROR'}]})
    assert ok


@pytest.mark.parametrize('rule, says', [
    ({'name': 'Test Rule', 'recipients': '', 'condition_logic': 'AND'}, ''),
    ({'recipients': 'test@example.com', 'condition_logic': 'INVALID'}, ''),
    ({'recipients': 'test@example.com', 'priority': 'not a number'}, ''),
    ({'recipients': 'a@x.it', 'conditions': [{'field_name': 'CODE', 'operator': 'regex', 'value': '([a-z'}]},
     'regular expression'),
    # Saved, an unknown operator would never match
    ({'recipients': 'a@x.it', 'conditions': [{'field_name': 'CODE', 'operator': 'equal', 'value': 'X'}]},
     "unknown operator 'equal'"),
    (['a@x.it'], ''), ({'recipients': ['a@x.it']}, ''), ({'recipients': 'a@x.it', 'conditions': 'CODE'}, ''),
    ({'recipients': 'a@x.it', 'conditions': ['CODE']}, ''),
])
def test_rules_that_cannot_be_saved(rule, says):
    ok, message = validate_routing_rule(rule)
    assert not ok and message and says in message


@pytest.mark.parametrize('text, clean', [
    ('Hello World', 'Hello World'), ('  Hello  ', 'Hello'), ('', ''), (None, ''), ('a\x00b\x07c\td\n', 'abc\td'),
])
def test_text_from_the_forms(text, clean):
    assert sanitize_string(text) == clean


def test_text_cut_to_the_maximum_length():
    assert sanitize_string('a' * 1000, max_length=100) == 'a' * 100
