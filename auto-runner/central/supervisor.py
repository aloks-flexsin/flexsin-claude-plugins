"""Supervisor read API (decision record 0010): who the caller is, and one project's state, read-only.

Both routes take a personal token. `state` needs an account with the `supervisor` role (any project, no membership
needed); it never shows a claim id and it writes nothing.
"""
import json

from flask import Blueprint, jsonify, request

from . import accounts, audit, auth, personal, projects, runners, sessions, tasks, tokens
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


PLAN_ROUND_CAP = 2          # a plan is sent back at most this many times, then it goes to a person
POST_MARKS = ("task.plan.post", "task.create")                    # a runner put a (new) plan up for review
DECISIONS = ("approve", "changes", "escalate")


def plan_marks(db, project_id):
    """-> {task number: {"rounds": supervisor.plan.changes rows, "decided": the supervisor's decision on the CURRENT plan
    post ("approve", "changes", "escalate") or None}}, read from the audit rows (no column; a new plan post starts afresh)."""
    names = ["supervisor.plan." + d for d in DECISIONS] + list(POST_MARKS)
    found = db.query("SELECT action, detail FROM audit WHERE project_id = ? AND action IN (%s) ORDER BY at, id"
                     % ",".join("?" * len(names)), (project_id,) + tuple(names))
    out = {}
    for row in found:
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            continue
        number = detail.get("number") if isinstance(detail, dict) else None
        if number is None:
            continue
        mark = out.setdefault(number, {"rounds": 0, "decided": None})
        if row["action"] in POST_MARKS:
            mark["decided"] = None
        else:
            decision = row["action"].rsplit(".", 1)[1]
            mark["decided"] = decision
            if decision == "changes":
                mark["rounds"] += 1
    return out


def _task(marks, row, moment):
    mark = marks.get(row["number"], {})
    out = tasks.public(row, moment, full=True)
    out.update(plan_rounds=mark.get("rounds", 0), plan_decided=mark.get("decided"), merged_at=row["merged_at"])
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
    marks = plan_marks(db, project["id"])
    return jsonify(project=project["slug"],
                   tasks=[_task(marks, r, moment) for r in tasks.for_project(db, project["id"])],
                   runners=[runners.public(r, moment) for r in runners.for_project(db, project["id"])],
                   blockers=[_blocker(r) for r in blocked])


def _reasons(value):
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        return []
    return [r.strip()[:300] for r in value if r.strip()][:20]


def _error(message, status):
    response = jsonify(error=message)
    response.status_code = status
    return response


@bp.post("/api/v1/supervisor/tasks/<int:number>/plan-decision")
def plan_decision(number):
    """The supervisor decides on a waiting plan: `{"decision": "approve" | "changes" | "escalate", "reasons": [...],
    "policy_sha": "...", "comment": "..."}` with `?project=<slug>`. Approve and changes go through `tasks.decide` (so the
    supervisor must be a manager member). Escalate leaves the plan waiting for a person and tells the managers. Each call
    writes one `supervisor.plan.<decision>` audit row with the policy SHA and the reasons. A `changes` after
    PLAN_ROUND_CAP earlier ones becomes `escalate`. Repeating the call for a plan post that has a decision is a no-op."""
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "plan decisions need a supervisor account")
    project = projects.get_by_slug(db, request.args.get("project"))
    if project is None:
        return _error("no such project", 404)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    decision = body.get("decision")
    if decision not in DECISIONS:
        return _error('decision is "approve", "changes" or "escalate"', 400)
    row = tasks.get(db, project["id"], number)
    if row is None:
        return _error("no such task", 404)
    moment = auth.now()
    mark = plan_marks(db, project["id"]).get(number, {"rounds": 0, "decided": None})
    if mark["decided"] is not None:                          # this plan post already has a decision
        return jsonify(task=tasks.public(row, moment), decision=mark["decided"], already=True, converted=False)
    reasons = _reasons(body.get("reasons"))
    comment = body.get("comment")
    comment = comment if isinstance(comment, str) and comment.strip() else "; ".join(reasons)
    converted = decision == "changes" and mark["rounds"] >= PLAN_ROUND_CAP
    if converted:
        decision = "escalate"
        reasons.append("the plan was already sent back %d times: a person decides" % mark["rounds"])
    sha = body.get("policy_sha") if isinstance(body.get("policy_sha"), str) else None
    try:
        if decision == "escalate":
            if row["status"] != "plan_review":
                raise tasks.TaskError("task %d has no plan waiting for approval" % number, 409)
            tasks.notify_plan(db, project, row, moment)
            updated = row
        else:
            updated = tasks.decide(db, project, row, account, decision, comment, moment)
    except tasks.TaskError as e:
        db.rollback()
        return _error(str(e), e.status)
    audit.record(db, "supervisor.plan." + decision, sessions.iso(moment), account_id=account["id"], project_id=project["id"],
                 detail={"number": number, "policy_sha": sha, "reasons": reasons, "converted": converted,
                         "round": mark["rounds"] + (1 if decision == "changes" else 0)})
    db.commit()
    return jsonify(task=tasks.public(updated, moment), decision=decision, already=False, converted=converted)
