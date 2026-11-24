"""Thin DB-API layer: sqlite3 for `sqlite:` URLs, psycopg (imported only when needed) for `postgres:` URLs.

SQL is written with `?` placeholders; for Postgres they become `%s`.
"""
import os
import sqlite3

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

TABLES = (
    "account", "session", "project", "member", "token", "event", "task",
    "plan", "blocker", "decision", "delivery", "issue", "audit", "invite", "device_login", "personal_token",
    "project_task", "claude_identity", "runner",
)

# Columns added to a table after it first shipped: (table, column, type). init_db adds each one that is missing
# (nullable, so existing rows stay valid). Keep schema.sql's CREATE TABLE in step.
COLUMNS_ADDED = (
    ("project", "repo_url", "VARCHAR(500)"),
    ("project", "repo_key", "VARCHAR(500)"),
    ("event", "account_id", "VARCHAR(64)"),
    ("account", "name", "VARCHAR(100)"),
    ("device_login", "runner_user", "VARCHAR(64)"),
    ("device_login", "claude_account", "VARCHAR(64)"),
    ("personal_token", "runner_user", "VARCHAR(64)"),
    ("personal_token", "claude_account", "VARCHAR(64)"),
)
REPO_KEY_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS project_repo_key ON project (repo_key)"


class DuplicateRepositories(RuntimeError):
    """Two or more projects name the same repository: a person decides which one stays (init_db changes nothing then)."""


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

    def columns(self, table):
        """The column names of `table` (lower case), read from an empty result so it works on SQLite and Postgres."""
        cur = self.execute("SELECT * FROM %s WHERE 1 = 0" % table)
        return {col[0].lower() for col in cur.description}

    def executescript(self, script):
        for statement in split_script(script):
            self.execute(statement)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


MISSING_SQLSTATES = ("42P01", "42703")       # Postgres: undefined table, undefined column


def schema_missing(exc):
    """True when `exc` says a table or column the code uses is not in the database, which means the code was deployed
    before `python -m central init-db` brought the database up to date."""
    if getattr(exc, "sqlstate", None) in MISSING_SQLSTATES:
        return True
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        return text.startswith(("no such table", "no such column")) or "has no column named" in text
    return False


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


def add_missing_columns(db):
    """ALTER TABLE ... ADD COLUMN for each COLUMNS_ADDED entry the database lacks; return what was added."""
    added = []
    for table, column, kind in COLUMNS_ADDED:
        if column not in db.columns(table):
            db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))
            added.append("%s.%s" % (table, column))
    return added


IDENTITY_DDL = """CREATE TABLE %s (
    id VARCHAR(64) PRIMARY KEY,
    account_id VARCHAR(64) NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    username VARCHAR(64) NOT NULL,
    claude_account VARCHAR(64) NOT NULL,
    surface VARCHAR(32) NOT NULL,
    first_seen VARCHAR(32) NOT NULL,
    last_seen VARCHAR(32) NOT NULL,
    UNIQUE (account_id, username, claude_account, surface)
)"""


def add_identity_surface(db):
    """Rebuild a `claude_identity` table made before it had `surface` (its unique key changes, so ALTER cannot do it):
    create the new table under a temporary name, copy every row with surface `auto-runner`, drop the old table and rename
    the new one. Return True when it rebuilt, False when the table already has `surface`. The caller commits."""
    if "surface" in db.columns("claude_identity"):
        return False
    if db.kind == "sqlite" and not db.conn.in_transaction:
        db.execute("BEGIN")                          # sqlite3 does not open a transaction for DDL by itself
    db.execute("DROP TABLE IF EXISTS claude_identity_new")
    db.execute(IDENTITY_DDL % "claude_identity_new")
    db.execute("INSERT INTO claude_identity_new (id, account_id, username, claude_account, surface, first_seen, last_seen)"
               " SELECT id, account_id, username, claude_account, 'auto-runner', first_seen, last_seen"
               " FROM claude_identity")
    db.execute("DROP TABLE claude_identity")
    db.execute("ALTER TABLE claude_identity_new RENAME TO claude_identity")
    return True


def _repo_keys(db):
    """-> [(project id, slug, stored repo_key or None, key computed from repo_url or None)]."""
    from . import projects as _projects                          # the one place that turns a URL into a key
    cols = db.columns("project")
    extra = [c for c in ("repo_url", "repo_key") if c in cols]
    rows = db.query("SELECT %s FROM project" % ", ".join(["id", "slug"] + extra))
    return [(r["id"], r["slug"], r["repo_key"] if "repo_key" in cols else None,
             _projects.repo_key(r["repo_url"]) if "repo_url" in cols else None) for r in rows]


def duplicate_repositories(db):
    """-> {repo_key: [slugs]} for repositories that more than one project names (empty when there are none)."""
    owners = {}
    for _, slug, stored, computed in _repo_keys(db):
        if stored or computed:
            owners.setdefault(stored or computed, []).append(slug)
    return {k: sorted(v) for k, v in owners.items() if len(v) > 1}


def fill_repo_keys(db):
    """Set `project.repo_key` from `repo_url` where it is missing."""
    for project_id, _, stored, computed in _repo_keys(db):
        if computed and not stored:
            db.execute("UPDATE project SET repo_key = ? WHERE id = ?", (computed, project_id))


def init_db(url):
    """Apply schema.sql (idempotent: every table is CREATE TABLE IF NOT EXISTS), add each COLUMNS_ADDED column an older
    database lacks, fill `project.repo_key` and make it unique (one project per repository). Return the columns added
    ("table.column"). Raise DuplicateRepositories, before adding any column, key or index, when two projects name the
    same repository."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        script = f.read()
    db = connect(url)
    try:
        db.executescript(script)                    # only creates what is missing; existing rows are untouched
        add_identity_surface(db)                     # not in COLUMNS_ADDED: the unique key changes too
        duplicates = duplicate_repositories(db)      # checked before any column, key or index is added
        if duplicates:
            raise DuplicateRepositories(duplicates)
        added = add_missing_columns(db)
        fill_repo_keys(db)
        db.execute(REPO_KEY_INDEX)
        db.commit()
    finally:
        db.close()
    return added


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
