import os
import re
import sqlite3
import sys

import pytest

import central
from central import config, db


def _tables(url):
    conn = db.connect(url)
    try:
        rows = conn.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    finally:
        conn.close()
    return {r['name'] for r in rows}


def test_schema_applies_twice_and_creates_all_tables(db_url):
    db.init_db(db_url)
    db.init_db(db_url)
    assert len(db.TABLES) == 13
    assert set(db.TABLES) <= _tables(db_url)


def test_row_with_missing_foreign_key_is_refused(db_url):
    db.init_db(db_url)
    conn = db.connect(db_url)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO member (project_id, account_id, role, created_at) VALUES (?, ?, ?, ?)",
                         ('no-project', 'no-account', 'viewer', '2026-01-01T00:00:00Z'))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO session (id_hash, account_id, csrf_token, created_at, last_seen_at) "
                         "VALUES (?, ?, ?, ?, ?)", ('h', 'missing', 'c', 't', 't'))
    finally:
        conn.close()


def test_valid_rows_are_accepted(db_url):
    db.init_db(db_url)
    conn = db.connect(db_url)
    conn.execute("INSERT INTO project (id, slug, name, created_at) VALUES (?, ?, ?, ?)", ('p1', 'demo', 'Demo', 't'))
    conn.execute("INSERT INTO account (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                 ('a1', 'a@example.com', 'x', 't'))
    conn.execute("INSERT INTO member (project_id, account_id, role, created_at) VALUES (?, ?, ?, ?)",
                 ('p1', 'a1', 'owner', 't'))
    conn.commit()
    assert conn.query_one("SELECT member.role, account.disabled FROM member JOIN account ON account.id = member.account_id") == \
        {'role': 'owner', 'disabled': 0}
    conn.close()


def test_schema_is_portable_sql():
    with open(db.SCHEMA_PATH, encoding='utf-8') as f:
        text = f.read()
    upper = text.upper()
    for sqlite_only in ('AUTOINCREMENT', 'PRAGMA', 'WITHOUT ROWID', 'INSERT OR ', 'STRICT'):
        assert sqlite_only not in upper
    statements = db.split_script(text)
    assert len(statements) == 13
    for statement in statements:
        assert statement.upper().startswith('CREATE TABLE IF NOT EXISTS ')
    created = [re.match(r'CREATE TABLE IF NOT EXISTS (\w+)', s, re.I).group(1) for s in statements]
    assert sorted(created) == sorted(db.TABLES)
    # Every foreign key is spelled out with REFERENCES.
    assert upper.count('REFERENCES') >= 15


@pytest.mark.parametrize('sql, expected', [
    ("SELECT * FROM t WHERE a = ? AND b = ?", "SELECT * FROM t WHERE a = %s AND b = %s"),
    ("SELECT '?' FROM t WHERE a = ?", "SELECT '?' FROM t WHERE a = %s"),
    ("SELECT * FROM t WHERE a LIKE 'x%' AND b = ?", "SELECT * FROM t WHERE a LIKE 'x%%' AND b = %s"),
    ("SELECT 'it''s?' FROM t WHERE a = ?", "SELECT 'it''s?' FROM t WHERE a = %s"),
    ("SELECT 1", "SELECT 1"),
])
def test_placeholder_conversion(sql, expected):
    assert db.convert_placeholders(sql) == expected


def test_postgres_database_converts_placeholders():
    calls = []

    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))

    class Conn:
        def cursor(self):
            return Cursor()

    db.Database(Conn(), 'postgres').execute("SELECT * FROM t WHERE a = ?", ['x'])
    db.Database(Conn(), 'sqlite').execute("SELECT * FROM t WHERE a = ?", ['x'])
    assert calls == [("SELECT * FROM t WHERE a = %s", ('x',)), ("SELECT * FROM t WHERE a = ?", ('x',))]


@pytest.mark.parametrize('url, expected', [
    ('sqlite:///central.db', ('sqlite', 'central.db')),
    ('sqlite:////tmp/central.db', ('sqlite', '/tmp/central.db')),
    ('sqlite:///C:/data/central.db', ('sqlite', 'C:/data/central.db')),
    ('postgres://u@h/db', ('postgres', 'postgres://u@h/db')),
    ('postgresql://u@h/db', ('postgres', 'postgresql://u@h/db')),
])
def test_parse_url(url, expected):
    assert db.parse_url(url) == expected


@pytest.mark.parametrize('url', ['', None, 'mysql://h/db', 'sqlite:'])
def test_parse_url_refuses_others(url):
    with pytest.raises(ValueError):
        db.parse_url(url)


def test_sqlite_path_does_not_import_psycopg(db_url):
    sys.modules.pop('psycopg', None)
    db.init_db(db_url)
    assert 'psycopg' not in sys.modules


def test_refuses_to_start_without_secret_key(monkeypatch, db_url):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(config.ConfigError):
        central.create_app({'DATABASE_URL': db_url})
    monkeypatch.setenv('SECRET_KEY', '')
    with pytest.raises(config.ConfigError):
        central.create_app()


def test_config_comes_from_env(monkeypatch, db_url):
    monkeypatch.setenv('SECRET_KEY', 'k' * 40)
    monkeypatch.setenv('DATABASE_URL', db_url)
    app = central.create_app()
    assert app.config['SECRET_KEY'] == 'k' * 40
    assert app.config['DATABASE_URL'] == db_url
    assert app.config['TESTING'] is False


def test_testing_gets_a_random_secret_key(monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    a = config.load({'TESTING': True}, environ={})
    b = config.load({'TESTING': True}, environ={})
    assert a['SECRET_KEY'] and a['SECRET_KEY'] != b['SECRET_KEY']


def test_healthz_and_security_headers(client):
    resp = client.get('/healthz')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok'}
    csp = resp.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'
    assert resp.headers['Referrer-Policy'] == 'no-referrer'


def test_security_headers_on_error_responses(client):
    resp = client.get('/no-such-page')
    assert resp.status_code == 404
    assert "frame-ancestors 'none'" in resp.headers['Content-Security-Policy']
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'


def test_request_db_connection(app):
    with app.app_context():
        conn = db.get_db()
        assert conn is db.get_db()
        assert conn.query("SELECT COUNT(*) AS n FROM account") == [{'n': 0}]


def test_requirements_files():
    here = os.path.dirname(__file__)
    root = os.path.abspath(os.path.join(here, '..', '..'))
    with open(os.path.join(root, 'auto-runner', 'central', 'requirements.txt'), encoding='utf-8') as f:
        reqs = f.read().split()
    assert 'flask' in reqs and 'psycopg[binary]' in reqs
    with open(os.path.join(root, 'requirements-dev.txt'), encoding='utf-8') as f:
        assert 'flask' in f.read().split()
