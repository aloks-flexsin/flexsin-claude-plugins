"""Server-side sessions. The cookie carries a random id; the database keeps only its sha256.

A session ends 12 hours after sign-in (absolute) or after 2 hours without a request (idle), whichever comes first.
"""
import datetime
import hashlib
import secrets

ABSOLUTE = datetime.timedelta(hours=12)
IDLE = datetime.timedelta(hours=2)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def iso(moment):
    return moment.astimezone(datetime.timezone.utc).strftime(TIME_FORMAT)


def parse_iso(text):
    return datetime.datetime.strptime(text, TIME_FORMAT).replace(tzinfo=datetime.timezone.utc)


def hash_id(raw_id):
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


def create(db, account_id, now):
    """Start a session (the caller commits); return (raw id for the cookie, CSRF token)."""
    raw_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO session (id_hash, account_id, csrf_token, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
               (hash_id(raw_id), account_id, csrf_token, iso(now), iso(now)))
    return raw_id, csrf_token


def expired(row, now):
    return (now - parse_iso(row["created_at"]) >= ABSOLUTE
            or now - parse_iso(row["last_seen_at"]) >= IDLE)


def lookup(db, raw_id, now):
    """The live session row for a cookie value, with `last_seen_at` moved to now; None when missing or expired.

    An expired row is deleted. The caller commits.
    """
    if not raw_id:
        return None
    row = db.query_one("SELECT * FROM session WHERE id_hash = ?", (hash_id(raw_id),))
    if row is None:
        return None
    if expired(row, now):
        delete(db, raw_id)
        return None
    row["last_seen_at"] = iso(now)
    db.execute("UPDATE session SET last_seen_at = ? WHERE id_hash = ?", (row["last_seen_at"], row["id_hash"]))
    return row


def delete(db, raw_id):
    if raw_id:
        db.execute("DELETE FROM session WHERE id_hash = ?", (hash_id(raw_id),))


def delete_for_account(db, account_id):
    db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
