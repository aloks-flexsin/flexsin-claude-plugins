"""Project pages and the `project_access(min_role)` guard (the rules are in projects.py).

- `/projects` lists the projects the signed-in person may see; admins also get a form to create one.
- `/projects/<slug>` is the members page (any member); owners and admins add, re-role and remove members there.
- The guard answers 404 to anyone signed out, to non-members and to members below `min_role`: never 403.
- Project creation and every membership change (and refusal) write an audit row with the project's id.
"""
import functools

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import audit, auth, projects, sessions
from .db import get_db

bp = Blueprint("projects", __name__)


def _at():
    return sessions.iso(auth.now())


def project_access(min_role):
    """Decorate a view taking `slug`: 404 unless the signed-in account has at least `min_role` on that project
    (admins always do). Sets `g.project` and `g.project_role` ('admin' or the member role)."""
    if min_role not in projects.RANK:
        raise ValueError("unknown project role: %r" % (min_role,))

    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            db = get_db()
            project = projects.get_by_slug(db, kwargs.get("slug"))
            role = projects.effective_role(db, project, g.get("account"))
            if not projects.allows(role, min_role):
                abort(404)
            g.project, g.project_role = project, role
            return view(*args, **kwargs)
        return wrapped
    return decorate


def _list_page(error=None, status=200, form=None):
    page = render_template("projects.html", projects=projects.visible_to(get_db(), g.account),
                           is_admin=g.account["role"] == projects.ADMIN, error=error, form=form or {})
    return page, status


@bp.get("/projects")
@auth.login_required
def projects_page():
    return _list_page()


@bp.post("/projects")
def project_create():
    account = g.get("account")
    if account is None or account["role"] != projects.ADMIN:
        abort(404)
    db = get_db()
    slug, name = request.form.get("slug", ""), request.form.get("name", "")
    try:
        project = projects.create(db, slug, name, _at())
    except projects.ProjectError as exc:
        return _list_page(error=str(exc), status=400, form={"slug": slug.strip()[:64], "name": name.strip()[:200]})
    audit.record(db, "project.create", _at(), account_id=account["id"], project_id=project["id"],
                 detail={"slug": project["slug"], "name": project["name"]})
    db.commit()
    return redirect(url_for("projects.members_page", slug=project["slug"]))


def _members_page(error=None, status=200, form_email=""):
    db = get_db()
    page = render_template("project_members.html", project=g.project, members=projects.members(db, g.project["id"]),
                           can_manage=projects.allows(g.project_role, "owner"), roles=projects.MEMBER_ROLES,
                           my_role=g.project_role, error=error, form_email=form_email)
    return page, status


def _record(db, action, detail):
    audit.record(db, action, _at(), account_id=g.account["id"], project_id=g.project["id"], detail=detail)
    db.commit()


@bp.get("/projects/<slug>")
@project_access("viewer")
def members_page(slug):
    return _members_page()


@bp.post("/projects/<slug>/members")
@project_access("owner")
def member_add(slug):
    db = get_db()
    email = request.form.get("email", "")
    role = request.form.get("role", "viewer")
    try:
        added = projects.add_member(db, g.project["id"], email, role, _at())
    except projects.ProjectError as exc:
        _record(db, "member.add.refused", {"email": email.strip().lower()[:320], "role": role[:16],
                                           "reason": str(exc)})
        return _members_page(error=str(exc), status=400, form_email=email.strip()[:320])
    _record(db, "member.add", {"target_id": added["id"], "email": added["email"], "role": role})
    return redirect(url_for("projects.members_page", slug=slug))


def _member_or_404(db, account_id):
    found = [m for m in projects.members(db, g.project["id"]) if m["account_id"] == account_id]
    if not found:
        abort(404)
    return found[0]


@bp.post("/projects/<slug>/members/<account_id>/role")
@project_access("owner")
def member_role(slug, account_id):
    db = get_db()
    target = _member_or_404(db, account_id)
    role = request.form.get("role", "")
    try:
        projects.set_member_role(db, g.project["id"], account_id, role)
    except projects.ProjectError as exc:
        _record(db, "member.role.refused", {"target_id": account_id, "email": target["email"], "role": role[:16],
                                            "reason": str(exc)})
        return _members_page(error=str(exc), status=400)
    _record(db, "member.role", {"target_id": account_id, "email": target["email"], "from": target["role"], "to": role})
    return redirect(url_for("projects.members_page", slug=slug))


@bp.post("/projects/<slug>/members/<account_id>/remove")
@project_access("owner")
def member_remove(slug, account_id):
    db = get_db()
    target = _member_or_404(db, account_id)
    try:
        projects.remove_member(db, g.project["id"], account_id)
    except projects.ProjectError as exc:
        _record(db, "member.remove.refused", {"target_id": account_id, "email": target["email"], "reason": str(exc)})
        return _members_page(error=str(exc), status=400)
    _record(db, "member.remove", {"target_id": account_id, "email": target["email"], "role": target["role"]})
    if account_id == g.account["id"] and g.project_role != projects.ADMIN:
        return redirect(url_for("projects.projects_page"))
    return redirect(url_for("projects.members_page", slug=slug))
