"""Thin DB-API layer: sqlite3 for `sqlite:` URLs, psycopg (imported only when needed) for `postgres:` URLs.

SQL is written with `?` placeholders; for Postgres they become `%s`.
"""
import os
import sqlite3

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

TABLES = (
    "account", "session", "project", "member", "token", "event", "task",
    "plan", "blocker", "decision", "delivery", "issue", "audit",
)


def parse_url(url):
    """Return ('sqlite', path) or ('postgres', url). Raise ValueError for anything else."""
    if not url:
        raise ValueError("DATABASE_URL is not set")
    if url.startswith("sqlite:"):
        rest = url[len("sqlite:"):]
        if rest.startswith("///"):
            rest = rest[3:]
        elif rest.startswith("//"):
            rest = rest[2:]
        if not rest:
            raise ValueError("sqlite URL has no path: %r" % url)
        return "sqlite", rest
    if url.startswith(("postgres://", "postgresql://")):
        return "postgres", url
    raise ValueError("unsupported DATABASE_URL scheme (use sqlite: or postgres:)")


def convert_placeholders(sql):
    """`?` → `%s` outside quoted literals, and `%` → `%%` so psycopg reads it literally."""
    out = []
    quote = None
    for ch in sql:
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "?":
            out.append("%s")
            continue
        out.append("%%" if ch == "%" else ch)
    return "".join(out)


def split_script(script):
    """Split an SQL script into statements, dropping `--` comments and blank statements."""
    lines = []
    for line in script.splitlines():
        cut = line.find("--")
        lines.append(line if cut < 0 else line[:cut])
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


class Database:
    """One connection. Rows come back as dicts."""

    def __init__(self, conn, kind):
        self.conn = conn
        self.kind = kind

    def _sql(self, sql):
        return convert_placeholders(sql) if self.kind == "postgres" else sql

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def query(self, sql, params=()):
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def query_one(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def executescript(self, script):
        for statement in split_script(script):
            self.execute(statement)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def connect(url):
    kind, target = parse_url(url)
    if kind == "sqlite":
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return Database(conn, kind)
    import psycopg  # only needed for Postgres deployments
    from psycopg.rows import dict_row
    return Database(psycopg.connect(target, row_factory=dict_row), kind)


def init_db(url):
    """Apply schema.sql (idempotent: every table is CREATE TABLE IF NOT EXISTS)."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        script = f.read()
    db = connect(url)
    try:
        db.executescript(script)
        db.commit()
    finally:
        db.close()


def get_db():
    """The request's connection, opened on first use and closed at teardown."""
    from flask import current_app, g
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_URL"])
    return g.db


def close_db(_exc=None):
    from flask import g
    db = g.pop("db", None)
    if db is not None:
        db.close()
