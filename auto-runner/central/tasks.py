"""Central tasks: the single source of what each person's runner should do next (decision record 0007, step 4).

A task belongs to a project and is **assigned to one person**. People add tasks for themselves; admins add them for
anyone. Every member sees every task of the project.

Life of a task (`status`):
  open --claim--> planning --plan posted--> plan_review --approve--> approved --claim--> building --report done--> done
                      ^                        | request changes                                 \\--report stopped: stays building
                      +---- changes_requested <+         reject --> rejected
A task typed in the terminal (`/auto-runner:auto <task>`, plan approved there) is recorded straight as `building`.

**Claims.** A runner takes its person's next task with `POST /api/v1/tasks/next` and gets a claim id; only that claim
may post a plan, beat or report. A claim stays alive while its runner sends a heartbeat (or any task call) within
CLAIM_TTL, and while its plan waits for a person (`plan_review`): waiting for a person never expires a claim. A silent
claim expires and the task can be taken again. An admin can release a claim or reassign the task; the runner then gets
409 on its next call and stops that task.

**Plan approval.** The plan (`plan`, the plan text; `items`, the queue items) is approved, sent back with a comment, or
rejected on the task page by the assignee or a project manager, owner or admin.

The task API takes personal tokens only (a task is someone's), with the project named as for every call.
"""
import datetime
import json
import secrets

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, url_for

from . import accounts, audit, auth, projects, sessions, tokens, ui
from .access import project_access
from .db import get_db

bp = Blueprint("tasks", __name__)

CLAIM_TTL = datetime.timedelta(hours=2)
STATUSES = ("open", "planning", "plan_review", "changes_requested", "approved", "building", "done", "rejected")
RUNNER_SIDE = ("planning", "changes_requested", "approved", "building")   # a silent claim on these expires
TAKE_ORDER = ("approved", "building", "changes_requested", "planning", "open")
APPROVER_ROLE = "manager"            # besides the assignee: managers, owners and admins approve plans
MAX_TITLE, MAX_TEXT, MAX_PLAN = 200, 20000, 200000
DESCRIBE = {"open": "open", "planning": "planning", "plan_review": "plan waiting for approval",
            "changes_requested": "changes requested", "approved": "approved, waiting to be built", "building": "building",
            "done": "done", "rejected": "rejected"}


class TaskError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _at(moment):
    return sessions.iso(moment)


def _text(value, limit, what, required=False):
    text = (value or "").strip() if isinstance(value, str) else ""
    if required and not text:
        raise TaskError("%s is required" % what)
    if len(text) > limit:
        raise TaskError("%s is longer than %d characters" % (what, limit))
    return text


def claim_live(row, moment):
    """True while the task's claim holds: a plan waiting for a person, or a heartbeat within CLAIM_TTL."""
    if not row["claim_id"]:
        return False
    if row["status"] == "plan_review":
        return True
    beat = sessions.parse_iso(row["heartbeat_at"]) if row["heartbeat_at"] else None
    return beat is not None and moment - beat < CLAIM_TTL


def get(db, project_id, number):
    return db.query_one("SELECT t.*, a.email AS assignee_email FROM project_task t JOIN account a ON a.id = t.assignee_id"
                        " WHERE t.project_id = ? AND t.number = ?", (project_id, number))


def for_project(db, project_id):
    return db.query("SELECT t.*, a.email AS assignee_email FROM project_task t JOIN account a ON a.id = t.assignee_id"
                    " WHERE t.project_id = ? ORDER BY t.number DESC", (project_id,))


def _can_run(db, project, account):
    return projects.allows(projects.effective_role(db, project, account), "developer")


def create(db, project, by, assignee, title, description, moment, status="open", plan="", items="", runner=None):
    """Add a task and commit -> its row. People add tasks for themselves; an admin for any member who can run runners."""
    title = _text(title, MAX_TITLE, "the title", required=True)
    description = _text(description, MAX_TEXT, "the description")
    if assignee["id"] != by["id"] and by["role"] != projects.ADMIN:
        raise TaskError("only an admin adds tasks for someone else", 403)
    if not _can_run(db, project, assignee):
        raise TaskError("%s is not a developer or above in this project, so no runner of theirs can take it" % assignee["email"])
    at = _at(moment)
    for _ in range(3):                                     # the number is per project; a clash just takes the next one
        number = (db.query_one("SELECT MAX(number) AS n FROM project_task WHERE project_id = ?", (project["id"],))["n"] or 0) + 1
        claim = secrets.token_urlsafe(16) if runner else None
        try:
            db.execute("INSERT INTO project_task (id, project_id, number, title, description, assignee_id, created_by, "
                       "status, plan, items, claim_id, claim_runner, heartbeat_at, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (secrets.token_hex(16), project["id"], number, title, description, assignee["id"], by["id"],
                        status, plan or None, items or None, claim, runner, at if runner else None, at, at))
        except Exception:
            db.rollback()
            continue
        db.commit()
        return get(db, project["id"], number)
    raise TaskError("could not number the task; try again", 409)


def take_next(db, project, account, runner, moment):
    """Claim the account's next task in this project for `runner` and commit -> (row, claim id) or (None, None).
    Order: approved (build it), building (resume), changes requested, planning, open; oldest first within each."""
    rows = db.query("SELECT * FROM project_task WHERE project_id = ? AND assignee_id = ? AND status IN (%s)"
                    " ORDER BY number" % ", ".join("?" * len(TAKE_ORDER)), (project["id"], account["id"]) + TAKE_ORDER)
    at = _at(moment)
    for status in TAKE_ORDER:
        for row in [r for r in rows if r["status"] == status]:
            if claim_live(row, moment) and row["claim_runner"] != runner:
                continue                                   # another runner of this person holds it
            claim = secrets.token_urlsafe(16)
            new_status = {"open": "planning", "approved": "building"}.get(status, status)
            cur = db.execute("UPDATE project_task SET claim_id = ?, claim_runner = ?, heartbeat_at = ?, status = ?,"
                             " updated_at = ? WHERE id = ? AND (claim_id IS NULL OR claim_id = ? OR heartbeat_at IS NULL"
                             " OR heartbeat_at < ? OR claim_runner = ?)",
                             (claim, runner, at, new_status, at, row["id"], row["claim_id"] or "",
                              _at(moment - CLAIM_TTL), runner))
            if cur.rowcount == 1:
                db.commit()
                return get(db, project["id"], row["number"]), claim
    db.commit()
    return None, None


def _held(db, project, number, claim, moment):
    row = get(db, project["id"], number)
    if row is None:
        raise TaskError("no such task", 404)
    if not claim or row["claim_id"] != claim or not claim_live(row, moment):
        raise TaskError("this runner no longer holds task %d (released, reassigned or expired): stop working on it" % number, 409)
    return row


def beat(db, project, number, claim, moment):
    row = _held(db, project, number, claim, moment)
    db.execute("UPDATE project_task SET heartbeat_at = ? WHERE id = ?", (_at(moment), row["id"]))
    db.commit()
    return get(db, project["id"], number)


def post_plan(db, project, number, claim, plan, items, moment):
    row = _held(db, project, number, claim, moment)
    if row["status"] not in ("planning", "changes_requested"):
        raise TaskError("task %d is %s; a plan is posted while planning" % (number, row["status"]), 409)
    plan = _text(plan, MAX_PLAN, "the plan", required=True)
    items = _text(items, MAX_PLAN, "the items", required=True)
    db.execute("UPDATE project_task SET plan = ?, items = ?, status = 'plan_review', heartbeat_at = ?, updated_at = ?"
               " WHERE id = ?", (plan, items, _at(moment), _at(moment), row["id"]))
    db.commit()
    return get(db, project["id"], number)


def report(db, project, number, claim, result, branch, pr_url, note, moment):
    row = _held(db, project, number, claim, moment)
    if row["status"] != "building":
        raise TaskError("task %d is %s; only a task being built is reported" % (number, row["status"]), 409)
    if result not in ("done", "stopped"):
        raise TaskError("result must be done or stopped")
    pr_url = pr_url if isinstance(pr_url, str) and pr_url.startswith("https://") else None
    db.execute("UPDATE project_task SET status = ?, branch = COALESCE(?, branch), pr_url = COALESCE(?, pr_url), note = ?,"
               " heartbeat_at = ?, updated_at = ?, claim_id = CASE WHEN ? = 'done' THEN NULL ELSE claim_id END"
               " WHERE id = ?", ("done" if result == "done" else "building", _text(branch, 300, "the branch") or None,
                                 pr_url, _text(note, 2000, "the note") or None, _at(moment), _at(moment), result, row["id"]))
    db.commit()
    return get(db, project["id"], number)


def can_decide(db, project, row, account):
    return row["assignee_id"] == account["id"] or projects.allows(projects.effective_role(db, project, account), APPROVER_ROLE)


def decide(db, project, row, account, decision, comment, moment):
    if row["status"] != "plan_review":
        raise TaskError("task %d has no plan waiting for approval" % row["number"], 409)
    if not can_decide(db, project, row, account):
        raise TaskError("only the assignee or a manager, owner or admin decides on a plan", 403)
    comment = _text(comment, MAX_TEXT, "the comment")
    if decision == "changes" and not comment:
        raise TaskError("say what should change")
    status = {"approve": "approved", "changes": "changes_requested", "reject": "rejected"}.get(decision)
    if status is None:
        raise TaskError("unknown decision")
    at = _at(moment)
    # approved/changes: the runner keeps its claim and gets CLAIM_TTL from now to come back; rejected: the claim ends
    db.execute("UPDATE project_task SET status = ?, decision_comment = ?, decided_by = ?, decided_at = ?, heartbeat_at = ?,"
               " updated_at = ?, claim_id = CASE WHEN ? = 'rejected' THEN NULL ELSE claim_id END WHERE id = ?",
               (status, comment or None, account["id"], at, at, at, status, row["id"]))
    db.commit()
    return get(db, project["id"], row["number"])


def release(db, project, row, assignee, moment):
    """An admin ends the claim (and, with `assignee`, gives the task to someone else). Work in progress goes back to
    the step it was at: planning -> open, building -> approved."""
    at = _at(moment)
    status = {"planning": "open", "building": "approved"}.get(row["status"], row["status"])
    if assignee is not None and status in ("plan_review", "changes_requested"):
        status = "open"                                     # a new person plans it afresh
    db.execute("UPDATE project_task SET claim_id = NULL, claim_runner = NULL, heartbeat_at = NULL, status = ?,"
               " assignee_id = ?, updated_at = ? WHERE id = ?",
               (status, assignee["id"] if assignee else row["assignee_id"], at, row["id"]))
    db.commit()
    return get(db, project["id"], row["number"])


def public(row, moment, full=False):
    """The task as the API shows it (never the claim id of someone else's claim)."""
    out = {"number": row["number"], "title": row["title"], "status": row["status"], "assignee": row["assignee_email"],
           "runner": row["claim_runner"] if claim_live(row, moment) else None, "branch": row["branch"],
           "pr_url": row["pr_url"], "updated_at": row["updated_at"]}
    if full:
        out.update(description=row["description"] or "", plan=row["plan"] or "", items=row["items"] or "",
                   comment=row["decision_comment"] or "", note=row["note"] or "")
    return out


# ---------- API (personal tokens) ----------

def _api_account():
    account = g.get("token_account")
    if account is None:
        tokens._fail(403, "the task API needs a personal token: sign this computer in with /auto-runner:central")
    return account


def _api(fn):
    try:
        return fn()
    except TaskError as e:
        response = jsonify(error=str(e))
        response.status_code = e.status
        return response


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.get("/api/v1/tasks")
@tokens.bearer_token("pull")
def api_list():
    _api_account()
    moment = auth.now()
    return jsonify(tasks=[public(r, moment) for r in for_project(get_db(), g.token_project["id"])])


@bp.get("/api/v1/tasks/<int:number>")
@tokens.bearer_token("pull")
def api_get(number):
    _api_account()
    row = get(get_db(), g.token_project["id"], number)
    if row is None:
        response = jsonify(error="no such task")
        response.status_code = 404
        return response
    return jsonify(task=public(row, auth.now(), full=True))


@bp.post("/api/v1/tasks/next")
@tokens.bearer_token("pull")
def api_next():
    account, moment = _api_account(), auth.now()
    runner = _text(_body().get("runner"), 200, "the runner") or "a runner"
    db = get_db()
    row, claim = take_next(db, g.token_project, account, runner, moment)
    if row is None:
        return jsonify(task=None)
    audit.record(db, "task.claim", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                 detail={"number": row["number"], "runner": runner})
    db.commit()
    return jsonify(task=public(row, moment, full=True), claim=claim,
                   action="build" if row["status"] == "building" else "plan")


@bp.post("/api/v1/tasks")
@tokens.bearer_token("ingest")
def api_record():
    """A task typed in the terminal, its plan approved there: recorded as `building`, claimed by this runner."""
    account, moment, body = _api_account(), auth.now(), _body()

    def run():
        db = get_db()
        runner = _text(body.get("runner"), 200, "the runner") or "a runner"
        row = create(db, g.token_project, account, account, body.get("title"), body.get("description"), moment,
                     status="building", plan=_text(body.get("plan"), MAX_PLAN, "the plan"),
                     items=_text(body.get("items"), MAX_PLAN, "the items"), runner=runner)
        audit.record(db, "task.create", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                     detail={"number": row["number"], "via": "terminal"})
        db.commit()
        return jsonify(task=public(row, moment, full=True), claim=row["claim_id"])
    return _api(run)


@bp.post("/api/v1/tasks/<int:number>/plan")
@tokens.bearer_token("ingest")
def api_plan(number):
    account, body = _api_account(), _body()

    def run():
        db, moment = get_db(), auth.now()
        row = post_plan(db, g.token_project, number, body.get("claim"), body.get("plan"), body.get("items"), moment)
        audit.record(db, "task.plan.post", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                     detail={"number": number})
        db.commit()
        return jsonify(task=public(row, moment))
    return _api(run)


@bp.post("/api/v1/tasks/<int:number>/heartbeat")
@tokens.bearer_token("ingest")
def api_beat(number):
    _api_account()
    return _api(lambda: jsonify(task=public(beat(get_db(), g.token_project, number, _body().get("claim"), auth.now()),
                                            auth.now())))


@bp.post("/api/v1/tasks/<int:number>/report")
@tokens.bearer_token("ingest")
def api_report(number):
    account, b = _api_account(), _body()

    def run():
        db, moment = get_db(), auth.now()
        row = report(db, g.token_project, number, b.get("claim"), b.get("result"), b.get("branch"), b.get("pr_url"),
                     b.get("note"), moment)
        audit.record(db, "task.report", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                     detail={"number": number, "result": row["status"] if row["status"] == "done" else "stopped"})
        db.commit()
        return jsonify(task=public(row, moment))
    return _api(run)


# ---------- pages ----------

def _runners(db, project):
    """Members who can run runners (developer and above), for the admin's assignee choice."""
    return [m for m in projects.members(db, project["id"]) if projects.allows(m["role"], "developer") and not m["disabled"]]


STATES = (("approval", "Needs approval", ("plan_review",)),
          ("progress", "In progress", ("planning", "changes_requested", "approved", "building")),
          ("open", "Open", ("open",)),
          ("done", "Done", ("done", "rejected")))


def _can_add(db):
    return _can_run(db, g.project, g.account) or g.account["role"] == projects.ADMIN


def _list_page():
    """The Tasks tab: state tabs with counts, "Mine only" and a title search, all plain links (`?state=`, `?mine=1`,
    `?q=`). With no state chosen it opens on the first tab that has tasks."""
    db, moment = get_db(), auth.now()
    mine, q = request.args.get("mine") == "1", (request.args.get("q") or "").strip()[:MAX_TITLE]
    rows = [dict(r, live=claim_live(r, moment)) for r in for_project(db, g.project["id"])]
    rows = [r for r in rows if (not mine or r["assignee_id"] == g.account["id"]) and (not q or q.lower() in r["title"].lower())]
    tabs = [{"key": k, "label": label, "rows": [r for r in rows if r["status"] in statuses]} for k, label, statuses in STATES]
    state = request.args.get("state")
    if state not in [t["key"] for t in tabs]:
        state = next((t["key"] for t in tabs if t["rows"]), "approval")
    shown = next(t["rows"] for t in tabs if t["key"] == state)
    building = [r for r in shown if r["status"] == "building" or r["live"]]
    if building:
        from . import runs as _runs
        task_runs = _runs.summarise(_runs.project_events(db, g.project["id"]), moment)
        for r in building:
            r["progress"] = _runs.task_progress([x for x in task_runs if x["task"] == r["number"]], r["items"])
    return render_template("tasks.html", project=g.project, tabs=tabs, state=state, tasks=shown, mine=mine, q=q,
                           any_tasks=bool(for_project(db, g.project["id"])), can_add=_can_add(db))


def _new_page(error=None, status=200, form=None):
    db = get_db()
    if error is None and not _can_add(db):
        abort(403)
    is_admin = g.account["role"] == projects.ADMIN
    return render_template("task_new.html", project=g.project, error=error, form=form or {}, is_admin=is_admin,
                           assignees=_runners(db, g.project) if is_admin else []), status


@bp.get("/projects/<slug>/tasks")
@project_access("viewer")
def tasks_page(slug):
    return _list_page()


@bp.get("/projects/<slug>/tasks/new")
@project_access("viewer")
def task_new(slug):
    return _new_page()


def with_done_when(description, done_when):
    """The task's text as stored: "How we know it's done" becomes a "Done when:" section of the description."""
    description, done_when = (description or "").strip(), (done_when or "").strip()
    if not done_when:
        return description
    return (description + "\n\n" if description else "") + "Done when:\n" + done_when


@bp.post("/projects/<slug>/tasks")
@project_access("viewer")
def task_add(slug):
    db, moment = get_db(), auth.now()
    assignee = g.account
    chosen = (request.form.get("assignee") or "").strip()
    if chosen and chosen != g.account["id"]:
        assignee = accounts.get(db, chosen)
        if assignee is None:
            abort(404)
    description = with_done_when(request.form.get("description"), request.form.get("done_when"))
    try:
        row = create(db, g.project, g.account, assignee, request.form.get("title"), description, moment)
    except TaskError as e:
        return _new_page(error=str(e), status=e.status,
                         form={"title": (request.form.get("title") or "")[:MAX_TITLE],
                               "description": (request.form.get("description") or "")[:MAX_TEXT],
                               "done_when": (request.form.get("done_when") or "")[:MAX_TEXT], "assignee": chosen})
    audit.record(db, "task.create", _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                 detail={"number": row["number"], "assignee": row["assignee_email"]})
    db.commit()
    return redirect(url_for("tasks.task_page", slug=slug, number=row["number"]))


STEPS = (("open", "Open"), ("planned", "Planned"), ("approval", "Approval"), ("building", "Building"), ("done", "Done"))
STEP_OF = {"open": 0, "planning": 1, "changes_requested": 1, "plan_review": 2, "approved": 3, "building": 3, "done": 4}


def _activity(db, project, number, task_runs):
    """The task's history, newest first: what people and runners did (audit rows) and each run's start and end."""
    from . import shell
    rows = db.query("SELECT a.*, acc.email, acc.name FROM audit a LEFT JOIN account acc ON acc.id = a.account_id"
                    " WHERE a.project_id = ? AND a.action LIKE 'task.%' ORDER BY a.at DESC, a.id DESC LIMIT 400",
                    (project["id"],))
    out = []
    for r in rows:
        d = r["detail"]
        try:
            d = json.loads(d) if isinstance(d, str) else (d or {})
        except ValueError:
            d = {}
        if d.get("number") != number:
            continue
        s = shell._sentence(r, project["slug"])
        if s:
            out.append({"text": s["text"].replace("task #%d" % number, "this task"), "at": r["at"],
                        "comment": None})
    for run in task_runs:
        who = run["user"] or "a runner"
        out.append({"text": "Run started by %s%s" % (who, " on " + run["branch_short"] if run["branch_short"] else ""),
                    "at": run["started"], "comment": None})
        if run["ended"]:
            out.append({"text": "Run %s" % ui.reason_text(run["reason"]) if run["state"] == "stopped"
                        else "Run finished (%d item%s done)" % (run["done"], "s" if run["done"] != 1 else ""),
                        "at": run["ended"], "comment": None})
    out.sort(key=lambda e: e["at"] or "", reverse=True)
    return out[:30]


def _detail(number, error=None, status=200):
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    from . import runs as _runs
    task_runs = _runs.for_task(db, g.project["id"], number, moment)
    progress = _runs.task_progress(task_runs, row["items"])
    states = (progress or {}).get("states", {})
    items = [dict(i, state="done" if i["ticked"] and i["id"] not in states else states.get(i["id"], "queued"))
             for i in _runs.parse_items(row["items"])]
    live = claim_live(row, moment)
    page = render_template("task.html", project=g.project, task=row, describe=DESCRIBE.get(row["status"], row["status"]),
                           runs=task_runs, progress=progress, items=items, steps=STEPS,
                           step=STEP_OF.get(row["status"]), stale_hours=int(_runs.STALE.total_seconds() // 3600),
                           live=live, quiet=bool(row["claim_id"]) and not live and row["status"] in RUNNER_SIDE,
                           can_decide=row["status"] == "plan_review" and can_decide(db, g.project, row, g.account),
                           is_admin=g.account["role"] == projects.ADMIN, assignees=_runners(db, g.project),
                           activity=_activity(db, g.project, number, task_runs), error=error,
                           ttl_hours=int(CLAIM_TTL.total_seconds() // 3600))
    return page, status


@bp.get("/projects/<slug>/tasks/<int:number>")
@project_access("viewer")
def task_page(slug, number):
    return _detail(number)


@bp.post("/projects/<slug>/tasks/<int:number>/decide")
@project_access("viewer")
def task_decide(slug, number):
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    decision = request.form.get("decision", "")
    if decision == "reject" and row["status"] == "plan_review" and not ui.confirmed():
        return ui.confirm_page("Reject task #%d %s?" % (number, row["title"]),
                               "The task closes and its runner lets it go. To change the plan instead, request changes.",
                               "Reject task", url_for("tasks.task_page", slug=slug, number=number))
    try:
        decide(db, g.project, row, g.account, decision, request.form.get("comment"), moment)
    except TaskError as e:
        return _detail(number, error=str(e), status=e.status)
    audit.record(db, "task.plan.%s" % decision, _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                 detail={"number": number})
    db.commit()
    return redirect(ui.safe_next(request.form.get("next")) or url_for("tasks.task_page", slug=slug, number=number))


@bp.post("/projects/<slug>/tasks/<int:number>/release")
@project_access("viewer")
def task_release(slug, number):
    """Admins only: end the claim, and optionally give the task to another member."""
    if g.account["role"] != projects.ADMIN:
        abort(404)
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    chosen = (request.form.get("assignee") or "").strip()
    if not ui.confirmed():
        to = accounts.get(db, chosen) if chosen and chosen != row["assignee_id"] else None
        return ui.confirm_page(("Give task #%d to %s?" % (number, to["email"])) if to else "Take task #%d back from its runner?" % number,
                               "The runner working on it stops at its next step. Work goes back one step (planning to open, "
                               "building to approved)" + ("; the new person plans it afresh." if to else "."),
                               "Give it to them" if to else "Take it back", url_for("tasks.task_page", slug=slug, number=number))
    assignee = accounts.get(db, chosen) if chosen and chosen != row["assignee_id"] else None
    if chosen and chosen != row["assignee_id"] and (assignee is None or not _can_run(db, g.project, assignee)):
        return _detail(number, error="choose a member who is a developer or above", status=400)
    release(db, g.project, row, assignee, moment)
    audit.record(db, "task.reassign" if assignee else "task.release", _at(moment), account_id=g.account["id"],
                 project_id=g.project["id"], detail={"number": number, "runner": row["claim_runner"],
                                                     "to": assignee["email"] if assignee else None})
    db.commit()
    return redirect(url_for("tasks.task_page", slug=slug, number=number))
