"""Runner presence (decision record 0009): each watching runner says where it is, and hears whether to pause or stop.

A runner sends `POST /api/v1/runners/beat?project=<slug>` with a personal token each step and when idle:
`{"label": ..., "state": ..., "task": N or null, "spend_usd": today's spend}`. The first beat for a label creates the
runner, owned by the token's account; a label belongs to one person in a project, so another person's token gets 403.
The reply carries `control`: null (carry on), "pause" (wait without claiming) or "stop" (end cleanly). A runner whose
last beat is older than SILENT_AFTER is shown as silent. People set `control` from the pages (a later step); the task
API does not change.
"""
import datetime
import math
import secrets

from flask import Blueprint, g, jsonify, request

from . import audit, auth, sessions, tokens
from .db import get_db

bp = Blueprint("runners", __name__)

SILENT_AFTER = datetime.timedelta(hours=2)
CONTROLS = ("pause", "stop")
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


def get(db, project_id, label):
    return db.query_one("SELECT r.*, a.email AS account_email FROM runner r JOIN account a ON a.id = r.account_id"
                        " WHERE r.project_id = ? AND r.label = ?", (project_id, label))


def for_project(db, project_id):
    """The project's runners, most recent beat first."""
    return db.query("SELECT r.*, a.email AS account_email FROM runner r JOIN account a ON a.id = r.account_id"
                    " WHERE r.project_id = ? ORDER BY r.beat_at DESC, r.label", (project_id,))


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
    return jsonify(runner=out, control=out["control"])
