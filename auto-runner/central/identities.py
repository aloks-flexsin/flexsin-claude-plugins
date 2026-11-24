"""Claude identities: the (username, Claude account) pairs a signed-in person's runners report."""
import re

from . import audit

# Own copy of the runner name pattern (autosafe.RUNNER_NAME); a test keeps the two equal.
NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')


def valid(value):
    return (isinstance(value, str) and bool(NAME.fullmatch(value)) and '..' not in value
            and not value.endswith(('.', '.lock')))


def record(db, account_id, username, claude_account, moment):
    """Upsert one pair for the account; return its row id, or None when a value is empty or invalid. The audit row
    `identity_seen` is written only the first time. The caller commits."""
    if not account_id or not valid(username) or not valid(claude_account):
        return None
    row = db.query_one("SELECT id FROM claude_identity WHERE account_id = ? AND username = ? AND claude_account = ?",
                       (account_id, username, claude_account))
    if row:
        db.execute("UPDATE claude_identity SET last_seen = ? WHERE id = ?", (moment, row["id"]))
        return row["id"]
    identity_id = audit.new_id()
    db.execute("INSERT INTO claude_identity (id, account_id, username, claude_account, first_seen, last_seen)"
               " VALUES (?, ?, ?, ?, ?, ?)", (identity_id, account_id, username, claude_account, moment, moment))
    audit.record(db, "identity_seen", moment, account_id=account_id,
                 detail={"username": username, "claude_account": claude_account})
    return identity_id


def of_account(db, account_id):
    return db.query("SELECT * FROM claude_identity WHERE account_id = ? ORDER BY username, claude_account",
                    (account_id,))


def of_project(db, project_id):
    """The identities of the project's members (each row carries its account_id)."""
    return db.query("SELECT i.* FROM claude_identity i JOIN member m ON m.account_id = i.account_id"
                    " WHERE m.project_id = ? ORDER BY i.username, i.claude_account", (project_id,))
