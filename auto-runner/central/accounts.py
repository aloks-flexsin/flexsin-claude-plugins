"""Account store: create, look up, disable. Emails are unique regardless of case (stored lower-cased).

There is no self sign-up: accounts come from `python -m central create-admin` or, later, an admin.
"""
import datetime
import secrets

from . import passwords

ROLES = ("admin", "user")
MAX_EMAIL = 320


class AccountError(ValueError):
    """The account cannot be created or changed as asked."""


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_email(email):
    """Trim and lower-case; raise AccountError for something that is not an email address."""
    email = (email or "").strip().lower()
    local, at, domain = email.partition("@")
    if (not at or not local or "@" in domain or "." not in domain.strip(".") or domain.startswith(".")
            or domain.endswith(".") or len(email) > MAX_EMAIL or any(ch.isspace() for ch in email)):
        raise AccountError("not a valid email address")
    return email


def find_by_email(db, email):
    try:
        email = normalize_email(email)
    except AccountError:
        return None
    return db.query_one("SELECT * FROM account WHERE email = ?", (email,))


def get(db, account_id):
    return db.query_one("SELECT * FROM account WHERE id = ?", (account_id,))


def create(db, email, password, role="user", now=None):
    """Create and commit an account; return its row. The password is checked against the policy and hashed."""
    if role not in ROLES:
        raise AccountError("role must be one of %s" % ", ".join(ROLES))
    email = normalize_email(email)
    password_hash = passwords.hash_password(password)
    if find_by_email(db, email) is not None:
        raise AccountError("an account with that email already exists")
    account_id = secrets.token_hex(16)
    db.execute("INSERT INTO account (id, email, password_hash, role, disabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (account_id, email, password_hash, role, 0, now or now_iso()))
    db.commit()
    return get(db, account_id)


def set_disabled(db, account_id, disabled):
    cur = db.execute("UPDATE account SET disabled = ? WHERE id = ?", (1 if disabled else 0, account_id))
    if cur.rowcount == 0:
        raise AccountError("no such account")
    db.commit()
    return get(db, account_id)


def is_enabled(account):
    return bool(account) and not account["disabled"]
