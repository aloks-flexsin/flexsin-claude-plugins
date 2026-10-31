"""Sign-in, sign-out, the signed-in account (`g.account`), lockout and CSRF for every POST form.

- The session cookie holds a random id; only its sha256 is stored (see sessions.py). A new id at every sign-in.
- 5 failed sign-ins lock the account for 15 minutes. Unknown email, wrong password, locked and disabled all get
  the same answer, so the page never tells whether an account exists.
- Before sign-in the CSRF token is a random value in its own cookie (double submit); after sign-in it is the
  session's token. A POST to a page without a matching `csrf_token` field gets 400. `/api/` routes use bearer
  tokens instead and are not forms.
- The clock is `app.config["CLOCK"]` (a callable returning an aware UTC datetime) so tests can move time.
"""
import datetime
import functools
import hmac
import logging
import secrets

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from . import accounts, audit, passwords, sessions
from .db import get_db

SESSION_COOKIE = "__Host-session"
CSRF_COOKIE = "__Host-csrf"
MAX_FAILURES = 5
LOCKOUT = datetime.timedelta(minutes=15)
FAILED_MESSAGE = "The email or password is not correct, or the account cannot sign in right now."

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)

_dummy_hash = None


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def now():
    return current_app.config.get("CLOCK", utcnow)()


def _set_cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="Lax")


def _clear_cookie(response, name):
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="Lax")


def csrf_token():
    """The token forms must send back: the session's when signed in, else the pre-session cookie's."""
    if g.get("session_row"):
        return g.session_row["csrf_token"]
    if not g.get("anon_csrf"):
        g.anon_csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    return g.anon_csrf


def _load_session():
    g.account = None
    g.session_row = None
    raw_id = request.cookies.get(SESSION_COOKIE)
    if not raw_id:
        return
    db = get_db()
    row = sessions.lookup(db, raw_id, now())
    account = accounts.get(db, row["account_id"]) if row else None
    if row and not accounts.is_enabled(account):
        sessions.delete(db, raw_id)
        row = None
    db.commit()
    if row:
        g.session_row, g.account = row, account
    else:
        g.clear_session_cookie = True


def _check_csrf():
    if request.method != "POST" or request.url_rule is None or request.path.startswith("/api/"):
        return None
    sent = request.form.get("csrf_token", "")
    if g.session_row:
        expected = g.session_row["csrf_token"]
    else:
        expected = request.cookies.get(CSRF_COOKIE, "")
    if not sent or not expected or not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
        abort(400, description="The form is missing its security token or it has expired; reload and try again.")
    return None


def _before():
    _load_session()
    _check_csrf()


def _after(response):
    if g.get("new_session_id"):
        _set_cookie(response, SESSION_COOKIE, g.new_session_id, int(sessions.ABSOLUTE.total_seconds()))
    elif g.get("clear_session_cookie"):
        _clear_cookie(response, SESSION_COOKIE)
    if g.get("anon_csrf") and request.cookies.get(CSRF_COOKIE) != g.anon_csrf:
        _set_cookie(response, CSRF_COOKIE, g.anon_csrf, None)
    return response


def init_app(app):
    app.before_request(_before)
    app.after_request(_after)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.register_blueprint(bp)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("account") is None:
            return redirect(url_for("auth.signin"))
        return view(*args, **kwargs)
    return wrapped


def _burn_time(password):
    """Verify against a throwaway hash so an unknown email takes as long as a wrong password."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = passwords.hash_password(secrets.token_urlsafe(24))
    passwords.verify_password(password, _dummy_hash)


def authenticate(db, email, password, moment):
    """Return (account, None) on success or (account or None, reason) on failure. Updates lockout state;
    the caller commits. Reasons: unknown_email, wrong_password, locked, disabled."""
    account = accounts.find_by_email(db, email)
    if account is None:
        _burn_time(password)
        return None, "unknown_email"
    at = sessions.iso(moment)
    if account["locked_until"] and account["locked_until"] > at:
        _burn_time(password)
        return account, "locked"
    if account["locked_until"]:
        db.execute("UPDATE account SET failed_count = 0, locked_until = NULL WHERE id = ?", (account["id"],))
        account["failed_count"], account["locked_until"] = 0, None
    if not passwords.verify_password(password, account["password_hash"]):
        failures = account["failed_count"] + 1
        if failures >= MAX_FAILURES:
            db.execute("UPDATE account SET failed_count = 0, locked_until = ? WHERE id = ?",
                       (sessions.iso(moment + LOCKOUT), account["id"]))
        else:
            db.execute("UPDATE account SET failed_count = ? WHERE id = ?", (failures, account["id"]))
        return account, "wrong_password"
    if not accounts.is_enabled(account):
        return account, "disabled"
    db.execute("UPDATE account SET failed_count = 0, locked_until = NULL WHERE id = ?", (account["id"],))
    return account, None


@bp.get("/signin")
def signin():
    if g.account is not None:
        return redirect(url_for("auth.home"))
    return render_template("signin.html", error=None, email="")


@bp.post("/signin")
def signin_post():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    db = get_db()
    moment = now()
    account, reason = authenticate(db, email, password, moment)
    shown_email = email.strip().lower()[:accounts.MAX_EMAIL]
    at = sessions.iso(moment)
    if reason:
        audit.record(db, "signin.failure", at, account_id=account["id"] if account else None,
                     detail={"email": shown_email, "reason": reason})
        db.commit()
        log.info("sign-in failed for %s (%s)", shown_email, reason)
        return render_template("signin.html", error=FAILED_MESSAGE, email=shown_email), 401
    # A new id at every sign-in: the old session (if any) ends here.
    sessions.delete(db, request.cookies.get(SESSION_COOKIE))
    raw_id, _csrf = sessions.create(db, account["id"], moment)
    audit.record(db, "signin.success", at, account_id=account["id"], detail={"email": account["email"]})
    db.commit()
    log.info("sign-in succeeded for %s", account["email"])
    g.new_session_id = raw_id
    return redirect(url_for("auth.home"))


@bp.post("/signout")
def signout():
    db = get_db()
    if g.account is not None:
        sessions.delete(db, request.cookies.get(SESSION_COOKIE))
        audit.record(db, "signout", sessions.iso(now()), account_id=g.account["id"],
                     detail={"email": g.account["email"]})
        db.commit()
    g.clear_session_cookie = True
    return redirect(url_for("auth.signin"))


@bp.get("/")
@login_required
def home():
    return render_template("home.html", account=g.account)
