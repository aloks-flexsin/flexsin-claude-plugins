"""Central tasks: the single source of what each person's runner should do next (decision record 0007, step 4).

A task belongs to a project and is **assigned to one person**, or to nobody (the project's **pool**). People add tasks
for themselves or for the pool; admins add them for anyone. Every member sees every task of the project.

Life of a task (`status`):
  open --claim--> planning --plan posted--> plan_review --approve--> approved --claim--> building --report done--> done
                      ^                        | request changes             ^                       |
                      +---- changes_requested <+         reject --> rejected  +-- report stopped ----+ (claim released)
A merge the supervisor records (`supervisor.merge.done`) also makes the task `done`.

**Pool.** A runner takes its own person's tasks first, then the pool: unassigned tasks, and other members' tasks whose
person has no active runner (no beat within runners.SILENT_AFTER) or whose claim expired. Taking one from the pool makes
that runner's person the assignee (`task.take` audit row). On the task page a developer or above can **Take it**; the
assignee, a manager or above, and the supervisor (API) can **Release** the claim or **Reassign** the task (to a member or
back to the pool).
A task typed in the terminal (`/auto-runner:auto <task>`, plan approved there) is recorded straight as `queued` (its run has not started; the first heartbeat without a state makes it `building`);
a plan proposed from the terminal (`"approval": "page"`) is recorded as `plan_review`, claimed, and approved on the page.

**Claims.** A runner takes its person's next task with `POST /api/v1/tasks/next` and gets a claim id; only that claim
may post a plan, beat or report. A claim stays alive while its runner sends a heartbeat (or any task call) within
CLAIM_TTL, and while its plan waits for a person (`plan_review`): waiting for a person never expires a claim. A silent
claim expires and the task can be taken again. When a claim is released or the task reassigned, the runner gets 409 on
its next call and stops that task.

**Plan approval.** The plan (`plan`, the plan text; `items`, the queue items) is approved, sent back with a comment, or
rejected on the task page by the assignee or a project manager, owner or admin.

The task API takes personal tokens only (a task is someone's), with the project named as for every call.
"""
import datetime
import json
import re
import secrets

from flask import Blueprint, abort, g, jsonify, make_response, redirect, render_template, request, url_for

from . import accounts, audit, auth, frictions, identities, notifications, projects, sessions, tokens, ui
from .access import project_access
from .db import get_db

bp = Blueprint("tasks", __name__)

CLAIM_TTL = datetime.timedelta(hours=2)
STATUSES = ("open", "planning", "plan_review", "changes_requested", "approved", "queued", "building", "done", "rejected")
RUNNER_SIDE = ("planning", "changes_requested", "approved", "building")   # a silent claim on these expires
TAKE_ORDER = ("approved", "building", "changes_requested", "planning", "open")
APPROVER_ROLE = "manager"            # besides the assignee: managers, owners and admins approve plans
MAX_TITLE, MAX_TEXT, MAX_PLAN = 200, 20000, 200000
DESCRIBE = {"open": "open", "planning": "planning", "plan_review": "plan waiting for approval",
            "changes_requested": "changes requested", "approved": "approved, waiting to be built", "queued": "queued: waits for its run to start",
            "building": "building",
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
    if row["status"] in ("plan_review", "queued"):
        return True                                        # waiting for a person or for its run to start
    beat = sessions.parse_iso(row["heartbeat_at"]) if row["heartbeat_at"] else None
    return beat is not None and moment - beat < CLAIM_TTL


def get(db, project_id, number):
    return db.query_one("SELECT t.*, a.email AS assignee_email FROM project_task t LEFT JOIN account a ON a.id = t.assignee_id"
                        " WHERE t.project_id = ? AND t.number = ?", (project_id, number))


def for_project(db, project_id):
    return db.query("SELECT t.*, a.email AS assignee_email FROM project_task t LEFT JOIN account a ON a.id = t.assignee_id"
                    " WHERE t.project_id = ? ORDER BY t.number DESC", (project_id,))


def _can_run(db, project, account):
    if account["role"] == accounts.SUPERVISOR:             # a supervisor never runs tasks
        return False
    return projects.allows(projects.effective_role(db, project, account), "developer")


MAX_SOURCE = 500
MAX_DEPENDS = 50


def parse_priority(value):
    """The priority as an integer (lower is more urgent) or None when unset. Accepts an int or a numeric string."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TaskError("the priority is a whole number")
    try:
        number = int(value.strip() if isinstance(value, str) else value)
    except ValueError:
        raise TaskError("the priority is a whole number")
    if abs(number) > 1000000:
        raise TaskError("the priority is a whole number between -1000000 and 1000000")
    return number


def parse_depends(value):
    """The task numbers a task waits for, as a sorted list without repeats; [] when unset. Accepts a list of ints or a
    string such as "3, 4" (the form)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    if isinstance(value, str):
        parts = [p for p in re.split(r"[\s,;]+", value.replace("#", " ").strip()) if p]
    elif isinstance(value, list):
        parts = value
    else:
        raise TaskError("depends_on is a list of task numbers")
    numbers = set()
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, (int, str)) or not str(part).strip().isdigit():
            raise TaskError("depends_on is a list of task numbers; %r is not one" % (part,))
        numbers.add(int(part))
    if len(numbers) > MAX_DEPENDS:
        raise TaskError("a task depends on at most %d others" % MAX_DEPENDS)
    return sorted(numbers)


def depends_of(row):
    """The task numbers of `row`'s depends_on (stored as JSON text); [] when none or unreadable."""
    try:
        value = json.loads(row["depends_on"]) if row["depends_on"] else []
    except ValueError:
        return []
    return [n for n in value if isinstance(n, int)] if isinstance(value, list) else []


def create(db, project, by, assignee, title, description, moment, status="open", plan="", items="", runner=None,
           priority=None, depends_on=None, source=None):
    """Add a task and commit -> its row. People add tasks for themselves; an admin for any member who can run runners.
    `priority` (an integer, lower is more urgent) and `depends_on` (task numbers of this project) are optional.
    A supervisor (decision record 0010, 6a) adds tasks for a member who can run runners, and only with `source`: what
    the task traces to (a review, a plan's "Needs a person", a failing check, a person's request). It never invents scope.
    `assignee` None puts the task in the project's pool: the first runner of a developer or above takes it (take_next).
    Anyone who may add tasks (a developer or above, an admin, the supervisor) may add one to the pool."""
    title = _text(title, MAX_TITLE, "the title", required=True)
    description = _text(description, MAX_TEXT, "the description")
    if by["role"] == accounts.SUPERVISOR:
        source = _text(source, MAX_SOURCE, "what the task comes from (source)", required=True)
        description = ((description + "\n\n") if description else "") + "From: " + source
        if len(description) > MAX_TEXT:
            raise TaskError("the description is longer than %d characters" % MAX_TEXT)
    elif assignee is None:
        if not (_can_run(db, project, by) or by["role"] == projects.ADMIN):
            raise TaskError("only a developer or above adds a task to the pool", 403)
    elif assignee["id"] != by["id"] and by["role"] != projects.ADMIN:
        raise TaskError("only an admin adds tasks for someone else", 403)
    if assignee is not None and not _can_run(db, project, assignee):
        raise TaskError("%s is not a developer or above in this project, so no runner of theirs can take it" % assignee["email"])
    if assignee is None and runner:
        raise TaskError("a task a runner holds has an assignee")
    priority, depends = parse_priority(priority), parse_depends(depends_on)
    at = _at(moment)
    for _ in range(3):                                     # the number is per project; a clash just takes the next one
        number = (db.query_one("SELECT MAX(number) AS n FROM project_task WHERE project_id = ?", (project["id"],))["n"] or 0) + 1
        if number in depends:
            raise TaskError("a task cannot depend on itself (task %d)" % number)
        if depends:
            known = {r["number"] for r in db.query("SELECT number FROM project_task WHERE project_id = ?", (project["id"],))}
            missing = [n for n in depends if n not in known]
            if missing:
                raise TaskError("no such task to depend on: %s" % ", ".join("#%d" % n for n in missing))
        claim = secrets.token_urlsafe(16) if runner else None
        try:
            db.execute("INSERT INTO project_task (id, project_id, number, title, description, assignee_id, created_by, "
                       "status, plan, items, claim_id, claim_runner, heartbeat_at, created_at, updated_at, priority, depends_on) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (secrets.token_hex(16), project["id"], number, title, description,
                        assignee["id"] if assignee else None, by["id"],
                        status, plan or None, items or None, claim, runner, at if runner else None, at, at,
                        priority, json.dumps(depends) if depends else None))
        except Exception:
            db.rollback()
            continue
        db.commit()
        return get(db, project["id"], number)
    raise TaskError("could not number the task; try again", 409)


def _order(rows):
    """By status (TAKE_ORDER), then priority (lower first, unset last), rank (unset last) and number."""
    return sorted(rows, key=lambda r: (TAKE_ORDER.index(r["status"]), r["priority"] is None, r["priority"] or 0,
                                       r["rank"] is None, r["rank"] or 0, r["number"]))


def pool_reason(db, project, row, account, moment):
    """Why `account`'s runner may take `row` from the pool -> "unassigned", "no active runner" (its person has no runner
    that beat within runners.SILENT_AFTER), "claim expired" (its runner went quiet), or None when it may not."""
    from . import runners                                   # runners does not import this module; kept local anyway
    if row["assignee_id"] == account["id"]:
        return None
    if row["assignee_id"] is None:
        return "unassigned"
    if not runners.watcher_active(db, project["id"], row["assignee_id"], moment):
        return "no active runner"
    if row["claim_id"] and not claim_live(row, moment):
        return "claim expired"
    return None


def take_next(db, project, account, runner, moment):
    """Claim the account's next task in this project for `runner` and commit -> (row, claim id) or (None, None).
    Own tasks first; then the project's pool (decision: shared task pool): unassigned tasks, and other members' tasks
    whose person has no active runner or whose claim expired (pool_reason). Taking one from the pool makes the account
    its assignee and writes a `task.take` audit row (from, to, reason). Only a developer or above takes from the pool.
    Order within each group: approved (build it), building (resume), changes requested, planning, open; within each status
    by priority (lower first, unset last), then rank (unset last), then number. A task is skipped while a task it depends
    on is not done, and while another runner holds a live claim on it."""
    rows = db.query("SELECT * FROM project_task WHERE project_id = ? AND status IN (%s) ORDER BY number"
                    % ", ".join("?" * len(TAKE_ORDER)), (project["id"],) + TAKE_ORDER)
    own = _order([r for r in rows if r["assignee_id"] == account["id"]])
    pool = []
    if _can_run(db, project, account):
        for r in _order([r for r in rows if r["assignee_id"] != account["id"]]):
            reason = pool_reason(db, project, r, account, moment)
            if reason:
                pool.append((r, reason))
    waiting = {n for r in own + [p[0] for p in pool] for n in depends_of(r)}
    unfinished = set()
    if waiting:
        unfinished = {r["number"] for r in db.query("SELECT number, status FROM project_task WHERE project_id = ?",
                                                    (project["id"],)) if r["number"] in waiting and r["status"] != "done"}
    at = _at(moment)
    for row, reason in [(r, None) for r in own] + pool:
        if unfinished and unfinished.intersection(depends_of(row)):
            continue                                       # waits for a task that is not done
        if claim_live(row, moment) and row["claim_runner"] != runner:
            continue                                       # another runner holds it
        status = row["status"]
        claim = secrets.token_urlsafe(16)
        new_status = {"open": "planning", "approved": "building"}.get(status, status)
        same_owner = "assignee_id IS NULL" if row["assignee_id"] is None else "assignee_id = ?"
        cur = db.execute("UPDATE project_task SET claim_id = ?, claim_runner = ?, heartbeat_at = ?, status = ?,"
                         " assignee_id = ?,"
                         " note = CASE WHEN ? = 'building' AND ? <> 'building' THEN NULL ELSE note END,"
                         " updated_at = ? WHERE id = ? AND status = ? AND " + same_owner +
                         " AND (claim_id IS NULL OR claim_id = ? OR heartbeat_at IS NULL"
                         " OR heartbeat_at < ? OR claim_runner = ?)",
                         (claim, runner, at, new_status, account["id"], new_status, status, at, row["id"], status)
                         + ((row["assignee_id"],) if row["assignee_id"] is not None else ())
                         + (row["claim_id"] or "", _at(moment - CLAIM_TTL), runner))
        if cur.rowcount == 1:
            if reason:
                previous = accounts.get(db, row["assignee_id"]) if row["assignee_id"] else None
                audit.record(db, "task.take", at, account_id=account["id"], project_id=project["id"],
                             detail={"number": row["number"], "from": previous["email"] if previous else None,
                                     "to": account["email"], "reason": reason, "runner": runner, "via": "runner"})
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


MAX_WAIT_NOTE = 300


def beat(db, project, number, claim, moment, state=None, note=None):
    """Keep the claim alive. `state` "queued" moves an approved or building task to queued; "building" (or no state on
    a queued task) moves queued to building. `note` (at most MAX_WAIT_NOTE characters) says why a queued task waits; it is
    kept only while the task is queued and cleared when it moves to building."""
    row = _held(db, project, number, claim, moment)
    if state not in (None, "queued", "building"):
        raise TaskError('state is "queued" or "building"')
    if note is not None and not isinstance(note, str):
        raise TaskError("the note must be text")
    note = (note or "").strip()
    if len(note) > MAX_WAIT_NOTE:
        raise TaskError("the note is at most %d characters" % MAX_WAIT_NOTE)
    status = row["status"]
    if state == "queued" and status in ("approved", "building"):
        status = "queued"
    elif status == "queued" and state in (None, "building"):
        status = "building"
    kept = row["note"]
    if status == "building" and row["status"] == "queued":
        kept = None                                         # it started: the waiting note is stale
    elif status == "queued" and note:
        kept = note
    db.execute("UPDATE project_task SET heartbeat_at = ?, status = ?, note = ?, updated_at = ? WHERE id = ?",
               (_at(moment), status, kept, _at(moment) if status != row["status"] else row["updated_at"], row["id"]))
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
    notifications.resolve(db, notifications.task_key(project, number, "changes"), moment)
    notify_plan(db, project, row, moment)
    db.commit()
    return get(db, project["id"], number)


def notify_plan(db, project, row, moment):
    """A plan waits for approval: tell the assignee and the project's managers and owners. Does not commit."""
    who = [row["assignee_id"]] + notifications.members_with(db, project, APPROVER_ROLE)
    notifications.notify(db, who, project, "plan", notifications.task_text("plan", row),
                         notifications.task_link(project, row["number"]),
                         notifications.task_key(project, row["number"], "plan"), moment)


def notify_assigned(db, project, row, moment):
    """The task was given to `row["assignee_id"]` by someone else. Does not commit."""
    notifications.notify(db, [row["assignee_id"]], project, "assigned", notifications.task_text("assigned", row),
                         notifications.task_link(project, row["number"]),
                         notifications.task_key(project, row["number"], "assigned"), moment)


def report(db, project, number, claim, result, branch, pr_url, note, moment):
    row = _held(db, project, number, claim, moment)
    if row["status"] not in ("building", "queued"):
        raise TaskError("task %d is %s; only a task being built is reported" % (number, row["status"]), 409)
    if result not in ("done", "stopped"):
        raise TaskError("result must be done or stopped")
    pr_url = pr_url if isinstance(pr_url, str) and pr_url.startswith("https://") else None
    # done ends the claim; stopped releases it too (the task goes back to approved, branch and pull request kept), so a
    # stalled task returns to the pool: its person's next runner, or another member's when theirs is not active, takes it
    db.execute("UPDATE project_task SET status = ?, branch = COALESCE(?, branch), pr_url = COALESCE(?, pr_url), note = ?,"
               " heartbeat_at = NULL, updated_at = ?, claim_id = NULL, claim_runner = NULL"
               " WHERE id = ?", ("done" if result == "done" else "approved", _text(branch, 300, "the branch") or None,
                                 pr_url, _text(note, 2000, "the note") or None, _at(moment), row["id"]))
    if result == "done":
        frictions.task_ended(db, project, row, "done", row["assignee_id"], _at(moment))   # a drafted friction is fixed
    db.commit()
    return get(db, project["id"], number)


def manual(row):
    """True when a person ticked "manual" on the task: the supervisor leaves its plan to a person."""
    return bool(row.get("manual"))


def auto_mode(project, row):
    """The supervisor decides this task: its project is in auto mode and the task is not marked manual."""
    return projects.autonomy(project) == "auto" and not manual(row)


def set_manual(db, row, value):
    """Mark a task manual (True) or not; -> the value before. The caller audits and commits."""
    before = manual(row)
    db.execute("UPDATE project_task SET manual = ? WHERE id = ?", (1 if value else 0, row["id"]))
    return before


def can_decide(db, project, row, account):
    """The assignee, a manager or above; in auto mode (auto_mode) also the supervisor, without a membership."""
    if account["role"] == accounts.SUPERVISOR and auto_mode(project, row):
        return True
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
    notifications.resolve(db, notifications.task_key(project, row["number"], "plan"), moment)
    if status == "changes_requested":
        notifications.notify(db, [row["assignee_id"]], project, "changes", notifications.task_text("changes", row),
                             notifications.task_link(project, row["number"]),
                             notifications.task_key(project, row["number"], "changes"), moment)
        frictions.record(db, project, "plan_changes", "", at)      # a plan sent back is a friction (record 0011)
    if status == "rejected":
        frictions.task_ended(db, project, row, "rejected", account["id"], at)    # mutes a drafted friction (R9)
    db.commit()
    if status == "changes_requested":
        frictions.draft_safely(db, moment)
    return get(db, project["id"], row["number"])


CLOSABLE = ("open", "planning", "changes_requested", "approved", "queued")
MAX_CLOSE_REASON = 500


def close(db, project, row, account, reason, moment):
    """End a task that will not be built: rejected, with the reason in `note` and the claim cleared."""
    if not can_decide(db, project, row, account):
        raise TaskError("only the assignee or a manager, owner or admin closes a task", 403)
    if row["status"] == "building":
        raise TaskError("task %d is being built: stop its runner first" % row["number"], 409)
    if row["status"] not in CLOSABLE:
        raise TaskError("task %d is already %s" % (row["number"], row["status"].replace("_", " ")), 409)
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    if not reason:
        raise TaskError("say why the task is closed")
    if len(reason) > MAX_CLOSE_REASON:
        raise TaskError("the reason is at most %d characters" % MAX_CLOSE_REASON)
    at = _at(moment)
    db.execute("UPDATE project_task SET status = 'rejected', note = ?, claim_id = NULL, claim_runner = NULL,"
               " heartbeat_at = NULL, decided_by = ?, decided_at = ?, updated_at = ? WHERE id = ?",
               (reason, account["id"], at, at, row["id"]))
    for kind in ("plan", "changes"):
        notifications.resolve(db, notifications.task_key(project, row["number"], kind), moment)
    frictions.task_ended(db, project, row, "rejected", account["id"], at)        # mutes a drafted friction (R9)
    return reason


def can_manage(db, project, row, account):
    """Release the claim or reassign the task: its assignee, a manager or above (admins included) and the supervisor.
    A pool task (no assignee) is managed by managers and above and the supervisor."""
    if account is None:
        return False
    if account["role"] == accounts.SUPERVISOR:
        return True
    if row["assignee_id"] is not None and row["assignee_id"] == account["id"]:
        return True
    return projects.allows(projects.effective_role(db, project, account), APPROVER_ROLE)


def can_take(db, project, row, account, moment):
    """"Take it" on the task page: a developer or above takes a task that is not theirs, not finished, and that no
    runner holds right now (a live claim is released by its assignee or a manager first)."""
    return (row["status"] not in ("done", "rejected") and row["assignee_id"] != account["id"]
            and not claim_live(row, moment) and _can_run(db, project, account))


def release(db, project, row, assignee, moment, pool=False):
    """End the claim (and, with `assignee`, give the task to someone else; with `pool`, to nobody: back to the pool).
    Work in progress goes back to the step it was at: planning -> open, building -> approved. Branch and pull request
    are kept. Commits."""
    at = _at(moment)
    reassigned = assignee is not None or pool
    status = {"planning": "open", "building": "approved", "queued": "approved"}.get(row["status"], row["status"])
    if reassigned and status in ("plan_review", "changes_requested"):
        status = "open"                                     # a new person plans it afresh
    owner = None if pool else (assignee["id"] if assignee else row["assignee_id"])
    db.execute("UPDATE project_task SET claim_id = NULL, claim_runner = NULL, heartbeat_at = NULL, status = ?,"
               " note = CASE WHEN ? = 'queued' THEN NULL ELSE note END, assignee_id = ?, updated_at = ? WHERE id = ?",
               (status, row["status"], owner, at, row["id"]))
    # a plan or a change request that no longer waits (the task went back to open for a new person) is done
    for kind, waiting in (("plan", "plan_review"), ("changes", "changes_requested")):
        if status != waiting:
            notifications.resolve(db, notifications.task_key(project, row["number"], kind), moment)
    updated = get(db, project["id"], row["number"])
    if assignee is not None:
        notify_assigned(db, project, updated, moment)
    db.commit()
    return updated


def take(db, project, row, account, moment):
    """A person takes the task for themselves from the task page (can_take) -> the updated row; writes `task.take`.
    Commits."""
    if not can_take(db, project, row, account, moment):
        raise TaskError("you cannot take task %d: it is yours, finished, held by a running runner, or you are not a "
                        "developer here" % row["number"], 409)
    reason = "unassigned" if row["assignee_id"] is None else "taken on the task page"
    updated = release(db, project, row, None, moment) if row["claim_id"] else row
    db.execute("UPDATE project_task SET assignee_id = ?, updated_at = ? WHERE id = ?", (account["id"], _at(moment), row["id"]))
    audit.record(db, "task.take", _at(moment), account_id=account["id"], project_id=project["id"],
                 detail={"number": row["number"], "from": updated["assignee_email"], "to": account["email"],
                         "reason": reason, "via": "page"})
    db.commit()
    return get(db, project["id"], row["number"])


def mark_done(db, project, row, by, moment):
    """The task's pull request is merged (the supervisor's `merge.done`): the task is done and its claim ends. Does not
    commit. A task already done or rejected is left as it is -> True when it changed."""
    if row["status"] in ("done", "rejected"):
        return False
    at = _at(moment)
    db.execute("UPDATE project_task SET status = 'done', claim_id = NULL, claim_runner = NULL, heartbeat_at = NULL,"
               " updated_at = ? WHERE id = ?", (at, row["id"]))
    for kind in ("plan", "changes"):
        notifications.resolve(db, notifications.task_key(project, row["number"], kind), moment)
    frictions.task_ended(db, project, row, "done", by, at)        # a drafted friction is fixed
    return True


def public(row, moment, full=False):
    """The task as the API shows it (never the claim id of someone else's claim)."""
    out = {"number": row["number"], "title": row["title"], "status": row["status"], "assignee": row["assignee_email"],
           "runner": row["claim_runner"] if claim_live(row, moment) else None, "branch": row["branch"],
           "pr_url": row["pr_url"], "updated_at": row["updated_at"], "priority": row["priority"],
           "depends_on": depends_of(row), "rank": row["rank"], "manual": manual(row)}
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


def _note_identity(db, account, runner, moment):
    """A runner named `<user>/<claude account>-claude-auto-runner` reports who it is; record it for the signed-in person."""
    pair = identities.split_runner(runner)
    if pair:
        identities.record(db, account["id"], pair[0], pair[1], _at(moment), "auto-runner")


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
    from . import attachments                              # attachments imports this module
    task = public(row, auth.now(), full=True)
    task["attachments"] = [{"id": a["id"], "filename": a["filename"], "content_type": a["content_type"],
                            "size": a["size"]} for a in attachments.for_task(get_db(), g.token_project["id"], number)]
    return jsonify(task=task)


@bp.post("/api/v1/tasks/next")
@tokens.bearer_token("pull")
def api_next():
    account, moment = _api_account(), auth.now()
    runner = _text(_body().get("runner"), 200, "the runner") or "a runner"
    db = get_db()
    row, claim = take_next(db, g.token_project, account, runner, moment)
    if row is None:
        return jsonify(task=None)
    _note_identity(db, account, runner, moment)
    audit.record(db, "task.claim", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                 detail={"number": row["number"], "runner": runner})
    db.commit()
    return jsonify(task=public(row, moment, full=True), claim=claim,
                   action="build" if row["status"] == "building" else "plan")


@bp.post("/api/v1/tasks")
@tokens.bearer_token("ingest")
def api_record():
    """A task planned in the terminal, claimed by this runner. Its plan approved there (the default): recorded as
    `building`. With `"approval": "page"` the plan is proposed instead: stored as `plan_review` (plan and items kept, the
    claim held while it waits) for a person to approve on the task page."""
    account, moment, body = _api_account(), auth.now(), _body()

    def run():
        db = get_db()
        approval = body.get("approval") or "terminal"
        if approval not in ("terminal", "page"):
            raise TaskError('approval is "terminal" (the default) or "page"')
        on_page = approval == "page"
        runner = _text(body.get("runner"), 200, "the runner") or "a runner"
        plan = _text(body.get("plan"), MAX_PLAN, "the plan", required=on_page)
        items = _text(body.get("items"), MAX_PLAN, "the items", required=on_page)
        row = create(db, g.token_project, account, account, body.get("title"), body.get("description"), moment,
                     status="plan_review" if on_page else "queued", plan=plan, items=items, runner=runner,
                     priority=body.get("priority"), depends_on=body.get("depends_on"))
        _note_identity(db, account, runner, moment)
        audit.record(db, "task.create", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                     detail={"number": row["number"], "via": "terminal", "approval": approval})
        if on_page:
            notify_plan(db, g.token_project, row, moment)
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
        _note_identity(db, account, _text(body.get("runner"), 200, "the runner"), moment)
        audit.record(db, "task.plan.post", _at(moment), account_id=account["id"], project_id=g.token_project["id"],
                     detail={"number": number})
        db.commit()
        return jsonify(task=public(row, moment))
    return _api(run)


@bp.post("/api/v1/tasks/<int:number>/heartbeat")
@tokens.bearer_token("ingest")
def api_beat(number):
    _api_account()
    body = _body()
    return _api(lambda: jsonify(task=public(beat(get_db(), g.token_project, number, body.get("claim"), auth.now(),
                                                 body.get("state"), body.get("note")), auth.now())))


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


@bp.post("/api/v1/tasks/<int:number>/decide")
@tokens.bearer_token("ingest")
def api_decide(number):
    """A person decides on a waiting plan from the terminal: the same rules as the task page's buttons."""
    account, body = _api_account(), _body()

    def run():
        db, moment = get_db(), auth.now()
        row = get(db, g.token_project["id"], number)
        if row is None:
            raise TaskError("no such task", 404)
        decision = body.get("decision")
        if decision not in ("approve", "changes", "reject"):
            raise TaskError('decision is "approve", "changes" or "reject"')
        row = decide(db, g.token_project, row, account, decision, body.get("comment"), moment)
        audit.record(db, "task.plan.%s" % decision, _at(moment), account_id=account["id"],
                     project_id=g.token_project["id"], detail={"number": number, "via": "api"})
        db.commit()
        return jsonify(task=public(row, moment))
    return _api(run)


# ---------- pages ----------

def _runners(db, project):
    """Members who can run runners (developer and above), for the admin's assignee choice."""
    return [m for m in projects.members(db, project["id"]) if projects.allows(m["role"], "developer") and not m["disabled"]]


STATES = (("approval", "Needs approval", ("plan_review",)),
          ("progress", "In progress", ("planning", "changes_requested", "approved", "queued", "building")),
          ("open", "Open", ("open",)),
          ("done", "Done", ("done", "rejected")))


BOARD_COLUMNS = (("open", "Open", ("open",)),
                 ("planning", "Planning", ("planning", "changes_requested")),
                 ("approval", "Needs approval", ("plan_review",)),
                 ("ready", "Ready", ("approved", "queued")),
                 ("building", "Building", ("building",)))
DONE_STATUSES = ("done", "rejected")    # finished tasks leave the board for the History panel below it (design)


RECENT_RUNS = 6                     # rows in the board's Runs panel
DONE_CAP = 20                       # rows shown in the board's History panel
TASK_VIEW_MAX_AGE = 365 * 24 * 3600


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
    chosen = request.args.get("view")
    view = chosen
    if view not in ("board", "list"):
        saved = request.cookies.get("task_view")
        view = "list" if state else (saved if saved in ("board", "list") else "board")
    columns = None
    if view == "board":
        columns = [{"key": k, "label": label,
                    "rows": sorted((r for r in rows if r["status"] in statuses), key=lambda r: r["updated_at"], reverse=True)}
                   for k, label, statuses in BOARD_COLUMNS]
        for c in columns:
            c["total"] = len(c["rows"])
        finished = sorted((r for r in rows if r["status"] in DONE_STATUSES), key=lambda r: r["updated_at"], reverse=True)
        done = {"rows": finished[:DONE_CAP], "total": len(finished)}
        shown = [r for c in columns for r in c["rows"]]
    else:
        if state not in [t["key"] for t in tabs]:
            state = next((t["key"] for t in tabs if t["rows"]), "approval")
        shown = next(t["rows"] for t in tabs if t["key"] == state)
    building = [r for r in shown if r["status"] == "building" or r["live"]]
    if building:
        from . import runs as _runs
        task_runs = _runs.summarise(_runs.project_events(db, g.project["id"]), moment)
        for r in building:
            r["progress"] = _runs.task_progress([x for x in task_runs if x["task"] == r["number"]], r["items"])
    lower = None
    if view == "board":
        from . import shell as _shell          # the board's lower panels: activity or history, runners or runs
        lower = _shell.board_lower(db, g.project, g.account, moment)
        lower["done"] = done
        lower["feed"] = "history" if request.args.get("feed") == "history" else "activity"
        lower["side"] = "runs" if request.args.get("side") == "runs" else "runners"
        if lower["side"] == "runs":
            from . import runs as _runs
            lower["runs"] = _runs.summarise(_runs.project_events(db, g.project["id"]), moment)[:RECENT_RUNS]
    response = make_response(render_template("tasks.html", project=g.project, tabs=tabs, state=state, tasks=shown,
                                             mine=mine, q=q, view=view, columns=columns, lower=lower,
                                             any_tasks=bool(for_project(db, g.project["id"])), can_add=_can_add(db)))
    if chosen in ("board", "list"):
        response.set_cookie("task_view", chosen, max_age=TASK_VIEW_MAX_AGE, path="/", secure=True, httponly=True,
                            samesite="Lax")
    return response


def _new_page(error=None, status=200, form=None):
    db = get_db()
    if error is None and not _can_add(db):
        abort(403)
    is_admin = g.account["role"] == projects.ADMIN
    from . import attachments as _att
    return render_template("task_new.html", project=g.project, error=error, form=form or {}, is_admin=is_admin,
                           assignees=_runners(db, g.project) if is_admin else [],
                           can_upload=projects.allows(g.project_role, _att.UPLOAD_ROLE),
                           shot_limits={"size": _att.LIMIT_TEXT, "count": _att.MAX_PER_TASK, "bytes": _att.MAX_SIZE}), status


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
    if chosen == "pool":
        assignee = None
    elif chosen and chosen != g.account["id"]:
        assignee = accounts.get(db, chosen)
        if assignee is None:
            abort(404)
    description = with_done_when(request.form.get("description"), request.form.get("done_when"))
    try:
        row = create(db, g.project, g.account, assignee, request.form.get("title"), description, moment,
                     priority=request.form.get("priority"), depends_on=request.form.get("depends_on"))
    except TaskError as e:
        return _new_page(error=str(e), status=e.status,
                         form={"title": (request.form.get("title") or "")[:MAX_TITLE],
                               "description": (request.form.get("description") or "")[:MAX_TEXT],
                               "done_when": (request.form.get("done_when") or "")[:MAX_TEXT], "assignee": chosen,
                               "priority": (request.form.get("priority") or "")[:20],
                               "depends_on": (request.form.get("depends_on") or "")[:200]})
    audit.record(db, "task.create", _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                 detail={"number": row["number"], "assignee": row["assignee_email"]})
    if row["assignee_id"] not in (None, g.account["id"]):
        notify_assigned(db, g.project, row, moment)
    db.commit()
    # Screenshots chosen on the form are stored after the task exists, so a refused file never loses the task; the
    # task page names each refused file and why.
    from . import attachments as _att
    files, refused = _att.uploaded_files(), []
    if files and not projects.allows(g.project_role, _att.UPLOAD_ROLE):
        refused = ["%s (viewers do not add screenshots)" % _att.clean_name(f.filename) for f in files]
    elif files:
        refused = _att.store_files(db, g.project, row["number"], g.account, files, moment)
    args = {"refused": _att.sign_refused(g.project["id"], row["number"], refused)} if refused else {}
    return redirect(url_for("tasks.task_page", slug=slug, number=row["number"], **args))


STEPS = (("open", "Open"), ("planned", "Planned"), ("approval", "Approval"), ("building", "Building"), ("done", "Done"))
STEP_OF = {"open": 0, "planning": 1, "changes_requested": 1, "plan_review": 2, "approved": 3, "queued": 3, "building": 3, "done": 4}


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


def _closed(db, project, number):
    """Who closed the task and when, from its newest `task.close` audit row; None when there is none."""
    rows = db.query("SELECT a.at, a.detail, acc.email FROM audit a LEFT JOIN account acc ON acc.id = a.account_id"
                    " WHERE a.project_id = ? AND a.action = 'task.close' ORDER BY a.at DESC, a.id DESC LIMIT 50",
                    (project["id"],))
    for r in rows:
        d = r["detail"]
        try:
            d = json.loads(d) if isinstance(d, str) else (d or {})
        except ValueError:
            d = {}
        if d.get("number") == number:
            return {"by": r["email"], "at": r["at"]}
    return None


def _detail(number, error=None, status=200):
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    from . import runs as _runs
    task_runs = _runs.for_task(db, g.project["id"], number, moment)
    progress = _runs.task_progress(task_runs, row["items"])
    states = (progress or {}).get("states", {})
    items = [dict(i, state="done" if i["ticked"] and i["id"] not in states else states.get(i["id"], "queued"),
                  show_state=row["status"] != "done" or i["id"] in states)
             for i in _runs.parse_items(row["items"])]
    closed = _closed(db, g.project, number) if row["status"] == "rejected" else None
    live = claim_live(row, moment)
    from . import attachments as _att
    shots = [dict(a, can_delete=_att.can_delete(a, g.account)) for a in _att.for_task(db, g.project["id"], number)]
    page = render_template("task.html", project=g.project, task=row, describe=DESCRIBE.get(row["status"], row["status"]),
                           runs=task_runs, progress=progress, items=items, steps=STEPS, closed=closed,
                           step=STEP_OF.get(row["status"]), stale_hours=int(_runs.STALE.total_seconds() // 3600),
                           live=live, quiet=bool(row["claim_id"]) and not live and row["status"] in RUNNER_SIDE,
                           can_decide=row["status"] == "plan_review" and can_decide(db, g.project, row, g.account),
                           can_close=row["status"] in CLOSABLE and can_decide(db, g.project, row, g.account),
                           can_manage=row["status"] not in ("done", "rejected") and can_manage(db, g.project, row, g.account),
                           can_take=can_take(db, g.project, row, g.account, moment), assignees=_runners(db, g.project),
                           project_auto=projects.autonomy(g.project) == "auto",
                           can_set_manual=can_manage(db, g.project, row, g.account) and row["status"] not in ("done", "rejected"),
                           activity=_activity(db, g.project, number, task_runs), error=error,
                           ttl_hours=int(CLAIM_TTL.total_seconds() // 3600), shots=shots,
                           can_upload=projects.allows(g.project_role, _att.UPLOAD_ROLE) and row["status"] not in ("done", "rejected"),
                           shot_limits={"size": _att.LIMIT_TEXT, "count": _att.MAX_PER_TASK, "bytes": _att.MAX_SIZE})
    return page, status


@bp.get("/projects/<slug>/tasks/<int:number>")
@project_access("viewer")
def task_page(slug, number):
    from . import attachments as _att
    return _detail(number, error=_att.read_refused(g.project["id"], number, request.args.get("refused")))


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


@bp.post("/projects/<slug>/tasks/<int:number>/close")
@project_access("viewer")
def task_close(slug, number):
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    reason = request.form.get("reason")
    try:
        if not ui.confirmed():
            # check first, so a refused close never reaches the confirm step
            if not can_decide(db, g.project, row, g.account):
                raise TaskError("only the assignee or a manager, owner or admin closes a task", 403)
            if row["status"] == "building" or row["status"] not in CLOSABLE:
                close(db, g.project, row, g.account, reason, moment)
            text = (reason or "").strip()
            if not text or len(text) > MAX_CLOSE_REASON:
                close(db, g.project, row, g.account, reason, moment)
            return ui.confirm_page("Close task #%d %s?" % (number, row["title"]),
                                   "The task is marked rejected with your reason and no runner will take it.",
                                   "Close task", url_for("tasks.task_page", slug=slug, number=number))
        reason = close(db, g.project, row, g.account, reason, moment)
    except TaskError as e:
        return _detail(number, error=str(e), status=e.status)
    audit.record(db, "task.close", _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                 detail={"number": number, "reason": reason})
    db.commit()
    return redirect(url_for("tasks.task_page", slug=slug, number=number))


@bp.post("/projects/<slug>/tasks/<int:number>/release")
@project_access("viewer")
def task_release(slug, number):
    """Release (end the claim) or Reassign (`assignee`: a member's account id, or "pool" for nobody): the assignee, a
    manager or above, or an admin. Anyone else gets 404."""
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None or not can_manage(db, g.project, row, g.account):
        abort(404)
    chosen = (request.form.get("assignee") or "").strip()
    pool = chosen == "pool" and row["assignee_id"] is not None
    to = accounts.get(db, chosen) if chosen and chosen != "pool" and chosen != row["assignee_id"] else None
    if not ui.confirmed():
        question = ("Give task #%d to %s?" % (number, to["email"]) if to else
                    "Put task #%d back in the pool?" % number if pool else "Take task #%d back from its runner?" % number)
        return ui.confirm_page(question,
                               "The runner working on it stops at its next step. Work goes back one step (planning to open, "
                               "building to approved); branch and pull request are kept"
                               + ("; the next person plans it afresh." if (to or pool) else "."),
                               "Give it to them" if to else "Put it in the pool" if pool else "Release it",
                               url_for("tasks.task_page", slug=slug, number=number))
    if chosen and chosen != "pool" and chosen != row["assignee_id"] and (to is None or not _can_run(db, g.project, to)):
        return _detail(number, error="choose a member who is a developer or above", status=400)
    release(db, g.project, row, to, moment, pool=pool)
    audit.record(db, "task.reassign" if (to or pool) else "task.release", _at(moment), account_id=g.account["id"],
                 project_id=g.project["id"], detail={"number": number, "runner": row["claim_runner"],
                                                     "from": row["assignee_email"],
                                                     "to": to["email"] if to else None, "pool": pool})
    db.commit()
    return redirect(url_for("tasks.task_page", slug=slug, number=number))


@bp.post("/projects/<slug>/tasks/<int:number>/manual")
@project_access("viewer")
def task_manual(slug, number):
    """Mark the task manual ("1": a person decides its plan, the supervisor leaves it alone) or back to the project's
    mode ("0"): the assignee, a manager or above, or an admin (can_manage). Audited as `task.manual`."""
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None or not can_manage(db, g.project, row, g.account):
        abort(404)
    value = request.form.get("manual", "")
    if value not in ("0", "1"):
        return _detail(number, error='manual is "1" (on) or "0" (off)', status=400)
    before = set_manual(db, row, value == "1")
    audit.record(db, "task.manual", _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                 detail={"number": number, "manual": value == "1", "before": before})
    db.commit()
    return redirect(url_for("tasks.task_page", slug=slug, number=number))


@bp.post("/projects/<slug>/tasks/<int:number>/take")
@project_access("viewer")
def task_take(slug, number):
    """Take it: a developer or above makes the task theirs (can_take); their runner takes it next."""
    db, moment = get_db(), auth.now()
    row = get(db, g.project["id"], number)
    if row is None:
        abort(404)
    if not can_take(db, g.project, row, g.account, moment):
        return _detail(number, error="you cannot take this task: it is yours, finished, held by a running runner, or "
                                     "you are not a developer here", status=409)
    if not ui.confirmed():
        return ui.confirm_page("Take task #%d %s?" % (number, row["title"]),
                               "It becomes yours and your runner takes it at its next look for work. Its branch and "
                               "pull request are kept.", "Take it", url_for("tasks.task_page", slug=slug, number=number))
    take(db, g.project, row, g.account, moment)
    return redirect(url_for("tasks.task_page", slug=slug, number=number))
