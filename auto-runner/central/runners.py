"""Runner presence (decision record 0009): each watching runner says where it is, and hears whether to pause or stop.

A runner sends `POST /api/v1/runners/beat?project=<slug>` with a personal token each step and when idle:
`{"label": ..., "state": ..., "task": N or null, "spend_usd": today's spend}`. The first beat for a label creates the
runner, owned by the token's account; a label belongs to one person in a project, so another person's token gets 403.
The reply carries `control`: null (carry on), "pause" (wait without claiming), "stop" (end cleanly) or "wake" (the
Supervisor asks it to look for work now; said once, then cleared). A runner whose
last beat is older than SILENT_AFTER is shown as silent. People set `control` from the pages (Pause, Resume and Stop, at the end of this
file); the task API does not change.
"""
import datetime
import math
import re
import secrets

from flask import Blueprint, abort, g, jsonify, redirect, request, url_for

from . import audit, auth, sessions, tokens, ui
from .access import project_access
from .db import get_db

bp = Blueprint("runners", __name__)

SILENT_AFTER = datetime.timedelta(hours=2)
CONTROLS = ("pause", "stop", "wake")
MAX_LABEL, MAX_STATE = 200, 100


class RunnerError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _at(moment):
    return sessions.iso(moment)


def _text(value, limit, what):
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise RunnerError("%s is required" % what)
    if len(text) > limit:
        raise RunnerError("%s is longer than %d characters" % (what, limit))
    return text


def _task(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RunnerError("task must be a task number or null")
    return value


def _spend(value):
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunnerError("spend_usd must be a number of dollars, 0 or more")
    return round(float(value), 4)


_SELECT = ("SELECT r.*, a.email AS account_email, c.role AS control_role FROM runner r"
           " JOIN account a ON a.id = r.account_id LEFT JOIN account c ON c.id = r.control_by")


def get(db, project_id, label):
    return db.query_one(_SELECT + " WHERE r.project_id = ? AND r.label = ?", (project_id, label))


def for_project(db, project_id):
    """The project's runners, most recent beat first."""
    return db.query(_SELECT + " WHERE r.project_id = ? ORDER BY r.beat_at DESC, r.label", (project_id,))


def silent(row, moment):
    """True when the runner has not beaten for SILENT_AFTER."""
    beat = sessions.parse_iso(row["beat_at"]) if row["beat_at"] else None
    return beat is None or moment - beat >= SILENT_AFTER


def beat(db, project, account, label, state, task, spend, moment):
    """Record one beat and commit -> (row, created, was_silent). 403 when the label is another person's runner."""
    label, state = _text(label, MAX_LABEL, "the label"), _text(state, MAX_STATE, "the state")
    task, spend, at = _task(task), _spend(spend), _at(moment)
    row = get(db, project["id"], label)
    if row is None:
        try:
            db.execute("INSERT INTO runner (id, project_id, account_id, label, state, task_number, spend_usd, beat_at,"
                       " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (secrets.token_hex(16), project["id"], account["id"], label, state, task, spend, at, at))
        except Exception:                                    # another beat created it first: treat as an update
            db.rollback()
            row = get(db, project["id"], label)
            if row is None:
                raise
        else:
            db.commit()
            return get(db, project["id"], label), True, False
    if row["account_id"] != account["id"]:
        raise RunnerError("runner %r belongs to another person in this project; choose another label" % label, 403)
    was_silent = silent(row, moment)
    db.execute("UPDATE runner SET state = ?, task_number = ?, spend_usd = ?, beat_at = ? WHERE id = ?",
               (state, task, spend, at, row["id"]))
    db.commit()
    return get(db, project["id"], label), False, was_silent


def public(row, moment):
    """The runner as the API shows it."""
    return {"label": row["label"], "person": row["account_email"], "state": row["state"], "task": row["task_number"],
            "spend_usd": row["spend_usd"], "beat_at": row["beat_at"], "silent": silent(row, moment),
            "control": row["control"] if row["control"] in CONTROLS else None}


# ---------- API (personal tokens) ----------

@bp.post("/api/v1/runners/beat")
@tokens.bearer_token("ingest")
def api_beat():
    account = g.get("token_account")
    if account is None:
        tokens._fail(403, "runner presence needs a personal token: sign this computer in with /auto-runner:central")
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    db, moment = get_db(), auth.now()
    try:
        row, created, was_silent = beat(db, g.token_project, account, body.get("label"), body.get("state"),
                                        body.get("task"), body.get("spend_usd"), moment)
    except RunnerError as e:
        response = jsonify(error=str(e))
        response.status_code = e.status
        return response
    if created or was_silent:                                # audit arrivals, not every beat
        audit.record(db, "runner.start" if created else "runner.back", _at(moment), account_id=account["id"],
                     project_id=g.token_project["id"], detail={"label": row["label"], "state": row["state"]})
        db.commit()
    out = public(row, moment)
    if row["control"] == "wake":                             # a wake is said once
        db.execute("UPDATE runner SET control = NULL WHERE id = ? AND control = 'wake'", (row["id"],))
        db.commit()
    return jsonify(runner=out, control=out["control"])


# ---------- on the pages: state in words, and Pause / Resume / Stop ----------
#
# Overview, Runs and My work list runners. Each runner's person and admins (the account role, not a project role) get
# Pause, Resume and Stop; everyone else sees the runner without buttons and gets 404 if they post. Each action asks
# first (`data-confirm` dialog, or `ui.confirm_page` without JavaScript), sets `control` (what the next beat returns)
# and writes an audit row `runner.pause`, `runner.resume` or `runner.stop`.

ACTIONS = {"pause": "pause", "resume": None, "stop": "stop", "wake": "wake"}   # wake: the Supervisor's API only
_STATE = re.compile(r"^(watching|planning|building|paused)\b(?:\s*#(\d+))?", re.I)
ASK = {
    "pause": ("Pause %s?", "It finishes the step it is on, then waits without taking new work until someone resumes it.",
              "Pause runner"),
    "resume": ("Resume %s?", "It takes work again at its next step. A runner that has already ended must be started "
               "again on its computer.", "Resume runner"),
    "stop": ("Stop %s?", "It finishes the step it is on and ends. Someone has to start it again on its computer.",
             "Stop runner"),
}
WORDS = {"watching": "Watching for work", "planning": "Planning", "building": "Building", "paused": "Paused",
         "silent": "Silent: no beat for 2 hours or more"}


def by_id(db, project_id, runner_id):
    return db.query_one(_SELECT + " WHERE r.project_id = ? AND r.id = ?", (project_id, runner_id))


def can_control(row, account):
    """The runner's own person, admins and the supervisor account."""
    return account is not None and (row["account_id"] == account["id"] or account["role"] in ("admin", "supervisor"))


def words(row, moment):
    """-> (key, task, text): the runner's state for a status chip and in a sentence. Silence wins over the last state."""
    m = _STATE.match(row["state"] or "")
    key = m.group(1).lower() if m else "other"
    task = int(m.group(2)) if m and m.group(2) else row["task_number"]
    if silent(row, moment):
        key = "silent"
    text = WORDS.get(key, (row["state"] or "").capitalize())
    if task and key in ("planning", "building"):
        text += " task #%d" % task
    return key, task, text


def view(row, moment, account, project):
    """One runner as the pages show it."""
    key, task, text = words(row, moment)
    control = row["control"] if row["control"] in CONTROLS else None
    note = None
    if control == "pause" and row["control_role"] == "supervisor":
        note = "Paused by the Supervisor"
    elif control == "pause":
        note = "Paused by a person" if key == "paused" else "Asked to pause: it waits after the step it is on"
    elif control == "stop":
        note = "Asked to stop: it ends after the step it is on"
    return {"id": row["id"], "label": row["label"], "person": row["account_email"], "key": key, "task": task,
            "text": text, "state": row["state"], "spend_usd": row["spend_usd"], "beat_at": row["beat_at"],
            "control": control, "note": note, "can_control": can_control(row, account), "project": project}


def views(db, project, account, moment, only_controllable=False):
    out = [view(r, moment, account, project) for r in for_project(db, project["id"])]
    return [v for v in out if v["can_control"]] if only_controllable else out


def set_control(db, row, action, account, moment):
    """Pause, resume or stop -> True when `control` changed (the caller audits and commits)."""
    value = ACTIONS[action]
    if (row["control"] or None) == value:
        return False
    db.execute("UPDATE runner SET control = ?, control_by = ?, control_at = ? WHERE id = ?",
               (value, account["id"], _at(moment), row["id"]))
    return True


@bp.post("/projects/<slug>/runners/<runner_id>/<action>")
@project_access("viewer")
def control_runner(slug, runner_id, action):
    if action not in ASK:
        abort(404)
    db, moment = get_db(), auth.now()
    row = by_id(db, g.project["id"], runner_id)
    if row is None or not can_control(row, g.account):
        abort(404)
    back = ui.safe_next(request.form.get("next")) or url_for("runs.runs_page", slug=slug)
    if not ui.confirmed():
        question, detail, label = ASK[action]
        return ui.confirm_page(question % ui.runner_label(row["label"]), detail, label, back)
    if set_control(db, row, action, g.account, moment):
        audit.record(db, "runner." + action, _at(moment), account_id=g.account["id"], project_id=g.project["id"],
                     detail={"label": row["label"], "owner": row["account_email"], "state": row["state"]})
    db.commit()
    return redirect(back)
