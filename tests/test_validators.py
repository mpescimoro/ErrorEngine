"""
Test per il modulo validators.
"""
import pytest
from validators import (
    validate_email, validate_email_list, validate_query_name,
    validate_key_fields, validate_sql_query, validate_interval,
    validate_routing_rule, validate_url, sanitize_string
)


class TestValidateEmail:
    """Test per validate_email."""
    
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
        assert 'vuota' in error.lower()
    
    def test_invalid_format(self):
        is_valid, error = validate_email('not-an-email')
        assert is_valid is False
        assert 'formato' in error.lower()
    
    def test_email_too_long(self):
        long_email = 'a' * 250 + '@example.com'
        is_valid, error = validate_email(long_email)
        assert is_valid is False
        assert 'lunga' in error.lower()


class TestValidateEmailList:
    """Test per validate_email_list."""
    
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


class TestValidateQueryName:
    """Test per validate_query_name."""
    
    def test_valid_name(self):
        is_valid, error = validate_query_name('Test Query 01')
        assert is_valid is True
    
    def test_name_with_underscore(self):
        is_valid, error = validate_query_name('query_name_test')
        assert is_valid is True
    
    def test_empty_name(self):
        is_valid, error = validate_query_name('')
        assert is_valid is False
    
    def test_too_short(self):
        is_valid, error = validate_query_name('ab')
        assert is_valid is False
        assert 'corto' in error.lower()
    
    def test_too_long(self):
        is_valid, error = validate_query_name('a' * 101)
        assert is_valid is False
        assert 'lungo' in error.lower()


class TestValidateKeyFields:
    """Test per validate_key_fields."""
    
    def test_single_field(self):
        is_valid, error, fields = validate_key_fields('ID')
        assert is_valid is True
        assert fields == ['ID']
    
    def test_multiple_fields(self):
        is_valid, error, fields = validate_key_fields('ID, CODE, STATUS')
        assert is_valid is True
        assert len(fields) == 3
    
    def test_empty(self):
        is_valid, error, fields = validate_key_fields('')
        assert is_valid is False
    
    def test_invalid_field_name(self):
        is_valid, error, fields = validate_key_fields('123invalid')
        assert is_valid is False


class TestValidateSqlQuery:
    """Test per validate_sql_query."""
    
    def test_valid_select(self):
        is_valid, error = validate_sql_query('SELECT * FROM USERS')
        assert is_valid is True
    
    def test_select_with_where(self):
        is_valid, error = validate_sql_query("SELECT id, name FROM users WHERE status = 'active'")
        assert is_valid is True
    
    def test_empty_query(self):
        is_valid, error = validate_sql_query('')
        assert is_valid is False
    
    def test_not_select(self):
        is_valid, error = validate_sql_query('UPDATE users SET status = 1')
        assert is_valid is False
        assert 'SELECT' in error
    
    def test_dangerous_pattern_drop(self):
        is_valid, error = validate_sql_query('SELECT * FROM users; DROP TABLE users')
        assert is_valid is False
    
    def test_dangerous_pattern_delete(self):
        is_valid, error = validate_sql_query('SELECT * FROM users; DELETE FROM users')
        assert is_valid is False


class TestValidateInterval:
    """Test per validate_interval."""
    
    def test_valid_interval(self):
        is_valid, error = validate_interval(15)
        assert is_valid is True
    
    def test_minimum(self):
        is_valid, error = validate_interval(1)
        assert is_valid is True
    
    def test_maximum(self):
        is_valid, error = validate_interval(1440)
        assert is_valid is True
    
    def test_below_minimum(self):
        is_valid, error = validate_interval(0)
        assert is_valid is False
    
    def test_above_maximum(self):
        is_valid, error = validate_interval(1441)
        assert is_valid is False
    
    def test_not_integer(self):
        is_valid, error = validate_interval('not a number')
        assert is_valid is False


class TestValidateRoutingRule:
    """Test per validate_routing_rule."""
    
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


class TestValidateUrl:
    """Test per validate_url."""
    
    def test_valid_https(self):
        is_valid, error = validate_url('https://example.com/api')
        assert is_valid is True
    
    def test_valid_http(self):
        is_valid, error = validate_url('http://example.com')
        assert is_valid is True
    
    def test_no_protocol(self):
        is_valid, error = validate_url('example.com')
        assert is_valid is False
    
    def test_empty(self):
        is_valid, error = validate_url('')
        assert is_valid is False


class TestSanitizeString:
    """Test per sanitize_string."""
    
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
