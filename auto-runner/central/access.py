"""Project pages and the `project_access(min_role)` guard (the rules are in projects.py).

- `/projects` lists the projects the signed-in person may see; admins also get a form to create one, which needs the
  project's repository URL (see projects.normalize_repo_url).
- `/projects/<slug>` is the members page (any member) and shows the repository URL; owners and admins add, re-role and
  remove members there and change the repository URL (`POST /projects/<slug>/repo`, audited as `project.repo_url`).
- The guard answers 404 to anyone signed out, to non-members and to members below `min_role`: never 403.
- Project creation and every membership change (and refusal) write an audit row with the project's id.
- `/projects/<slug>/tokens` (managers, owners and admins) creates, revokes and rotates API tokens (see tokens.py);
  a new token appears once, on the page that answers its creation or rotation, and is never in an audit row.
- `GET /api/v1/project` (a project token with any scope) answers the token's project: slug, name and repo_url, so the
  plugin's init and join can check they are pointed at the right repository.
"""
import functools

from flask import Blueprint, abort, g, jsonify, make_response, redirect, render_template, request, url_for

from . import audit, auth, projects, sessions, tokens
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
    repo_url = request.form.get("repo_url", "")
    try:
        project = projects.create(db, slug, name, _at(), repo_url)
    except projects.ProjectError as exc:
        # The refused URL is not shown again: it may hold a credential someone pasted by mistake.
        return _list_page(error=str(exc), status=400, form={"slug": slug.strip()[:64], "name": name.strip()[:200]})
    audit.record(db, "project.create", _at(), account_id=account["id"], project_id=project["id"],
                 detail={"slug": project["slug"], "name": project["name"], "repo_url": project["repo_url"]})
    db.commit()
    return redirect(url_for("projects.members_page", slug=project["slug"]))


def _members_page(error=None, status=200, form_email=""):
    db = get_db()
    page = render_template("project_members.html", project=g.project, members=projects.members(db, g.project["id"]),
                           can_manage=projects.allows(g.project_role, "owner"), roles=projects.MEMBER_ROLES,
                           can_tokens=projects.allows(g.project_role, "manager"),
                           my_role=g.project_role, error=error, form_email=form_email)
    return page, status


def _record(db, action, detail):
    audit.record(db, action, _at(), account_id=g.account["id"], project_id=g.project["id"], detail=detail)
    db.commit()


@bp.get("/projects/<slug>")
@project_access("viewer")
def members_page(slug):
    return _members_page()


@bp.post("/projects/<slug>/repo")
@project_access("owner")
def repo_url_set(slug):
    db = get_db()
    try:
        before = projects.set_repo_url(db, g.project["id"], request.form.get("repo_url", ""))
    except projects.ProjectError as exc:
        # Neither the refused value nor any part of it goes in the audit row: it may hold a credential.
        _record(db, "project.repo_url.refused", {"reason": str(exc)})
        return _members_page(error=str(exc), status=400)
    after = projects.get_by_slug(db, slug)["repo_url"]
    _record(db, "project.repo_url", {"from": before, "to": after})
    return redirect(url_for("projects.members_page", slug=slug))


@bp.get("/api/v1/project")
@tokens.bearer_token(None)
def api_project():
    project = g.token_project
    return jsonify(slug=project["slug"], name=project["name"], repo_url=project.get("repo_url"))


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


def _tokens_page(new_token=None, error=None, status=200):
    page = render_template("project_tokens.html", project=g.project, tokens=tokens.for_project(get_db(), g.project["id"]),
                           scopes=tokens.SCOPES, new_token=new_token, error=error)
    response = make_response(page, status)
    # The page may carry a token shown only this once: never keep it in a cache.
    response.headers["Cache-Control"] = "no-store"
    return response


def _token_detail(row):
    return {"token_id": row["id"], "prefix": row["prefix"], "scopes": row["scopes"]}


@bp.get("/projects/<slug>/tokens")
@project_access("manager")
def tokens_page(slug):
    return _tokens_page()


@bp.post("/projects/<slug>/tokens")
@project_access("manager")
def token_create(slug):
    db = get_db()
    scopes = request.form.getlist("scope")
    try:
        raw, row = tokens.create(db, g.project["id"], scopes, g.account["id"], _at())
    except tokens.TokenError as exc:
        _record(db, "token.create.refused", {"scopes": " ".join(s[:16] for s in scopes[:4]), "reason": str(exc)})
        return _tokens_page(error=str(exc), status=400)
    _record(db, "token.create", _token_detail(row))
    return _tokens_page(new_token=raw, status=201)


def _token_or_404(db, token_id):
    row = tokens.get(db, g.project["id"], token_id)
    if row is None:
        abort(404)
    return row


@bp.post("/projects/<slug>/tokens/<token_id>/revoke")
@project_access("manager")
def token_revoke(slug, token_id):
    db = get_db()
    row = _token_or_404(db, token_id)
    try:
        tokens.revoke(db, g.project["id"], token_id, _at())
    except tokens.TokenError as exc:
        _record(db, "token.revoke.refused", dict(_token_detail(row), reason=str(exc)))
        return _tokens_page(error=str(exc), status=400)
    _record(db, "token.revoke", _token_detail(row))
    return redirect(url_for("projects.tokens_page", slug=slug))


@bp.post("/projects/<slug>/tokens/<token_id>/rotate")
@project_access("manager")
def token_rotate(slug, token_id):
    db = get_db()
    old = _token_or_404(db, token_id)
    try:
        raw, row = tokens.rotate(db, g.project["id"], token_id, g.account["id"], _at())
    except tokens.TokenError as exc:
        _record(db, "token.rotate.refused", dict(_token_detail(old), reason=str(exc)))
        return _tokens_page(error=str(exc), status=400)
    _record(db, "token.rotate", dict(_token_detail(row), old_token_id=old["id"], old_prefix=old["prefix"]))
    return _tokens_page(new_token=raw, status=201)
