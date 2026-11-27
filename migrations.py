"""
Numbered migrations of ErrorEngine's database: the changes that ensure_schema and ensure_indexes cannot make,
as those only add what is missing. Each runs once, in order, at the start, in one transaction with the record
that it ran (the schema_migrations table): a migration that fails leaves the database as it was, and
ErrorEngine does not start. A new database runs them too, on its empty tables.

A migration is written once and then never changed: it works with SQL and the rules of its time, not with the
models, which keep changing. A new one takes the next number, at the end of MIGRATIONS.
"""
import hashlib
import json
import logging

from sqlalchemy import bindparam, text, DateTime

from utils import get_utc_now

logger = logging.getLogger(__name__)


def _001_active_errors_unique(session):
    """
    One active error per identity. Two checks of the same query at the same time, when a long delivery outlived
    the lock, could save the same error twice, and the copy then stayed active for ever, as a check keeps
    one error per identity. The copies are resolved, the oldest error stays, and an index refuses new ones.
    """
    session.execute(
        text("UPDATE error_records SET resolved_at = :now, notify_pending = 0 "
             "WHERE resolved_at IS NULL AND id NOT IN "
             "(SELECT min(id) FROM error_records WHERE resolved_at IS NULL GROUP BY query_id, error_hash)")
        .bindparams(bindparam('now', type_=DateTime)),
        {'now': get_utc_now()})
    session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_error_records_active_identity "
                         "ON error_records (query_id, error_hash) WHERE resolved_at IS NULL"))


def _identity_v2(data: dict, key_fields: list) -> str:
    """ErrorRecord.calculate_hash as migration 2 wrote it: the key values as a JSON list, NULL apart from ''."""
    values = []
    for field in key_fields:
        value = next((v for k, v in data.items() if k.upper() == field.upper()), None)
        values.append(None if value is None else str(value))
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _002_error_identity_v2(session):
    """
    The identity of the errors: the key values joined with | made ('a|b', 'c') and ('a', 'b|c')
    one error, and a NULL the same as an empty value. The open errors get the new hash, computed from their
    saved data, which is their row as the last check returned it: the next check finds them again, and nothing
    is notified again. The resolved ones keep the old hash, as nothing compares it any more.

    Open errors of a query whose key fields changed after they were saved can share the new identity: the
    oldest stays, the others are resolved, as one active error per identity (migration 1).
    """
    key_fields = {query_id: [name.strip() for name in (fields or '').split(',') if name.strip()]
                  for query_id, fields in session.execute(text('SELECT id, key_fields FROM monitored_queries'))}
    kept, copies = {}, []
    for error_id, query_id, data in session.execute(
            text('SELECT id, query_id, error_data FROM error_records WHERE resolved_at IS NULL ORDER BY id')):
        identity = (query_id, _identity_v2(json.loads(data) if data else {}, key_fields.get(query_id, [])))
        if identity in kept:
            copies.append({'id': error_id, 'now': get_utc_now()})
        else:
            kept[identity] = error_id
    if copies:
        session.execute(text('UPDATE error_records SET resolved_at = :now, notify_pending = 0 WHERE id = :id')
                        .bindparams(bindparam('now', type_=DateTime)), copies)
    if kept:
        session.execute(text('UPDATE error_records SET error_hash = :hash WHERE id = :id'),
                        [{'id': error_id, 'hash': identity[1]} for identity, error_id in kept.items()])


# (number, name, function of the session): in order, the last one at the end
MIGRATIONS = [
    (1, 'active errors unique', _001_active_errors_unique),
    (2, 'error identity v2', _002_error_identity_v2),
]


def run_migrations() -> list:
    """Runs the migrations not yet applied, in order of number; returns their names ('001 active errors unique')."""
    from models import db, SchemaMigration

    applied = {number for (number,) in db.session.query(SchemaMigration.number)}
    done = []
    for number, name, migrate in sorted(MIGRATIONS, key=lambda migration: migration[0]):
        if number in applied:
            continue
        try:
            migrate(db.session)
            db.session.add(SchemaMigration(number=number, name=name, applied_at=get_utc_now()))
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.error(f"Migration {number:03d} {name} of the database failed: the database is as it was")
            raise
        done.append(f'{number:03d} {name}')
    return done
