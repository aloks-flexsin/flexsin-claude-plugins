"""Decisions and their delivery to the runner, built to `auto-runner/docs/delivery-contract.md`.

- `record_decision(...)` (owners, managers and admins of the blocker's project) stores a `decision.v1` (the same
  shape as `autoblock.new_decision`) as the blocker's next version, and a delivery row `pending` with
  `delivery_id = <blocker_id>.v<version>`, in one commit with an audit row. The blocker row moves to
  `PENDING_DELIVERY`. The service keeps its own copy of the decision fields and validation (a parity test holds
  them equal to `auto-runner/bin/autoblock.py`).
- `GET /api/v1/deliveries/pending` (token scope `pull`) answers `{"deliveries": [...]}`: the token project's rows that
  are not acknowledged, oldest delivery id first. Each row handed over moves pending/failed -> delivered and counts an
  attempt; a row whose reply cannot be built (for example a stored decision that no longer parses) is left out,
  moves to `failed` with `last_error` and also counts an attempt. A delivered row is offered again until acknowledged.
- `POST /api/v1/deliveries/<delivery_id>/ack` (scope `pull`) moves delivered -> acknowledged and answers the row.
  A second ack changes nothing (200, same row). 404: no such delivery in the token's project (another project's id
  included). 409: the row was never delivered (pending or failed).
"""
import datetime
import json
import secrets

from flask import Blueprint, g, jsonify

from . import audit, auth, projects, sessions, tokens
from .db import get_db

bp = Blueprint("deliveries", __name__)

# The service's own copy of decision.v1 (see auto-runner/bin/autoblock.py; test_delivery_api.py checks they agree).
DECISION_SCHEMA = "decision.v1"
DECISION_FIELDS = ("schema", "blocker_id", "version", "responder", "role", "answer", "context", "created_at",
                   "applies_to")
DECIDER_ROLE = "manager"            # owner, manager and admin may decide
PENDING, DELIVERED, FAILED, ACKNOWLEDGED = "pending", "delivered", "failed", "acknowledged"
MAX_ERROR = 500


class DecisionError(ValueError):
    """The decision cannot be recorded as asked (no such blocker, an empty answer, ...)."""


class DecisionRefused(PermissionError):
    """The account may not decide on this project's blockers."""


def delivery_id(blocker_id, version):
    return "%s.v%d" % (blocker_id, version)


def decision_time(moment):
    """The timestamp format `autoblock.new_decision` writes (ISO 8601 with +00:00, whole seconds)."""
    return moment.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def validate_decision(d):
    """-> list of problems (empty = a valid decision.v1); the service's copy of autoblock.validate_decision."""
    if not isinstance(d, dict):
        return ["decision is not an object"]
    errors = ["missing field: %s" % f for f in DECISION_FIELDS if f not in d]
    errors += ["unknown field: %s" % f for f in d if f not in DECISION_FIELDS]
    if errors:
        return errors
    if d["schema"] != DECISION_SCHEMA:
        errors.append("schema: expected %s" % DECISION_SCHEMA)
    for f in ("blocker_id", "responder", "role", "created_at"):
        if not isinstance(d[f], str) or not d[f].strip():
            errors.append("%s: must be a non-empty string" % f)
    if not isinstance(d["version"], int) or isinstance(d["version"], bool) or d["version"] < 1:
        errors.append("version: must be an integer from 1")
    ans = d["answer"]
    if not (isinstance(ans, dict) and ans and set(ans) <= {"option", "text"}
            and all(isinstance(v, str) and v.strip() for v in ans.values())):
        errors.append("answer: must hold a non-empty option and/or text")
    if not isinstance(d["context"], str):
        errors.append("context: must be a string")
    if isinstance(d["created_at"], str) and d["created_at"]:
        try:
            datetime.datetime.fromisoformat(d["created_at"])
        except ValueError:
            errors.append("created_at: not ISO 8601")
    if d["applies_to"] is not None and not isinstance(d["applies_to"], str):
        errors.append("applies_to: must be null or a version string")
    return errors


def new_decision(blocker_id, version, responder, role, option, text, context, created_at):
    answer = {k: v for k, v in (("option", option), ("text", text)) if v}
    d = {"schema": DECISION_SCHEMA, "blocker_id": blocker_id, "version": version, "responder": responder,
         "role": role, "answer": answer, "context": context, "created_at": created_at, "applies_to": None}
    errors = validate_decision(d)
    if errors:
        raise DecisionError("; ".join(errors))
    return d


def _blocker(db, project_id, blocker_id):
    return db.query_one("SELECT b.*, t.item AS task FROM blocker b LEFT JOIN task t ON t.id = b.task_id"
                        " WHERE b.project_id = ? AND b.blocker_id = ?", (project_id, blocker_id))


def record_decision(db, project, blocker_id, account, option=None, text=None, context="", now=None):
    """Record a person's answer to a blocker and queue its delivery, in one commit; -> the delivery row (public
    shape). Raises DecisionRefused for anyone below manager on the project, DecisionError for a bad request."""
    moment = now or auth.utcnow()
    at = sessions.iso(moment)
    role = projects.effective_role(db, project, account)
    if not projects.allows(role, DECIDER_ROLE):
        if project is not None and account is not None:
            audit.record(db, "decision.record.refused", at, account_id=account["id"], project_id=project["id"],
                         detail={"blocker_id": str(blocker_id)[:64], "reason": "role %s" % (role or "none")})
            db.commit()
        raise DecisionRefused("only owners, managers and admins may answer this project's blockers")
    blocker = _blocker(db, project["id"], blocker_id)
    if blocker is None:
        raise DecisionError("no blocker %s in project %s" % (blocker_id, project["slug"]))
    if not blocker["task"]:
        raise DecisionError("blocker %s has no task" % blocker_id)
    try:
        last = db.query_one("SELECT MAX(version) AS v FROM decision WHERE blocker_ref = ?", (blocker["id"],))
        version = (last["v"] or 0) + 1
        decision = new_decision(blocker_id, version, account["email"], role, option, text, context or "",
                                decision_time(moment))
        decision_ref = secrets.token_hex(16)
        db.execute("INSERT INTO decision (id, blocker_ref, version, account_id, option_label, text, body, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (decision_ref, blocker["id"], version, account["id"], decision["answer"].get("option"),
                    decision["answer"].get("text"), json.dumps(decision, sort_keys=True), at))
        did = delivery_id(blocker_id, version)
        db.execute("INSERT INTO delivery (id, project_id, decision_id, delivery_id, status, attempts, created_at)"
                   " VALUES (?, ?, ?, ?, ?, 0, ?)",
                   (secrets.token_hex(16), project["id"], decision_ref, did, PENDING, at))
        db.execute("UPDATE blocker SET status = ?, updated_at = ? WHERE id = ?",
                   ("PENDING_DELIVERY", at, blocker["id"]))
        audit.record(db, "decision.record", at, account_id=account["id"], project_id=project["id"],
                     detail={"blocker_id": blocker_id, "version": version, "delivery_id": did, "role": role})
    except Exception:
        db.rollback()
        raise
    db.commit()
    return get(db, project, did)


# --- rows ---

_ROWS = ("SELECT d.*, c.version, c.body AS decision_body, b.blocker_id, t.item AS task FROM delivery d"
         " JOIN decision c ON c.id = d.decision_id JOIN blocker b ON b.id = c.blocker_ref"
         " LEFT JOIN task t ON t.id = b.task_id WHERE d.project_id = ?")


def public(row, project):
    """A row as the contract shows it (without the decision)."""
    return {"delivery_id": row["delivery_id"], "project": project["slug"], "task": row["task"],
            "blocker_id": row["blocker_id"], "version": row["version"], "state": row["status"],
            "attempts": row["attempts"], "last_error": row["last_error"]}


def get(db, project, did):
    row = db.query_one(_ROWS + " AND d.delivery_id = ?", (project["id"], did))
    return public(row, project) if row else None


def serialise(row, project):
    """-> (item, its JSON text) for a pull reply. Raises ValueError or TypeError when it cannot be built."""
    decision = json.loads(row["decision_body"])
    if decision.get("version") != row["version"] or decision.get("blocker_id") != row["blocker_id"]:
        raise ValueError("stored decision does not match delivery %s" % row["delivery_id"])
    item = {"delivery_id": row["delivery_id"], "blocker_id": row["blocker_id"], "version": row["version"],
            "project": project["slug"], "task": row["task"], "decision": decision}
    return item, json.dumps(item, sort_keys=True, allow_nan=False)


def pull_pending(db, project, at):
    """Hand over every unacknowledged row of `project`, oldest delivery id first, in one commit; -> the items."""
    rows = db.query(_ROWS + " AND d.status <> ? ORDER BY d.delivery_id", (project["id"], ACKNOWLEDGED))
    out = []
    try:
        for row in rows:
            try:
                item, _ = serialise(row, project)
            except (ValueError, TypeError) as exc:
                db.execute("UPDATE delivery SET status = ?, attempts = attempts + 1, last_error = ? WHERE id = ?",
                           (FAILED, ("reply could not be built: %s" % exc)[:MAX_ERROR], row["id"]))
                continue
            db.execute("UPDATE delivery SET status = ?, attempts = attempts + 1, last_error = NULL, delivered_at = ?"
                       " WHERE id = ?", (DELIVERED, at, row["id"]))
            out.append(item)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return out


class AckError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def ack(db, project, did, at):
    """delivered -> acknowledged and commit; -> the row. A second ack changes nothing. AckError 404 / 409."""
    row = get(db, project, did)
    if row is None:
        raise AckError(404, "no delivery %s" % did)
    if row["state"] == ACKNOWLEDGED:
        return row
    if row["state"] != DELIVERED:
        raise AckError(409, "delivery %s is %s: only a delivered row can be acknowledged" % (did, row["state"]))
    cur = db.execute("UPDATE delivery SET status = ?, acked_at = ? WHERE project_id = ? AND delivery_id = ?"
                     " AND status = ?", (ACKNOWLEDGED, at, project["id"], did, DELIVERED))
    if cur.rowcount:
        audit.record(db, "delivery.ack", at, project_id=project["id"],
                     detail={"delivery_id": did, "token_id": g.token["id"] if "token" in g else None})
    db.commit()
    return get(db, project, did)


# --- API ---

@bp.get("/api/v1/deliveries/pending")
@tokens.bearer_token("pull")
def pending():
    items = pull_pending(get_db(), g.token_project, sessions.iso(auth.now()))
    response = jsonify(deliveries=items)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/v1/deliveries/<path:did>/ack")
@tokens.bearer_token("pull")
def acknowledge(did):
    try:
        row = ack(get_db(), g.token_project, did, sessions.iso(auth.now()))
    except AckError as e:
        response = jsonify(error=e.message)
        response.status_code = e.status
        return response
    return jsonify(row)
