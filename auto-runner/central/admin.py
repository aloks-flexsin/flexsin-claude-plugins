"""Account administration (admins only) and changing your own password (everyone signed in).

- Admin pages answer 404 to anyone who is not a signed-in, enabled admin, so they do not reveal themselves.
- A password change or reset ends every session of that account (the person signs in again).
- The last enabled admin cannot be disabled or demoted (enforced in accounts.py).
- Every action writes an audit row; the password is never in it. CSRF is checked for every POST in auth.py.
"""
from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import accounts, audit, auth, passwords, sessions
from .db import get_db

bp = Blueprint("admin", __name__)


def _at():
    return sessions.iso(auth.now())


def _require_admin():
    account = g.get("account")
    if account is None or account["role"] != "admin" or not accounts.is_enabled(account):
        abort(404)


def _target(db, account_id):
    account = accounts.get(db, account_id)
    if account is None:
        abort(404)
    return account


def _list_page(error=None, status=200, form_email=""):
    page = render_template("admin_accounts.html", accounts=accounts.list_all(get_db()), roles=accounts.ROLES,
                           error=error, form_email=form_email)
    return page, status


def _refused(db, action, target, reason):
    audit.record(db, action + ".refused", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"], "reason": reason})
    db.commit()
    return _list_page(error=reason, status=400)


@bp.get("/admin/accounts")
def accounts_page():
    _require_admin()
    return _list_page()


@bp.post("/admin/accounts")
def account_add():
    _require_admin()
    db = get_db()
    email = request.form.get("email", "")
    role = request.form.get("role", "user")
    try:
        created = accounts.create(db, email, request.form.get("password", ""), role=role, now=_at())
    except (accounts.AccountError, passwords.PasswordError) as exc:
        return _list_page(error=str(exc), status=400, form_email=email.strip()[:accounts.MAX_EMAIL])
    audit.record(db, "account.create", _at(), account_id=g.account["id"],
                 detail={"target_id": created["id"], "email": created["email"], "role": created["role"]})
    db.commit()
    return redirect(url_for("admin.accounts_page"))


@bp.post("/admin/accounts/<account_id>/disabled")
def account_disabled(account_id):
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    disable = request.form.get("disabled") == "1"
    action = "account.disable" if disable else "account.enable"
    try:
        accounts.set_disabled(db, target["id"], disable)
    except accounts.AccountError as exc:
        return _refused(db, action, target, str(exc))
    audit.record(db, action, _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"]})
    db.commit()
    return redirect(url_for("admin.accounts_page"))


@bp.post("/admin/accounts/<account_id>/role")
def account_role(account_id):
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    role = request.form.get("role", "")
    try:
        accounts.set_role(db, target["id"], role)
    except accounts.AccountError as exc:
        return _refused(db, "account.role", target, str(exc))
    audit.record(db, "account.role", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"], "from": target["role"], "to": role})
    db.commit()
    return redirect(url_for("admin.accounts_page"))


@bp.post("/admin/accounts/<account_id>/password")
def account_reset_password(account_id):
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    try:
        accounts.set_password(db, target["id"], request.form.get("password", ""))
    except passwords.PasswordError as exc:
        return _list_page(error=str(exc), status=400)
    audit.record(db, "account.password_reset", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"]})
    db.commit()
    if target["id"] == g.account["id"]:
        g.clear_session_cookie = True
        return redirect(url_for("auth.signin"))
    return redirect(url_for("admin.accounts_page"))


@bp.get("/account/password")
@auth.login_required
def own_password():
    return render_template("own_password.html", error=None)


@bp.post("/account/password")
@auth.login_required
def own_password_post():
    db = get_db()
    me = g.account
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    if not passwords.verify_password(current, me["password_hash"]):
        error, reason = "The current password is not correct.", "wrong_current_password"
    elif new != request.form.get("confirm_password", ""):
        error, reason = "The new passwords do not match.", "mismatch"
    else:
        try:
            accounts.set_password(db, me["id"], new)
            error = reason = None
        except passwords.PasswordError as exc:
            error, reason = str(exc), "policy"
    if error:
        audit.record(db, "account.password_change.refused", _at(), account_id=me["id"],
                     detail={"email": me["email"], "reason": reason})
        db.commit()
        return render_template("own_password.html", error=error), 400
    audit.record(db, "account.password_change", _at(), account_id=me["id"], detail={"email": me["email"]})
    db.commit()
    g.clear_session_cookie = True
    return redirect(url_for("auth.signin"))
