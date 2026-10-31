"""Audit rows: who did what, when. `detail` is a small JSON object; callers never put a password or token in it."""
import json
import secrets
import threading
import time

_lock = threading.Lock()
_last_ns = 0


def new_id():
    """Ids sort in insertion order (strictly increasing nanoseconds in this process, then random)."""
    global _last_ns
    with _lock:
        _last_ns = max(time.time_ns(), _last_ns + 1)
        stamp = _last_ns
    return "%020d%s" % (stamp, secrets.token_hex(6))


def record(db, action, at, account_id=None, project_id=None, detail=None):
    """Insert one audit row (the caller commits)."""
    db.execute("INSERT INTO audit (id, at, account_id, project_id, action, detail) VALUES (?, ?, ?, ?, ?, ?)",
               (new_id(), at, account_id, project_id, action,
                json.dumps(detail or {}, sort_keys=True)))


def rows(db, action=None):
    """Audit rows oldest first, `detail` decoded (used by tests and, later, the audit page)."""
    if action is None:
        found = db.query("SELECT * FROM audit ORDER BY at, id")
    else:
        found = db.query("SELECT * FROM audit WHERE action = ? ORDER BY at, id", (action,))
    for row in found:
        row["detail"] = json.loads(row["detail"] or "{}")
    return found
