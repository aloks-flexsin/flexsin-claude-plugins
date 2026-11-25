"""Runner frictions (decision record 0011): counting at ingest into tables shared across projects.

- `record(db, project, kind, subject, at)` counts one occurrence of the signature `kind` or `kind:subject` for a
  project that shares its frictions (`project.share_frictions`, on by default; owners switch it on Settings ›
  Repository). It adds one to `friction_count` (signature + project: count, first and last seen) and to
  `friction_day` (signature + project + UTC day), and creates the `friction_signature` row (state `watching`) the first
  time a signature is seen. A signature row that already exists is left as it is (its state belongs to people and to
  drafting).
- `at` is the service's own time (when the event was received or the decision was made), not the runner's clock, so a
  runner with a wrong clock cannot move a count into another day. Duplicated events never reach here (ingest skips them).
- The caller commits.
- `GET /frictions` (admins only; anyone else 404) lists one row per signature, with Mute (a reason, confirmed) and Unmute
  (`POST /frictions/mute` and `/frictions/unmute`, the signature in the form; CSRF in auth.py; audited `friction.mute`
  and `friction.unmute`). Unmute sets `watching`, clears the reason and sets `changed_at`, so, as after a fix, only
  occurrences after it count toward drafting (R8).
- `draft_due(db, now)` runs after ingest stores friction events and after a plan is sent back. When
  CENTRAL_REFLECTION_PROJECT names a project, each `watching` or `fixed` signature with at least
  CENTRAL_REFLECTION_MIN_COUNT occurrences or CENTRAL_REFLECTION_MIN_PROJECTS projects in the last 30 UTC days
  (`friction_day`; after an unmute or a fix only days after its UTC date) gets an open task there, at most
  CENTRAL_REFLECTION_DAILY a UTC day. The task goes to CENTRAL_REFLECTION_ASSIGNEE when that account is an enabled member
  who can run runners, else to the project's first owner, who is also `by` (no person acts: the audit row
  `friction.draft` says `"via": "reflection"`) and gets an `assigned` notification. The signature becomes `drafted`
  with the task linked.
- `task_ended(db, project, row, outcome, by, at)` closes the loop (R9): tasks.decide (reject), tasks.close and
  tasks.report call it. When the task is the one linked to a `drafted` signature, a rejection mutes the signature with
  the reason "task #N rejected" and a `done` result marks it `fixed` (`changed_at` = then), both audited
  (`friction.mute` / `friction.fixed`, `"via": "task"`). A `fixed` signature that reaches the threshold again on later
  days drafts "Runner friction came back: <signature>"; the earlier task's number moves to `previous_task_number` first.
"""
import datetime
import logging

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from . import audit, auth, config, ingest, projects, sessions, ui
from .admin import _require_admin
from .db import get_db, schema_missing

STATES = ("watching", "drafted", "muted", "fixed")
MAX_REASON = 500

bp = Blueprint("frictions", __name__)


class FrictionError(ValueError):
    pass


def signature(kind, subject=""):
    """-> `kind` or `kind:subject` (the same rule as autoevents.signature)."""
    return "%s:%s" % (kind, subject) if subject else kind


def shares(project):
    """True unless the project switched sharing off (a row read before the column existed counts as on)."""
    value = project.get("share_frictions")
    return value is None or bool(int(value))


def _guarded(db, fn, *args):
    """Run friction bookkeeping `fn(db, *args)` inside a savepoint. When the friction tables or the share column are not
    in the database yet (code deployed before `python -m central init-db`), roll back only these writes, log it and
    return None, so the request that caused them (an event push, a report, a decision) still succeeds. Any other error
    is raised as before."""
    db.execute("SAVEPOINT friction_bookkeeping")
    try:
        out = fn(db, *args)
    except Exception as exc:
        db.execute("ROLLBACK TO SAVEPOINT friction_bookkeeping")
        db.execute("RELEASE SAVEPOINT friction_bookkeeping")
        if not schema_missing(exc):
            raise
        logging.getLogger(__name__).warning("friction bookkeeping skipped: run python -m central init-db (%s)", exc)
        return None
    db.execute("RELEASE SAVEPOINT friction_bookkeeping")
    return out


def record(db, project, kind, subject, at):
    """Count one friction (see _record); skipped while the database lacks the friction tables."""
    return _guarded(db, _record, project, kind, subject, at)


def _record(db, project, kind, subject, at):
    """Count one friction for `project` at `at` (an ISO UTC time); -> the signature, or None when not shared."""
    row = db.query_one("SELECT share_frictions FROM project WHERE id = ?", (project["id"],))
    if row is None or not shares(row):
        return None
    sig = signature(kind, subject)
    day = at[:10]
    if db.query_one("SELECT signature FROM friction_count WHERE signature = ? AND project_id = ?", (sig, project["id"])):
        db.execute("UPDATE friction_count SET count = count + 1, last_seen = ? WHERE signature = ? AND project_id = ?",
                   (at, sig, project["id"]))
    else:
        db.execute("INSERT INTO friction_count (signature, project_id, count, first_seen, last_seen)"
                   " VALUES (?, ?, 1, ?, ?)", (sig, project["id"], at, at))
    if db.query_one("SELECT day FROM friction_day WHERE signature = ? AND project_id = ? AND day = ?",
                    (sig, project["id"], day)):
        db.execute("UPDATE friction_day SET count = count + 1 WHERE signature = ? AND project_id = ? AND day = ?",
                   (sig, project["id"], day))
    else:
        db.execute("INSERT INTO friction_day (signature, project_id, day, count) VALUES (?, ?, ?, 1)",
                   (sig, project["id"], day))
    if not db.query_one("SELECT signature FROM friction_signature WHERE signature = ?", (sig,)):
        # changed_at stays empty: it marks a person's unmute or a fix, after which only newer days count (R8)
        db.execute("INSERT INTO friction_signature (signature, kind, subject, state) VALUES (?, ?, ?, 'watching')",
                   (sig, kind, subject or ""))
    return sig


# ---------- drafting a task when a signature keeps coming back (R8) ----------

WINDOW_DAYS = 30
DRAFTABLE = ("watching", "fixed")

# Where to look first for each kind; written into the drafted task (a fixed map: no project content).
CODE_AREAS = {
    "permission_denied": "the `.claude/settings.json` allow list and `auto-runner/bin/autohook.py`",
    "gate_stop": "the gates in `auto-runner/bin/auto_run.py` (docs: `autodocs.py`, browser: `autobrowser.py`,"
                 " journey: `autojourney.py`, stale: `autostale.py`)",
    "fix_round": "the suite and journey fix rounds in `auto-runner/bin/auto_run.py`",
    "retry": "early-exit retries in `auto-runner/bin/auto_run.py`",
    "usage_stop": "usage, budget and threshold stops in `auto-runner/bin/auto_run.py`",
    "refused_start": "the start checks in `auto-runner/bin/autosafe.py` (`preflight_groups`) and"
                     " `auto-runner/bin/autoupdate.py`",
    "central_error": "the central calls in `auto-runner/bin/autotasks.py` and `auto-runner/bin/autodeliver.py`,"
                     " and the matching route in `auto-runner/central/`",
    "plan_changes": "the planner prompt and principles in `auto-runner/bin/autoplan.py`",
}


def window_counts(db, sig, since_day, changed_at=None):
    """-> (occurrences, number of projects) of `sig` on days from `since_day` on; with `changed_at` (an unmute or a
    fix) only days after its UTC date count, so the change day itself is left out."""
    floor = since_day
    if changed_at and changed_at[:10] >= floor:
        floor = (datetime.date.fromisoformat(changed_at[:10]) + datetime.timedelta(days=1)).isoformat()
    rows = db.query("SELECT project_id, count FROM friction_day WHERE signature = ? AND day >= ?", (sig, floor))
    return sum(r["count"] for r in rows), len({r["project_id"] for r in rows if r["count"]})


def _drafts_on(db, day):
    return db.query_one("SELECT COUNT(*) AS n FROM audit WHERE action = 'friction.draft' AND at LIKE ?",
                        (day + "%",))["n"]


def _assignee(db, project, email):
    """The configured account when it is an enabled member who can run runners, else the project's first owner."""
    from . import accounts
    if email:
        account = accounts.find_by_email(db, email)
        if account and accounts.is_enabled(account) and projects.allows(
                projects.member_role(db, project["id"], account["id"]), "developer"):
            return account
    row = db.query_one("SELECT account_id FROM member WHERE project_id = ? AND role = 'owner'"
                       " ORDER BY created_at, account_id LIMIT 1", (project["id"],))
    return accounts.get(db, row["account_id"]) if row else None


def _description(row, total, nprojects, settings):
    seen = row.get("_seen") or {}
    return "\n".join([
        "The runner keeps hitting the friction `%s` (decision record 0011)." % row["signature"],
        "",
        "- Kind: %s; subject: %s" % (row["kind"], row["subject"] or "(none)"),
        "- In the last %d days: %d time%s in %d project%s (thresholds: %d times or %d projects)"
        % (WINDOW_DAYS, total, "" if total == 1 else "s", nprojects, "" if nprojects == 1 else "s",
           settings["min_count"], settings["min_projects"]),
        "- First seen: %s; last seen: %s" % (seen.get("first") or "unknown", seen.get("last") or "unknown"),
        "- Where to look: %s" % CODE_AREAS.get(row["kind"], "the auto-runner plugin"),
        "",
        "Drafted by the reflection layer, not by a person. Reject the task to mute this signature.",
    ])


def draft_due(db, now, cfg=None):
    """Draft an open task in CENTRAL_REFLECTION_PROJECT for each signature over the threshold; -> the signatures
    drafted. Called after ingest and after a plan is sent back, once their own changes are committed; commits."""
    from . import notifications, tasks                     # both import this module
    settings = config.reflection(current_app.config if cfg is None else cfg)
    if not settings["project"]:
        return []
    project = projects.get_by_slug(db, settings["project"])
    if project is None:
        return []
    today = now.astimezone(datetime.timezone.utc).date()
    since = (today - datetime.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    room = settings["daily"] - _drafts_on(db, today.isoformat())
    drafted = []
    for row in db.query("SELECT * FROM friction_signature WHERE state IN (?, ?) ORDER BY signature",
                        DRAFTABLE):
        if room <= 0:
            break
        total, nprojects = window_counts(db, row["signature"], since, row["changed_at"])
        if total < settings["min_count"] and nprojects < settings["min_projects"]:
            continue
        assignee = _assignee(db, project, settings["assignee"])
        if assignee is None:
            return drafted
        seen = db.query_one("SELECT MIN(first_seen) AS first, MAX(last_seen) AS last FROM friction_count"
                            " WHERE signature = ?", (row["signature"],))
        text = _description(dict(row, _seen=dict(seen) if seen else {}), total, nprojects, settings)
        came_back = row["state"] == "fixed" and row["task_number"] is not None
        title = ("Runner friction came back: %s" if came_back else "Runner friction: %s") % row["signature"]
        if came_back:
            text += "\n\nIt came back after task #%d was done." % row["task_number"]
        try:
            task = tasks.create(db, project, assignee, assignee, title, text, now)
        except tasks.TaskError:
            return drafted                                 # the assignee cannot run runners there; nothing to draft
        at = sessions.iso(now)
        previous = row["task_number"] if came_back else row["previous_task_number"]
        db.execute("UPDATE friction_signature SET state = 'drafted', task_project_id = ?, task_number = ?,"
                   " previous_task_number = ?, changed_by = ?, changed_at = ? WHERE signature = ?",
                   (project["id"], task["number"], previous, assignee["id"], at, row["signature"]))
        notifications.notify(db, [assignee["id"]], project, "assigned", notifications.task_text("assigned", task),
                             notifications.task_link(project, task["number"]),
                             notifications.task_key(project, task["number"], "assigned"), now)
        audit.record(db, "friction.draft", at, account_id=assignee["id"], project_id=project["id"],
                     detail={"signature": row["signature"], "task": task["number"], "count": total,
                             "projects": nprojects, "via": "reflection",
                             **({"previous_task": row["task_number"]} if came_back else {})})
        db.commit()
        drafted.append(row["signature"])
        room -= 1
    return drafted


def task_ended(db, project, row, outcome, by, at):
    """See _task_ended; skipped while the database lacks the friction tables."""
    return _guarded(db, _task_ended, project, row, outcome, by, at)


def _task_ended(db, project, row, outcome, by, at):
    """A task ended (`outcome` "rejected" or "done"): when it is the task linked to a drafted signature, mute it or mark
    it fixed; -> the signature, or None. `by` is the acting account id (or None); `at` an ISO time. Does not commit."""
    sig = db.query_one("SELECT * FROM friction_signature WHERE task_project_id = ? AND task_number = ?"
                       " AND state = 'drafted'", (project["id"], row["number"]))
    if sig is None:
        return None
    if outcome == "rejected":
        reason = "task #%d rejected" % row["number"]
        db.execute("UPDATE friction_signature SET state = 'muted', reason = ?, changed_by = ?, changed_at = ?"
                   " WHERE signature = ?", (reason, by, at, sig["signature"]))
        audit.record(db, "friction.mute", at, account_id=by, project_id=project["id"],
                     detail={"signature": sig["signature"], "from": "drafted", "reason": reason, "via": "task"})
    elif outcome == "done":
        db.execute("UPDATE friction_signature SET state = 'fixed', reason = NULL, changed_by = ?, changed_at = ?"
                   " WHERE signature = ?", (by, at, sig["signature"]))
        audit.record(db, "friction.fixed", at, account_id=by, project_id=project["id"],
                     detail={"signature": sig["signature"], "task": row["number"], "via": "task"})
    else:
        return None
    return sig["signature"]


def draft_safely(db, now, cfg=None):
    """draft_due for callers whose own work is already committed: a failure is logged and rolled back, never raised."""
    try:
        return draft_due(db, now, cfg)
    except Exception:                                      # noqa: BLE001 - the push or decision already succeeded
        db.rollback()
        logging.getLogger(__name__).exception("drafting a friction task failed")
        return []


def set_sharing(db, project_id, share):
    """Switch sharing on or off; -> the value before (True/False)."""
    row = db.query_one("SELECT share_frictions FROM project WHERE id = ?", (project_id,))
    before = shares(row)
    db.execute("UPDATE project SET share_frictions = ? WHERE id = ?", (1 if share else 0, project_id))
    return before


# ---------- the Frictions page ----------

def overview(db, kind=None, state=None):
    """-> one dict per signature (its row plus `count`, `projects` (names), `first_seen`, `last_seen`, `task_slug`),
    newest last-seen first; `kind`/`state` narrow it (an unknown value lists nothing)."""
    per = {}
    for r in db.query("SELECT c.signature, c.count, c.first_seen, c.last_seen, p.name FROM friction_count c"
                      " JOIN project p ON p.id = c.project_id"):
        a = per.setdefault(r["signature"], {"count": 0, "projects": [], "first_seen": None, "last_seen": None})
        a["count"] += r["count"]
        a["projects"].append(r["name"])
        a["first_seen"] = min(filter(None, (a["first_seen"], r["first_seen"])))
        a["last_seen"] = max(filter(None, (a["last_seen"], r["last_seen"])))
    slugs = {r["id"]: r["slug"] for r in db.query("SELECT id, slug FROM project")}
    rows = []
    for s in db.query("SELECT * FROM friction_signature"):
        if (kind and s["kind"] != kind) or (state and s["state"] != state):
            continue
        a = per.get(s["signature"], {"count": 0, "projects": [], "first_seen": None, "last_seen": None})
        rows.append(dict(s, count=a["count"], projects=sorted(a["projects"], key=str.lower), first_seen=a["first_seen"],
                         last_seen=a["last_seen"], task_slug=slugs.get(s["task_project_id"])))
    rows.sort(key=lambda r: r["signature"])
    rows.sort(key=lambda r: r["last_seen"] or "", reverse=True)
    return rows


def get(db, sig):
    return db.query_one("SELECT * FROM friction_signature WHERE signature = ?", (sig,))


def mute(db, sig, by, at, reason):
    """Mute a signature (never drafted while muted); -> the state before. Raises FrictionError without a reason."""
    reason = (reason or "").strip()
    if not reason:
        raise FrictionError("Say why it is muted.")
    if len(reason) > MAX_REASON:
        raise FrictionError("The reason is longer than %d characters." % MAX_REASON)
    row = get(db, sig)
    if row["state"] == "muted":
        raise FrictionError("It is already muted.")
    db.execute("UPDATE friction_signature SET state = 'muted', reason = ?, changed_by = ?, changed_at = ?"
               " WHERE signature = ?", (reason, by, at, sig))
    return row["state"]


def unmute(db, sig, by, at):
    """Back to `watching`: the reason is cleared and `changed_at` set, so only later occurrences count toward drafting."""
    row = get(db, sig)
    if row["state"] != "muted":
        raise FrictionError("It is not muted.")
    db.execute("UPDATE friction_signature SET state = 'watching', reason = NULL, changed_by = ?, changed_at = ?"
               " WHERE signature = ?", (by, at, sig))
    return row["reason"]


def _at():
    return sessions.iso(auth.now())


def _page(error=None, status=200):
    db = get_db()
    kind = (request.args.get("kind") or "").strip()[:32]
    state = (request.args.get("state") or "").strip()[:16]
    rows = overview(db, kind or None, state or None)
    return render_template("frictions.html", rows=rows, kinds=ingest.FRICTION_KINDS, states=STATES, kind=kind,
                           state=state, error=error, max_reason=MAX_REASON,
                           drafting=config.reflection(current_app.config)), status


def _target(db):
    row = get(db, (request.form.get("signature") or "")[:120])
    if row is None:
        abort(404)
    return row


def _refused(db, action, row, reason):
    audit.record(db, action + ".refused", _at(), account_id=g.account["id"],
                 detail={"signature": row["signature"], "reason": reason})
    db.commit()
    return _page(error=reason, status=400)


@bp.get("/frictions")
def frictions_page():
    _require_admin()
    return _page()


@bp.post("/frictions/mute")
def mute_post():
    _require_admin()
    db = get_db()
    row = _target(db)
    if not ui.confirmed():
        return ui.confirm_page("Mute %s?" % row["signature"],
                               "It is still counted, but no task is drafted for it until someone unmutes it.", "Mute",
                               url_for("frictions.frictions_page"))
    reason = request.form.get("reason")
    try:
        before = mute(db, row["signature"], g.account["id"], _at(), reason)
    except FrictionError as exc:
        return _refused(db, "friction.mute", row, str(exc))
    audit.record(db, "friction.mute", _at(), account_id=g.account["id"],
                 detail={"signature": row["signature"], "from": before, "reason": reason.strip()})
    db.commit()
    return redirect(url_for("frictions.frictions_page"))


@bp.post("/frictions/unmute")
def unmute_post():
    _require_admin()
    db = get_db()
    row = _target(db)
    if not ui.confirmed():
        return ui.confirm_page("Unmute %s?" % row["signature"],
                               "Only occurrences from now on count toward drafting a task.", "Unmute",
                               url_for("frictions.frictions_page"))
    try:
        reason = unmute(db, row["signature"], g.account["id"], _at())
    except FrictionError as exc:
        return _refused(db, "friction.unmute", row, str(exc))
    audit.record(db, "friction.unmute", _at(), account_id=g.account["id"],
                 detail={"signature": row["signature"], "reason_was": reason})
    db.commit()
    return redirect(url_for("frictions.frictions_page"))
