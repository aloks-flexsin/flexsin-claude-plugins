"""Account store: create, look up, disable, change role and password. Emails are unique regardless of case
(stored lower-cased).

There is no self sign-up: accounts come from `python -m central create-admin` or an admin (see admin.py).
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


def list_all(db):
    return db.query("SELECT * FROM account ORDER BY email")


# Appended to an UPDATE's WHERE so that, in the same statement, the row is left alone when it is the only
# enabled admin. Disabling or demoting it would leave nobody able to administer the service.
_NOT_LAST_ADMIN = (" AND NOT (role = 'admin' AND disabled = 0 AND"
                   " (SELECT COUNT(*) FROM account WHERE role = 'admin' AND disabled = 0) <= 1)")


def _require(db, account_id):
    account = get(db, account_id)
    if account is None:
        raise AccountError("no such account")
    return account


def set_disabled(db, account_id, disabled):
    """Disable or enable and commit; disabling ends every session of the account. The last enabled admin
    cannot be disabled."""
    _require(db, account_id)
    if disabled:
        cur = db.execute("UPDATE account SET disabled = 1 WHERE id = ?" + _NOT_LAST_ADMIN, (account_id,))
        if cur.rowcount == 0:
            raise AccountError("the last enabled admin cannot be disabled")
        db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
    else:
        db.execute("UPDATE account SET disabled = 0 WHERE id = ?", (account_id,))
    db.commit()
    return get(db, account_id)


def set_role(db, account_id, role):
    """Change the role and commit. The last enabled admin cannot be demoted."""
    if role not in ROLES:
        raise AccountError("role must be one of %s" % ", ".join(ROLES))
    _require(db, account_id)
    sql = "UPDATE account SET role = ? WHERE id = ?"
    if role != "admin":
        sql += _NOT_LAST_ADMIN
    cur = db.execute(sql, (role, account_id))
    if cur.rowcount == 0:
        raise AccountError("the last enabled admin cannot be demoted")
    db.commit()
    return get(db, account_id)


def set_password(db, account_id, password):
    """Check the policy, store the new hash, clear any lockout and end every session of the account; commit."""
    _require(db, account_id)
    password_hash = passwords.hash_password(password)
    db.execute("UPDATE account SET password_hash = ?, failed_count = 0, locked_until = NULL WHERE id = ?",
               (password_hash, account_id))
    db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
    db.commit()
    return get(db, account_id)


def is_enabled(account):
    return bool(account) and not account["disabled"]
