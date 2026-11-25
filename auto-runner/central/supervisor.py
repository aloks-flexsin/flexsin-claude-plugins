"""Supervisor read API (decision record 0010): who the caller is, and one project's state, read-only.

Both routes take a personal token. `state` needs an account with the `supervisor` role (any project, no membership
needed); it never shows a claim id and it writes nothing.
"""
import json

from flask import Blueprint, jsonify, request

from . import accounts, auth, personal, projects, runners, tasks, tokens
from .blockers import ANSWERABLE, _content
from .db import get_db

bp = Blueprint("supervisor", __name__)


def _caller(db):
    """-> the token's account (None for a project token, which has none); 401 without a valid token."""
    raw = tokens._bearer()
    if raw and raw.startswith(personal.PREFIX):
        moment = auth.now()
        row, account = personal.lookup(db, raw, moment)
        if row is None:
            tokens._fail(401, "a valid bearer token is required")
        personal.touch(db, row, moment)
        return account
    if raw and tokens.lookup(db, raw) is not None:
        return None
    tokens._fail(401, "a valid bearer token is required")


@bp.get("/api/v1/supervisor/whoami")
def whoami():
    account = _caller(get_db())
    if account is None:
        return jsonify(role=None, email=None)
    return jsonify(role=account["role"], email=account["email"])


def plan_rounds(db, project_id, number):
    """How many times a person asked for changes to this task's plan (its `task.plan.changes` audit rows)."""
    count = 0
    for row in db.query("SELECT detail FROM audit WHERE project_id = ? AND action = ?", (project_id, "task.plan.changes")):
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("number") == number:
            count += 1
    return count


def _task(db, project, row, moment):
    out = tasks.public(row, moment, full=True)
    out.update(plan_rounds=plan_rounds(db, project["id"], row["number"]), merged_at=row["merged_at"])
    return out


def _blocker(row):
    content = _content(row)
    return {"id": row["blocker_id"], "status": row["status"],
            "question": row["question"] or content.get("question") or "", "created_at": row["created_at"]}


@bp.get("/api/v1/supervisor/state")
def state():
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "the supervisor state needs a supervisor account")
    project = projects.get_by_slug(db, request.args.get("project"))
    if project is None:
        response = jsonify(error="no such project")
        response.status_code = 404
        return response
    moment = auth.now()
    marks = ",".join("?" * len(ANSWERABLE))
    blocked = db.query("SELECT * FROM blocker WHERE project_id = ? AND status IN (%s) ORDER BY created_at, id" % marks,
                       (project["id"],) + tuple(ANSWERABLE))
    return jsonify(project=project["slug"],
                   tasks=[_task(db, project, r, moment) for r in tasks.for_project(db, project["id"])],
                   runners=[runners.public(r, moment) for r in runners.for_project(db, project["id"])],
                   blockers=[_blocker(r) for r in blocked])
