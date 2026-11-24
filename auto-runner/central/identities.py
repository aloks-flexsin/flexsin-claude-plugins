"""Claude identities: the (username, Claude account) pairs a signed-in person's runners report."""
import re

from . import audit

# Own copy of the runner name pattern (autosafe.RUNNER_NAME); a test keeps the two equal.
NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')


RUNNER = re.compile(r"([a-z0-9._-]+)/([a-z0-9._-]+)-claude-auto-runner")


def split_runner(label):
    """'alok/bhawish-claude-auto-runner' -> ('alok', 'bhawish'); None for any other label."""
    m = RUNNER.fullmatch(label or "")
    return (m.group(1), m.group(2)) if m else None   # `record` still checks both values with `valid`


def valid(value):
    return (isinstance(value, str) and bool(NAME.fullmatch(value)) and '..' not in value
            and not value.endswith(('.', '.lock')))


SURFACES = ("auto-runner", "cli", "desktop", "browser")


def record(db, account_id, username, claude_account, moment, surface="auto-runner"):
    """Upsert one (username, Claude account, surface) for the account; return its row id, or None when a value is empty
    or invalid or the surface is unknown. The audit row `identity_seen` is written only the first time. The caller
    commits."""
    if not account_id or not valid(username) or not valid(claude_account) or surface not in SURFACES:
        return None
    row = db.query_one("SELECT id FROM claude_identity WHERE account_id = ? AND username = ? AND claude_account = ?"
                       " AND surface = ?", (account_id, username, claude_account, surface))
    if row:
        db.execute("UPDATE claude_identity SET last_seen = ? WHERE id = ?", (moment, row["id"]))
        return row["id"]
    identity_id = audit.new_id()
    db.execute("INSERT INTO claude_identity (id, account_id, username, claude_account, surface, first_seen, last_seen)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)",
               (identity_id, account_id, username, claude_account, surface, moment, moment))
    audit.record(db, "identity_seen", moment, account_id=account_id,
                 detail={"username": username, "claude_account": claude_account, "surface": surface})
    return identity_id


def of_account(db, account_id):
    return db.query("SELECT * FROM claude_identity WHERE account_id = ? ORDER BY username, claude_account, surface",
                    (account_id,))


def of_project(db, project_id):
    """The identities of the project's members (each row carries its account_id)."""
    return db.query("SELECT i.* FROM claude_identity i JOIN member m ON m.account_id = i.account_id"
                    " WHERE m.project_id = ? ORDER BY i.username, i.claude_account, i.surface", (project_id,))


def team_view(db, project_id):
    """The project's identities grouped for the Members page -> {by_account: {account_id: [{claude_account, username,
    shared}]}, shared: {claude accounts reported by more than one member}, clashes: {usernames reported by more than
    one member}, claude_accounts: sorted distinct Claude accounts}."""
    rows = of_project(db, project_id)
    owners, users = {}, {}
    for r in rows:
        owners.setdefault(r["claude_account"], set()).add(r["account_id"])
        users.setdefault(r["username"], set()).add(r["account_id"])
    shared = {c for c, ids in owners.items() if len(ids) > 1}
    by_account = {}
    for r in rows:
        by_account.setdefault(r["account_id"], []).append(
            {"claude_account": r["claude_account"], "username": r["username"], "surface": r["surface"],
             "shared": r["claude_account"] in shared})
    return {"by_account": by_account, "shared": shared, "claude_accounts": sorted(owners),
            "clashes": sorted(u for u, ids in users.items() if len(ids) > 1)}
