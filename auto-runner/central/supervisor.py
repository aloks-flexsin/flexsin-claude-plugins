"""Supervisor API (decision record 0010): who the caller is, one project's state (read-only), adding a task that traces
to asked-for work (6a), plan decisions and merge records.

Every route takes a personal token. All but `whoami` need an account with the `supervisor` role. `state` (any project,
no membership needed) never shows a claim id and writes nothing.
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
    out.update(plan_rounds=mark.get("rounds", 0), plan_decided=mark.get("decided"), merged_at=row["merged_at"],
               created_at=row["created_at"])
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
                   runners=[dict(runners.public(r, moment), id=r["id"], paused_by_supervisor=(
                       r["control"] == "pause" and r["control_role"] == accounts.SUPERVISOR))
                            for r in runners.for_project(db, project["id"])],
                   blockers=[_blocker(r) for r in blocked])


@bp.post("/api/v1/supervisor/tasks")
def add_task():
    """The supervisor adds a task (decision record 0010, 6a) with `?project=<slug>` and `{"title", "description", "from",
    "assignee": email, "plan", "items", "priority", "depends_on"}` (priority and depends_on as on any task). `from` (what the task traces to) is required and is appended to the description
    as "From: ...". The assignee must be a member who can run runners (developer or above); their runner takes the task.
    With `plan` and `items` the task waits for plan approval (`plan_review`, no claim) and a person decides it: the
    supervisor never approves or sends back a plan on a task it added. Otherwise the task is `open` and the assignee's
    runner plans it. Writes `task.create` and `supervisor.task.add` audit rows."""
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "adding a task this way needs a supervisor account")
    project = projects.get_by_slug(db, request.args.get("project"))
    if project is None:
        return _error("no such project", 404)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    email = body.get("assignee")
    assignee = accounts.find_by_email(db, email.strip()) if isinstance(email, str) and email.strip() else None
    if assignee is None:
        return _error("assignee must be the email of a member who can run runners", 400)
    moment = auth.now()
    plan, items = body.get("plan"), body.get("items")
    with_plan = bool((isinstance(plan, str) and plan.strip()) or (isinstance(items, str) and items.strip()))
    try:
        if with_plan:
            plan = tasks._text(plan, tasks.MAX_PLAN, "the plan", required=True)
            items = tasks._text(items, tasks.MAX_PLAN, "the items", required=True)
        row = tasks.create(db, project, account, assignee, body.get("title"), body.get("description"), moment,
                           status="plan_review" if with_plan else "open", plan=plan if with_plan else "",
                           items=items if with_plan else "", source=body.get("from"),
                           priority=body.get("priority"), depends_on=body.get("depends_on"))
    except tasks.TaskError as e:
        db.rollback()
        return _error(str(e), e.status)
    audit.record(db, "task.create", sessions.iso(moment), account_id=account["id"], project_id=project["id"],
                 detail={"number": row["number"], "via": "supervisor", "approval": "page" if with_plan else None})
    audit.record(db, "supervisor.task.add", sessions.iso(moment), account_id=account["id"], project_id=project["id"],
                 detail={"number": row["number"], "from": body.get("from").strip(), "assignee": assignee["email"],
                         "with_plan": with_plan})
    if with_plan:
        tasks.notify_plan(db, project, row, moment)
    db.commit()
    response = jsonify(task=tasks.public(row, moment, full=True))
    response.status_code = 201
    return response


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
    PLAN_ROUND_CAP earlier ones becomes `escalate`, and so does an approve or changes on a task the supervisor added
    itself (a person decides those). Repeating the call for a plan post that has a decision is a no-op."""
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
    if decision != "escalate" and row["created_by"] == account["id"]:
        converted = True                                     # it never decides on work it added itself
        decision = "escalate"
        reasons.append("the supervisor added this task: a person decides its plan")
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


MERGE_PHASES = ("decided", "done", "failed", "refused")


def merge_marks(db, project_id, number):
    """-> the task's `supervisor.merge.*` audit rows as (phase, detail), oldest first."""
    found = db.query("SELECT action, detail FROM audit WHERE project_id = ? AND action LIKE 'supervisor.merge.%' "
                     "ORDER BY at, id", (project_id,))
    out = []
    for row in found:
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("number") == number:
            out.append((row["action"].rsplit(".", 1)[1], detail))
    return out


@bp.post("/api/v1/supervisor/tasks/<int:number>/merge")
def merge_record(number):
    """The supervisor records a merge in phases, with `?project=<slug>` and `{"phase": "decided" | "done" | "failed" |
    "refused", "sha", "method", "reasons", "evidence", "merge_sha"}`. It merges nothing itself (the pass calls the forge
    between `decided` and `done`/`failed`); it writes one `supervisor.merge.<phase>` audit row per call, and `done` also
    sets `project_task.merged_at`. `decided` is refused (409) for a task already merged; `done` and `failed` need an
    earlier `decided` for the same SHA. A `refused` that repeats the previous row (same SHA and reasons) and a `done`
    for a merged task are no-ops (`already: true`)."""
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "merge records need a supervisor account")
    project = projects.get_by_slug(db, request.args.get("project"))
    if project is None:
        return _error("no such project", 404)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    phase = body.get("phase")
    if phase not in MERGE_PHASES:
        return _error('phase is "decided", "done", "failed" or "refused"', 400)
    row = tasks.get(db, project["id"], number)
    if row is None:
        return _error("no such task", 404)
    sha = body.get("sha") if isinstance(body.get("sha"), str) else None
    if phase != "refused" and not sha:
        return _error("sha is required", 400)
    moment = auth.now()
    reasons = _reasons(body.get("reasons"))
    marks = merge_marks(db, project["id"], number)
    if (phase == "refused" and marks and marks[-1][0] == "refused" and marks[-1][1].get("sha") == sha
            and marks[-1][1].get("reasons") == reasons):
        return jsonify(task=tasks.public(row, moment), phase=phase, already=True)
    if phase == "decided" and row["merged_at"]:
        return _error("task %d is already merged" % number, 409)
    if phase in ("done", "failed"):
        if not any(p == "decided" and d.get("sha") == sha for p, d in marks):
            return _error("no merge was decided for that SHA", 409)
        if phase == "done" and row["merged_at"]:
            return jsonify(task=tasks.public(row, moment), phase=phase, already=True)
    evidence = body.get("evidence") if isinstance(body.get("evidence"), dict) else {}
    merge_sha = body.get("merge_sha") if isinstance(body.get("merge_sha"), str) else None
    method = body.get("method") if isinstance(body.get("method"), str) else None
    if phase == "done":
        db.execute("UPDATE project_task SET merged_at = ? WHERE id = ?", (sessions.iso(moment), row["id"]))
        row = tasks.get(db, project["id"], number)
    audit.record(db, "supervisor.merge." + phase, sessions.iso(moment), account_id=account["id"], project_id=project["id"],
                 detail={"number": number, "sha": sha, "method": method, "reasons": reasons, "evidence": evidence,
                         "merge_sha": merge_sha})
    db.commit()
    return jsonify(task=tasks.public(row, moment), phase=phase, already=False)


@bp.put("/api/v1/supervisor/order")
def set_order():
    """The supervisor sets queue ranks, with `?project=<slug>` and `{"orders": [{"number", "rank", "reason"}]}`. Supervisor
    role only. Every number must be a task of the project and every rank a whole number of 1 or more, or nothing is
    written (400/404). A rank equal to the stored one is skipped; each changed rank is written with one
    `supervisor.order` audit row (detail: number, from, to, reason). Answers `{"changed": [numbers]}`, so repeating a
    call changes nothing."""
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "queue order needs a supervisor account")
    project = projects.get_by_slug(db, request.args.get("project"))
    if project is None:
        return _error("no such project", 404)
    body = request.get_json(silent=True)
    orders = body.get("orders") if isinstance(body, dict) else None
    if not isinstance(orders, list) or len(orders) > 1000:
        return _error('orders is a list of {"number", "rank", "reason"}', 400)
    wanted = {}
    for o in orders:
        number, rank = (o.get("number"), o.get("rank")) if isinstance(o, dict) else (None, None)
        if (not all(isinstance(v, int) and not isinstance(v, bool) for v in (number, rank)) or rank < 1
                or number in wanted):
            return _error("each order needs a distinct whole task number and a rank of 1 or more", 400)
        reason = o.get("reason")
        wanted[number] = (rank, reason.strip()[:300] if isinstance(reason, str) else "")
    rows = {}
    for number in wanted:
        rows[number] = tasks.get(db, project["id"], number)
        if rows[number] is None:
            return _error("no such task: %d" % number, 404)
    at = sessions.iso(auth.now())
    changed = []
    for number, (rank, reason) in wanted.items():
        if rows[number]["rank"] == rank:
            continue
        db.execute("UPDATE project_task SET rank = ? WHERE id = ?", (rank, rows[number]["id"]))
        audit.record(db, "supervisor.order", at, account_id=account["id"], project_id=project["id"],
                     detail={"number": number, "from": rows[number]["rank"], "to": rank, "reason": reason})
        changed.append(number)
    db.commit()
    return jsonify(changed=changed)


@bp.post("/api/v1/supervisor/runners/<runner_id>/<action>")
def control_runner(runner_id, action):
    """The supervisor pauses, resumes or wakes a runner, with `?project=<slug>`. Supervisor role only (403); an unknown
    runner or action is 404. Resume works only on a pause the supervisor made (403 on a person's pause or stop, 409 when
    nothing is paused). Pause is refused (409) on a runner asked to stop, and wake on one with a pause or stop pending.
    Repeating a call changes nothing. Each change writes a `supervisor.runner.<action>` audit row. Answers
    `{"runner", "changed"}`."""
    db = get_db()
    account = _caller(db)
    if account is None or account["role"] != accounts.SUPERVISOR:
        tokens._fail(403, "runner control needs a supervisor account")
    if action not in ("pause", "resume", "wake"):
        return _error("no such action", 404)
    project = projects.get_by_slug(db, request.args.get("project"))
    row = runners.by_id(db, project["id"], runner_id) if project else None
    if row is None:
        return _error("no such runner", 404)
    current = row["control"] or None
    if action == "resume":
        if current in ("pause", "stop") and row["control_role"] != accounts.SUPERVISOR:
            return _error("this runner was paused or stopped by a person; only a person can resume it", 403)
        if current not in ("pause", "stop"):
            return _error("this runner is not paused", 409)
    elif action == "pause" and current == "stop":
        return _error("this runner is asked to stop", 409)
    elif action == "wake" and current in ("pause", "stop"):
        return _error("this runner is asked to %s; waking it would override that" % current, 409)
    moment = auth.now()
    changed = runners.set_control(db, row, action, account, moment)
    if changed:
        audit.record(db, "supervisor.runner." + action, sessions.iso(moment), account_id=account["id"],
                     project_id=project["id"], detail={"label": row["label"], "owner": row["account_email"],
                                                       "state": row["state"]})
    db.commit()
    return jsonify(runner=runners.public(runners.by_id(db, project["id"], runner_id), moment), changed=changed)
