"""Account administration (admins only) and changing your own password (everyone signed in).

- Admin pages answer 404 to anyone who is not a signed-in, enabled admin, so they do not reveal themselves.
- A password change or reset ends every session of that account (the person signs in again).
- The last enabled admin cannot be disabled or demoted (enforced in accounts.py).
- Every action writes an audit row; the password is never in it. CSRF is checked for every POST in auth.py.
"""
from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import accounts, audit, auth, identities, invites, notifications, passwords, personal, push, sessions, ui
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


def _list_page(error=None, status=200, form_email="", link=None):
    """`link`: {'email', 'link', 'kind'} to show a just-made one-time link once."""
    db = get_db()
    seen, counts = accounts.last_active(db), accounts.project_counts(db)
    q = (request.args.get("q") or "").strip().lower()[:accounts.MAX_EMAIL]
    rows = [dict(a, pending=not invites.has_password(a), last_active=seen.get(a["id"]), projects=counts.get(a["id"], 0))
            for a in accounts.list_all(db)]
    total = len(rows)
    if q:
        rows = [a for a in rows if q in a["email"].lower() or q in (a["name"] or "").lower()]
    page = render_template("admin_accounts.html", accounts=rows, total=total, q=q, roles=accounts.ROLES, error=error,
                           form_email=form_email, link=link, lifetime_hours=int(invites.LIFETIME.total_seconds() // 3600))
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
        created = invites.create_account(db, email, role=role, now=_at())
    except accounts.AccountError as exc:
        return _list_page(error=str(exc), status=400, form_email=email.strip()[:accounts.MAX_EMAIL])
    audit.record(db, "account.create", _at(), account_id=g.account["id"],
                 detail={"target_id": created["id"], "email": created["email"], "role": created["role"], "via": "invite"})
    raw = invites.issue(db, created["id"], "invite", g.account["id"], auth.now())
    audit.record(db, "invite.create", _at(), account_id=g.account["id"],
                 detail={"target_id": created["id"], "email": created["email"]})
    db.commit()
    return _list_page(link={"email": created["email"], "link": invites.link(raw), "kind": "invite"})


@bp.post("/admin/accounts/<account_id>/disabled")
def account_disabled(account_id):
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    disable = request.form.get("disabled") == "1"
    if disable and not ui.confirmed():
        return ui.confirm_page("Disable %s?" % target["email"],
                               "They are signed out everywhere, their tokens stop working and their runners stop at their next "
                               "step. You can enable the account again later.", "Disable", url_for("admin.accounts_page"))
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
    if target["id"] == g.account["id"]:
        return _refused(db, "account.password_reset", target, "use the Account page to change your own password")
    kind = "reset" if invites.has_password(target) else "invite"
    raw = invites.issue(db, target["id"], kind, g.account["id"], auth.now())
    audit.record(db, "account.password_reset" if kind == "reset" else "invite.create", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"]})
    db.commit()
    return _list_page(link={"email": target["email"], "link": invites.link(raw), "kind": kind})


@bp.post("/admin/accounts/<account_id>/tokens/revoke")
def account_tokens_revoke(account_id):
    """Revoke every personal token of an account (for example a lost laptop or someone leaving)."""
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    if not ui.confirmed():
        return ui.confirm_page("Revoke every token of %s?" % target["email"],
                               "Each computer they signed in from stops working at once; they sign in again from Claude to "
                               "get a new one.", "Revoke tokens", url_for("admin.accounts_page"))
    count = personal.revoke_all(db, target["id"], auth.now())
    audit.record(db, "personal_token.revoke_all", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"], "count": count})
    db.commit()
    return redirect(url_for("admin.accounts_page"))


@bp.get("/admin/notifications")
def notifications_log():
    """Admins only: the newest notifications across people, with how each was delivered. `?account=` (an account id
    or email) and `?kind=` narrow it; an unknown account or kind lists nothing rather than everything."""
    _require_admin()
    db = get_db()
    everyone = accounts.list_all(db)
    wanted = (request.args.get("account") or "").strip()[:accounts.MAX_EMAIL]
    kind = (request.args.get("kind") or "").strip()[:20]
    account_id = None
    if wanted:
        found = accounts.get(db, wanted) or accounts.find_by_email(db, wanted)
        account_id = found["id"] if found else "-"
    rows = [dict(r, delivery=notifications.delivery(r))
            for r in notifications.recent(db, account_id=account_id, kind=kind or None)]
    return render_template("admin_notifications.html", rows=rows, people=everyone, kinds=notifications.KINDS,
                           account_id=account_id, kind=kind)


def _account_page(error=None, status=200, name_error=None, saved=None):
    return render_template("account.html", error=error, name_error=name_error, saved=saved,
                           push=push.account_state(get_db(), g.account),
                           identities=identities.of_account(get_db(), g.account["id"])), status


@bp.get("/account")
@auth.login_required
def account_page():
    return _account_page(saved=request.args.get("saved"))


@bp.get("/account/password")
@auth.login_required
def own_password():
    return redirect(url_for("admin.account_page") + "#password")


@bp.post("/account/name")
@auth.login_required
def own_name_post():
    db = get_db()
    try:
        name = accounts.set_name(db, g.account["id"], request.form.get("name"))
    except accounts.AccountError as exc:
        return _account_page(name_error=str(exc), status=400)
    audit.record(db, "account.name", _at(), account_id=g.account["id"], detail={"name": name})
    db.commit()
    return redirect(url_for("admin.account_page", saved="name"))


@bp.post("/admin/accounts/<account_id>/name")
def account_name(account_id):
    """Admins only: set someone's display name (people set their own on Account)."""
    _require_admin()
    db = get_db()
    target = _target(db, account_id)
    try:
        name = accounts.set_name(db, target["id"], request.form.get("name"))
    except accounts.AccountError as exc:
        return _list_page(error=str(exc), status=400)
    audit.record(db, "account.name", _at(), account_id=g.account["id"],
                 detail={"target_id": target["id"], "email": target["email"], "name": name})
    db.commit()
    return redirect(url_for("admin.accounts_page"))


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
        return _account_page(error=error, status=400)
    audit.record(db, "account.password_change", _at(), account_id=me["id"], detail={"email": me["email"]})
    db.commit()
    g.clear_session_cookie = True
    return redirect(url_for("auth.signin"))
