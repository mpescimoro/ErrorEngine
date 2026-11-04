"""
Tests for the validators module.
"""
from validators import (
    validate_email, validate_email_list, validate_routing_rule, sanitize_string
)


class TestValidateEmail:
    """Tests for validate_email."""
    
    def test_valid_email(self):
        is_valid, error = validate_email('test@example.com')
        assert is_valid is True
        assert error == ''
    
    def test_valid_email_with_subdomain(self):
        is_valid, error = validate_email('user@mail.example.com')
        assert is_valid is True
    
    def test_empty_email(self):
        is_valid, error = validate_email('')
        assert is_valid is False
        assert 'empty' in error.lower()
    
    def test_invalid_format(self):
        is_valid, error = validate_email('not-an-email')
        assert is_valid is False
        assert 'format' in error.lower()
    
    def test_email_too_long(self):
        long_email = 'a' * 250 + '@example.com'
        is_valid, error = validate_email(long_email)
        assert is_valid is False
        assert 'long' in error.lower()


class TestValidateEmailList:
    """Tests for validate_email_list."""
    
    def test_single_email(self):
        is_valid, error, emails = validate_email_list('test@example.com')
        assert is_valid is True
        assert emails == ['test@example.com']
    
    def test_multiple_emails(self):
        is_valid, error, emails = validate_email_list('a@test.com, b@test.com, c@test.com')
        assert is_valid is True
        assert len(emails) == 3
    
    def test_empty_string(self):
        is_valid, error, emails = validate_email_list('')
        assert is_valid is True
        assert emails == []
    
    def test_one_invalid(self):
        is_valid, error, emails = validate_email_list('valid@test.com, invalid')
        assert is_valid is False


class TestValidateRoutingRule:
    """Tests for validate_routing_rule."""
    
    def test_valid_rule(self):
        data = {
            'name': 'Test Rule',
            'recipients': 'test@example.com',
            'condition_logic': 'AND',
            'priority': 0,
            'conditions': [
                {'field_name': 'STATUS', 'operator': 'equals', 'value': 'ERROR'}
            ]
        }
        is_valid, error = validate_routing_rule(data)
        assert is_valid is True
    
    def test_missing_recipients(self):
        data = {
            'name': 'Test Rule',
            'recipients': '',
            'condition_logic': 'AND'
        }
        is_valid, error = validate_routing_rule(data)
        assert is_valid is False
    
    def test_invalid_logic(self):
        data = {
            'recipients': 'test@example.com',
            'condition_logic': 'INVALID'
        }
        is_valid, error = validate_routing_rule(data)
        assert is_valid is False
    
    def test_invalid_priority(self):
        data = {
            'recipients': 'test@example.com',
            'priority': 'not a number'
        }
        is_valid, error = validate_routing_rule(data)
        assert is_valid is False

    def test_invalid_regex_is_refused(self):
        ok, message = validate_routing_rule({'recipients': 'a@x.it', 'conditions': [
            {'field_name': 'CODE', 'operator': 'regex', 'value': '([a-z'}]})
        assert not ok and 'regular expression' in message


class TestSanitizeString:
    """Tests for sanitize_string."""
    
    def test_normal_string(self):
        result = sanitize_string('Hello World')
        assert result == 'Hello World'
    
    def test_trim_whitespace(self):
        result = sanitize_string('  Hello  ')
        assert result == 'Hello'
    
    def test_max_length(self):
        result = sanitize_string('a' * 1000, max_length=100)
        assert len(result) == 100
    
    def test_empty_string(self):
        result = sanitize_string('')
        assert result == ''
    
    def test_none(self):
        result = sanitize_string(None)
        assert result == ''

    def test_control_characters_removed(self):
        assert sanitize_string('a\x00b\x07c\td\n') == 'abc\td'
