"""One-time links instead of passwords passed by hand (decision record 0008).

- **Invite:** an owner or admin invites an email that has no account. The account is created **without a password**
  (NO_PASSWORD never verifies, so it cannot sign in) and a link is made; the person sets their own password with it.
- **Reset:** an admin resets an account's password. The old password stops working at once (NO_PASSWORD), every session
  of the account ends, and a link is made.
- A link is 32 random bytes (urlsafe), shown once to the person who made it and stored only as its SHA-256. It works
  **once**, **expires after LIFETIME**, and making a new link for an account ends that account's unused links. Setting a
  password with it follows the password policy, clears any lockout, ends every session and signs the person in.
- Nothing here sends email: the person who made the link passes it on. The link is never written to the audit log.
"""
import datetime
import hashlib
import secrets

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from . import accounts, audit, auth, passwords, sessions
from .db import get_db

bp = Blueprint("invites", __name__)

LIFETIME = datetime.timedelta(hours=72)
NO_PASSWORD = "!"                    # stored as password_hash: not a scrypt hash, so it never verifies
KINDS = ("invite", "reset")


class InviteError(ValueError):
    pass


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def has_password(account):
    return bool(account) and account["password_hash"] != NO_PASSWORD


def create_account(db, email, role="user", now=None):
    """Create and commit an account that has no password yet; return its row."""
    if role not in accounts.INVITE_ROLES:
        raise accounts.AccountError("role must be one of %s" % ", ".join(accounts.INVITE_ROLES))
    email = accounts.normalize_email(email)
    if accounts.find_by_email(db, email) is not None:
        raise accounts.AccountError("an account with that email already exists")
    account_id = secrets.token_hex(16)
    db.execute("INSERT INTO account (id, email, password_hash, role, disabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (account_id, email, NO_PASSWORD, role, 0, now or accounts.now_iso()))
    db.commit()
    return accounts.get(db, account_id)


def issue(db, account_id, kind, created_by, moment):
    """Make a one-time link for `account_id` and commit -> the raw token (shown once; only its hash is kept). Unused links
    of the account stop working. A reset also takes the current password away and ends every session."""
    if kind not in KINDS:
        raise ValueError("unknown link kind: %r" % (kind,))
    if accounts.get(db, account_id) is None:
        raise InviteError("no such account")
    raw = secrets.token_urlsafe(32)
    at = sessions.iso(moment)
    db.execute("DELETE FROM invite WHERE account_id = ? AND used_at IS NULL", (account_id,))
    if kind == "reset":
        db.execute("UPDATE account SET password_hash = ?, failed_count = 0, locked_until = NULL WHERE id = ?",
                   (NO_PASSWORD, account_id))
        db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
    db.execute("INSERT INTO invite (id, account_id, token_hash, kind, created_by, created_at, expires_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)",
               (secrets.token_hex(16), account_id, _hash(raw), kind, created_by, at, sessions.iso(moment + LIFETIME)))
    db.commit()
    return raw


def open_links(db, moment):
    """-> {account_id: expires_at} of each account's newest link that still works."""
    out = {}
    for r in db.query("SELECT account_id, expires_at FROM invite WHERE used_at IS NULL AND expires_at > ?"
                      " ORDER BY created_at", (sessions.iso(moment),)):
        out[r["account_id"]] = r["expires_at"]
    return out


def cancel(db, account_id, moment):
    """End every unused link of the account at once (no commit). The account stays, with no password."""
    db.execute("DELETE FROM invite WHERE account_id = ? AND used_at IS NULL", (account_id,))


def find(db, raw, moment):
    """-> the link row (with the account's email and disabled flag) while it is unused and not expired, else None."""
    if not raw or len(raw) > 128:
        return None
    row = db.query_one("SELECT i.*, a.email, a.disabled, a.name FROM invite i JOIN account a ON a.id = i.account_id "
                       "WHERE i.token_hash = ?", (_hash(raw),))
    if row is None or row["used_at"] or row["expires_at"] <= sessions.iso(moment) or row["disabled"]:
        return None
    return row


def accept(db, raw, password, moment):
    """Set the password with a link and commit -> the account. InviteError when the link no longer works;
    passwords.PasswordError when the password breaks the policy (the link stays usable)."""
    row = find(db, raw, moment)
    if row is None:
        raise InviteError("this link no longer works")
    password_hash = passwords.hash_password(password)
    at = sessions.iso(moment)
    cur = db.execute("UPDATE invite SET used_at = ? WHERE id = ? AND used_at IS NULL", (at, row["id"]))
    if cur.rowcount != 1:                                  # used by a request that came in at the same moment
        raise InviteError("this link no longer works")
    db.execute("UPDATE account SET password_hash = ?, failed_count = 0, locked_until = NULL WHERE id = ?",
               (password_hash, row["account_id"]))
    db.execute("DELETE FROM session WHERE account_id = ?", (row["account_id"],))
    db.commit()
    return accounts.get(db, row["account_id"]), row["kind"]


def link(raw):
    """The full address to send: PUBLIC_URL when it is set (production), else this request's own host."""
    base = (current_app.config.get("PUBLIC_URL") or "").strip().rstrip("/")
    path = url_for("invites.accept_page", token=raw)
    return base + path if base else url_for("invites.accept_page", token=raw, _external=True)


@bp.get("/invite/<token>")
def accept_page(token):
    row = find(get_db(), token, auth.now())
    if row is None:
        return render_template("invite.html", row=None, error=None), 404
    return render_template("invite.html", row=row, form={"name": row["name"] or ""}, error=None)


@bp.post("/invite/<token>")
def accept_post(token):
    db = get_db()
    moment = auth.now()
    password, again = request.form.get("password", ""), request.form.get("password_again", "")
    row = find(db, token, moment)
    if row is None:
        return render_template("invite.html", row=None, error=None), 404
    form = {"name": (request.form.get("name") or "")[:accounts.MAX_NAME]}
    if password != again:
        return render_template("invite.html", row=row, form=form, error="The two passwords are not the same."), 400
    try:
        name = accounts.clean_name(request.form.get("name"))
    except accounts.AccountError as exc:
        return render_template("invite.html", row=row, form=form, error=str(exc)), 400
    try:
        account, kind = accept(db, token, password, moment)
    except passwords.PasswordError as exc:
        return render_template("invite.html", row=row, form=form, error=str(exc)), 400
    except InviteError:
        abort(404)
    if name:
        accounts.set_name(db, account["id"], name)
    sessions.delete(db, request.cookies.get(auth.SESSION_COOKIE))
    raw_id, _csrf = sessions.create(db, account["id"], moment)
    audit.record(db, "%s.accept" % kind, sessions.iso(moment), account_id=account["id"],
                 detail={"email": account["email"]})
    db.commit()
    g.new_session_id = raw_id
    return redirect(url_for("auth.home"))
