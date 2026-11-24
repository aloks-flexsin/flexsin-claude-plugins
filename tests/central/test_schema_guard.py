"""Central service: code deployed before `python -m central init-db` must not crash pages. A query that needs a table
or column the database lacks answers 503 with the command to run (HTML for pages, JSON for the API); any other database
error still raises."""
import sqlite3

import pytest

from central import db

from .test_repo_url import conn, world  # noqa: F401  (fixtures)


def test_schema_missing_recognises_both_databases():
    assert db.schema_missing(sqlite3.OperationalError('no such table: runner'))
    assert db.schema_missing(sqlite3.OperationalError('no such column: r.state'))
    assert db.schema_missing(sqlite3.OperationalError('table account has no column named name'))
    assert not db.schema_missing(sqlite3.OperationalError('database is locked'))
    assert not db.schema_missing(ValueError('no such table: x'))

    class PgError(Exception):
        sqlstate = '42P01'
    assert db.schema_missing(PgError()) and not db.schema_missing(type('E', (Exception,), {'sqlstate': '23505'})())


def test_a_missing_table_gives_the_update_page_not_a_crash(world, conn):
    conn.execute('DROP TABLE runner')
    conn.commit()
    resp = world['clients']['owner'].get('/my')
    page = resp.get_data(as_text=True)
    assert resp.status_code == 503 and 'python -m central init-db' in page and 'needs a database update' in page
    assert world['clients']['owner'].get('/projects/alpha/settings/members').status_code == 200   # other pages still work


def test_the_api_answers_json(app, world, conn):
    conn.execute('DROP TABLE runner')
    conn.commit()
    from .test_access import new_client
    c = new_client(app)
    resp = c.get('/api/v1/project?project=alpha', headers={'Authorization': 'Bearer art_' + 'x' * 43})
    assert resp.status_code in (401, 403)          # auth fails before any runner query: unaffected
    with app.test_request_context('/api/v1/runners/beat'):
        handler = app.error_handler_spec[None][None][sqlite3.OperationalError]
        out = handler(sqlite3.OperationalError('no such table: runner'))
        assert out.status_code == 503 and 'init-db' in out.get_json()['error']


def test_other_database_errors_still_raise(app):
    with app.test_request_context('/my'):
        handler = app.error_handler_spec[None][None][sqlite3.OperationalError]
        with pytest.raises(sqlite3.OperationalError):
            handler(sqlite3.OperationalError('database is locked'))
