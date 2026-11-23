"""
Cleanup: by retention every night, or everything by hand; the active errors are never deleted.

The fixtures hold a recent and an old record of each kind (run log of 60 days, email of 120, error resolved 100
days ago) and 2 active errors; the tests run with 1 day of retention (config.TestingConfig).
"""
from cleanup_service import cleanup_service
from models import ErrorRecord, QueryLog, EmailLog

KEPT_ERRORS = ['hash_active_001', 'hash_active_002', 'hash_resolved_recent']


def deleted(results):
    return results['query_logs_deleted'], results['email_logs_deleted'], results['resolved_errors_deleted']


def test_the_retention_deletes_only_the_older_records(app, sample_errors_in_db, sample_logs_in_db):
    results = cleanup_service.run_full_cleanup()
    assert deleted(results) == (1, 1, 1) and 'executed_at' in results
    assert [log.rows_returned for log in QueryLog.query.all()] == [5]
    assert [email.subject for email in EmailLog.query.all()] == ['Test']
    assert sorted(e.error_hash for e in ErrorRecord.query.all()) == KEPT_ERRORS


def test_by_hand_every_log_and_resolved_error_goes_but_not_the_active_ones(app, sample_errors_in_db,
                                                                          sample_logs_in_db):
    assert deleted(cleanup_service.run_manual_cleanup()) == (2, 2, 2)
    assert QueryLog.query.count() == EmailLog.query.count() == 0
    assert ErrorRecord.query.filter_by(resolved_at=None).count() == 2


def test_the_counts(app, sample_errors_in_db, sample_logs_in_db):
    stats = cleanup_service.get_stats()
    assert stats['counts'] == {'query_logs': 2, 'email_logs': 2, 'total_errors': 4, 'active_errors': 2,
                               'resolved_errors': 2}
    assert set(stats['retention_config']) == {'log_retention_days', 'email_log_retention_days',
                                              'resolved_errors_retention_days'}


def test_the_counts_of_an_empty_database(app):
    stats = cleanup_service.get_stats()
    assert stats['counts']['query_logs'] == stats['counts']['total_errors'] == 0
    assert stats['oldest_records']['query_log'] is None
