"""
Numbered migrations of ErrorEngine's database: each runs once, in order, at the start, with the record that it
ran in the same transaction; a new database runs them too. The first one leaves one active error per identity.
"""
from unittest.mock import patch

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import migrations
from models import db, ErrorRecord, SchemaMigration
from tests.helpers import make_query


def applied():
    return [number for (number,) in db.session.query(SchemaMigration.number).order_by(SchemaMigration.number)]


def active_identity_index():
    return 'ix_error_records_active_identity' in {index['name'] for index in inspect(db.engine).get_indexes('error_records')}


def add_error(query, error_hash, resolved=False, data=None):
    error = ErrorRecord(query_id=query.id, error_hash=error_hash)
    error.set_error_data(data or {'ID': 1})
    if resolved:
        from utils import get_utc_now
        error.resolved_at = get_utc_now()
    db.session.add(error)
    db.session.commit()
    return error


def test_a_new_database_has_them_all(app):
    assert applied() == [number for number, _, _ in migrations.MIGRATIONS]
    assert active_identity_index()
    assert migrations.run_migrations() == []


def test_a_database_of_before_runs_them_once(app):
    """2.5.1: no record of migrations, no index, and the same active error twice from overlapping checks."""
    query = make_query()
    with db.engine.begin() as connection:
        connection.execute(text('DELETE FROM schema_migrations'))
        connection.execute(text('DROP INDEX ix_error_records_active_identity'))
    first, copy = add_error(query, 'same'), add_error(query, 'same')
    add_error(query, 'other', data={'ID': 2})
    assert migrations.run_migrations() == ['001 active errors unique', '002 error identity v2']
    db.session.expire_all()
    assert first.resolved_at is None and copy.resolved_at is not None and copy.notify_pending is False
    assert ErrorRecord.query.filter_by(resolved_at=None).count() == 2
    assert active_identity_index()
    assert applied() == [1, 2]
    assert migrations.run_migrations() == []


def test_in_order_and_all_or_nothing(app):
    query = make_query()
    ran = []

    def second(session):
        ran.append(2)

    def third(session):
        session.execute(text("UPDATE error_records SET error_hash = 'changed'"))
        raise ValueError('the database refused it')

    add_error(query, 'h')
    last = migrations.MIGRATIONS[-1][0]
    with patch.object(migrations, 'MIGRATIONS',
                      migrations.MIGRATIONS + [(last + 2, 'third', third), (last + 1, 'second', second)]):
        with pytest.raises(ValueError, match='refused'):
            migrations.run_migrations()
    db.session.expire_all()
    assert ran == [2] and applied()[-1] == last + 1               # the second ran and stays
    assert ErrorRecord.query.one().error_hash == 'h'              # the third left nothing behind


def test_one_active_error_per_identity(app):
    query = make_query()
    add_error(query, 'h', resolved=True)
    add_error(query, 'h')                   # resolved before, back now: a new error, allowed
    with pytest.raises(IntegrityError):
        add_error(query, 'h')
    db.session.rollback()
    add_error(make_query('Other'), 'h')     # the same identity in another query


def test_a_check_that_meets_another_one_stops_and_says_so(app):
    """Two checks of the same query at the same time (a lock taken over): the second one to save stops."""
    query = make_query(email_recipients='')
    done = []

    def the_other_check_saved_it_first(session, flush_context, instances):
        new = [item for item in session.new if isinstance(item, ErrorRecord)]
        if new and not done:
            done.append(True)
            session.add(ErrorRecord(query_id=new[0].query_id, error_hash=new[0].error_hash,
                                    error_data=new[0].error_data))
    event.listen(Session, 'before_flush', the_other_check_saved_it_first)
    try:
        with patch('monitor_service.execute_query_source', return_value=(['ID'], [{'ID': '1'}])):
            from monitor_service import monitor_service
            result = monitor_service.check_query(query, force=True)
    finally:
        event.remove(Session, 'before_flush', the_other_check_saved_it_first)
    assert result['status'] == 'error'
    assert result['error_message'].startswith('Another check of this query saved the same errors at the same time')
    assert 'INSERT' not in result['error_message']
    assert ErrorRecord.query.count() == 0 and query.locked_at is None
