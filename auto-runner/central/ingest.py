"""Event ingest: `POST /api/v1/events` (token scope `ingest`) takes one `event.v1` object or a list of them.

- The service keeps its own copy of the event.v1 field, type and evidence lists and of the validation (it never
  imports the plugin); a parity test holds them equal to `auto-runner/bin/autoevents.py`.
- A request is all or nothing: every event is checked first, and one bad event stores none of them.
  400: not JSON, an unknown schema version, or an invalid event. 403: an event's `project` is not the token's
  project slug. 409: an event id already stored with a different body. 413: a body over 256 KB.
  429: more than 120 requests in a minute for one token (in memory, per process; the clock is `CLOCK`).
- Idempotent by event id: the same id with the same body is counted as a duplicate and changes nothing.
  Bodies are compared after redaction, as stored (canonical JSON, sha256).
- Before storing, string values in `detail` that look like secrets are replaced with `[redacted]`, and so is
  every value under a key that names a secret (password, token, api_key, ...).
- A `blocker` event with action `raised` creates or updates the blocker row (project, task = item, blocker id); the
  question comes from `detail.blocker.question` when the runner sends the blocker's content (it does from this
  version on). Action `resumed` moves a known blocker row to RESUMED.
"""
import collections
import datetime
import hashlib
import json
import re
import secrets
import threading

from flask import Blueprint, current_app, g, jsonify, request

from . import auth, sessions, tokens
from .db import get_db

bp = Blueprint("ingest", __name__)

# The service's own copy of event.v1 (see auto-runner/bin/autoevents.py; test_ingest.py checks they agree).
SCHEMA = "event.v1"
TYPES = ("run_start", "run_end", "item_state", "tests", "blocker", "browser", "findings", "changelog", "docs", "publish")
RUN_TYPES = ("run_start", "run_end", "publish")
EVIDENCE = ("runner_observed", "independent_test", "agent_reported")
FIELDS = ("schema", "id", "time", "run", "project", "type", "item", "detail", "evidence_class")

MAX_BODY = 256 * 1024
RATE_LIMIT = 120
RATE_WINDOW = datetime.timedelta(minutes=1)
MAX_ID = 128
MAX_QUESTION = 2000
BLOCKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REDACTED = "[redacted]"

SECRET_KEY_NAME = re.compile(r"(passw(or)?d|passphrase|secret|token|api[_-]?key|private[_-]?key|credential|"
                             r"authorization|cookie|session[_-]?id)", re.I)
SECRET_VALUE = re.compile("|".join((
    r"art_[A-Za-z0-9_-]{20,}",                                   # this service's API tokens
    r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",                # Authorization header values
    r"sk-[A-Za-z0-9_-]{20,}",                                     # sk- style API keys
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",   # GitHub tokens
    r"xox[abprs]-[A-Za-z0-9-]{10,}",                              # Slack tokens
    r"(?:AKIA|ASIA)[A-Z0-9]{16}",                                 # AWS access key ids
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWTs
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)",                           # user:password@ in URLs
    r"(?i:passw(?:or)?d|secret|token|api[_-]?key)\s*[=:]\s*[^\s,;&'\"]+",  # key=value pairs
)), re.S)


class IngestError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# --- validation (the service's copy of autoevents.validate) ---

def validate(event):
    """-> list of problems (empty = a valid event.v1)."""
    if not isinstance(event, dict):
        return ["event is not an object"]
    errors = ["missing field: %s" % f for f in FIELDS if f not in event]
    errors += ["unknown field: %s" % f for f in event if f not in FIELDS]
    if errors:
        return errors
    if event["schema"] != SCHEMA:
        errors.append("schema: expected %s" % SCHEMA)
    for f in ("id", "time", "run", "project"):
        if not isinstance(event[f], str) or not event[f]:
            errors.append("%s: must be a non-empty string" % f)
    if isinstance(event["id"], str) and len(event["id"]) > MAX_ID:
        errors.append("id: at most %d characters" % MAX_ID)
    if event["type"] not in TYPES:
        errors.append("type: must be one of %s" % ", ".join(TYPES))
    if event["evidence_class"] not in EVIDENCE:
        errors.append("evidence_class: must be one of %s" % ", ".join(EVIDENCE))
    if not isinstance(event["detail"], dict):
        errors.append("detail: must be an object")
    if event["type"] in RUN_TYPES:
        if event["item"] is not None:
            errors.append("item: must be null for run events")
    elif not isinstance(event["item"], str) or not event["item"]:
        errors.append("item: required for item events")
    if isinstance(event["time"], str):
        try:
            datetime.datetime.fromisoformat(event["time"])
        except ValueError:
            errors.append("time: not ISO 8601")
    if _raised_blocker(event) and not BLOCKER_ID.match(str(event["detail"].get("id") or "")):
        errors.append("detail.id: a raised blocker needs a valid blocker id")
    return errors


def _raised_blocker(event):
    return event.get("type") == "blocker" and isinstance(event.get("detail"), dict) \
        and event["detail"].get("action") == "raised"


# --- redaction ---

def redact_text(text):
    return SECRET_VALUE.sub(REDACTED, text)


def redact(value, key=None):
    """A copy of `value` with secret-looking strings (and everything under secret-named keys) redacted."""
    if key is not None and SECRET_KEY_NAME.search(key) and value is not None and not isinstance(value, bool):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def canonical(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- rate limit ---

class RateLimiter:
    """At most `limit` requests per `window` for each key, in memory (one process; resets on restart)."""

    def __init__(self, limit=RATE_LIMIT, window=RATE_WINDOW):
        self.limit, self.window = limit, window
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key, now):
        """Count one request at `now`; -> (allowed, seconds until the next one is allowed)."""
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                wait = (hits[0] + self.window - now).total_seconds()
                return False, max(1, int(wait + 0.999))
            hits.append(now)
            return True, 0


def limiter():
    return current_app.extensions.setdefault("ingest_rate_limiter", RateLimiter())


# --- storing ---

def _read_body():
    length = request.content_length
    if length is not None and length > MAX_BODY:
        raise IngestError(413, "the body is larger than %d bytes" % MAX_BODY)
    data = request.stream.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise IngestError(413, "the body is larger than %d bytes" % MAX_BODY)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise IngestError(400, "the body is not JSON")


def _check(events, slug):
    if not events:
        raise IngestError(400, "no events")
    for index, event in enumerate(events):
        if isinstance(event, dict) and "schema" in event and event["schema"] != SCHEMA:
            raise IngestError(400, "unknown schema version %r (this service takes %s)" % (event["schema"], SCHEMA),
                              index=index)
        errors = validate(event)
        if errors:
            raise IngestError(400, "invalid event", index=index, errors=errors)
    for index, event in enumerate(events):
        if event["project"] != slug:
            raise IngestError(403, "event project %r is not this token's project" % event["project"], index=index)


def _upsert_task(db, project_id, item, at):
    row = db.query_one("SELECT id FROM task WHERE project_id = ? AND item = ?", (project_id, item))
    if row:
        db.execute("UPDATE task SET updated_at = ? WHERE id = ?", (at, row["id"]))
        return row["id"]
    task_id = secrets.token_hex(16)
    db.execute("INSERT INTO task (id, project_id, item, updated_at) VALUES (?, ?, ?, ?)",
               (task_id, project_id, item, at))
    return task_id


def _question(event):
    content = event["detail"].get("blocker")
    question = content.get("question") if isinstance(content, dict) else None
    return question[:MAX_QUESTION] if isinstance(question, str) and question.strip() else None


def _upsert_blocker(db, project_id, event, body, at):
    task_id = _upsert_task(db, project_id, event["item"], at)
    blocker_id = event["detail"]["id"]
    question = _question(event)
    row = db.query_one("SELECT id FROM blocker WHERE project_id = ? AND blocker_id = ?", (project_id, blocker_id))
    if row:
        db.execute("UPDATE blocker SET task_id = ?, question = COALESCE(?, question), body = ?, updated_at = ?"
                   " WHERE id = ?", (task_id, question, body, at, row["id"]))
    else:
        db.execute("INSERT INTO blocker (id, project_id, task_id, blocker_id, question, body, status, created_at,"
                   " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (secrets.token_hex(16), project_id, task_id, blocker_id, question, body, "OPEN", at, at))


def _resumed_blocker(event):
    return event.get("type") == "blocker" and isinstance(event.get("detail"), dict)         and event["detail"].get("action") == "resumed" and isinstance(event["detail"].get("id"), str)


def _mark_resumed(db, project_id, event, at):
    db.execute("UPDATE blocker SET status = ?, updated_at = ? WHERE project_id = ? AND blocker_id = ?",
               ("RESUMED", at, project_id, event["detail"]["id"]))


def store(db, project, events, at):
    """Store checked events in one commit; -> (accepted ids, duplicate ids). Raises IngestError(409) on a
    conflicting id, having stored nothing."""
    accepted, duplicates, seen, pending = [], [], {}, []
    for event in events:
        clean = dict(event, detail=redact(event["detail"]))
        body = canonical(clean)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        known = seen.get(clean["id"])
        if known is None:
            row = db.query_one("SELECT project_id, body_hash FROM event WHERE id = ?", (clean["id"],))
            known = (row["project_id"], row["body_hash"]) if row else None
        if known is not None:
            if known != (project["id"], digest):
                raise IngestError(409, "event %s is already stored with a different body" % clean["id"],
                                  id=clean["id"])
            duplicates.append(clean["id"])
            continue
        seen[clean["id"]] = (project["id"], digest)
        pending.append((clean, body, digest))
        accepted.append(clean["id"])
    try:
        for clean, body, digest in pending:
            db.execute("INSERT INTO event (id, project_id, type, ts, body, body_hash, received_at)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (clean["id"], project["id"], clean["type"], clean["time"], body, digest, at))
            if _raised_blocker(clean):
                _upsert_blocker(db, project["id"], clean, body, at)
            elif _resumed_blocker(clean):
                _mark_resumed(db, project["id"], clean, at)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return accepted, duplicates


@bp.post("/api/v1/events")
@tokens.bearer_token("ingest")
def post_events():
    moment = auth.now()
    allowed, wait = limiter().allow(g.token["id"], moment)
    if not allowed:
        response = jsonify(error="too many requests: at most %d a minute for one token" % RATE_LIMIT)
        response.status_code = 429
        response.headers["Retry-After"] = str(wait)
        return response
    try:
        payload = _read_body()
        events = payload if isinstance(payload, list) else [payload]
        _check(events, g.token_project["slug"])
        accepted, duplicates = store(get_db(), g.token_project, events, sessions.iso(moment))
    except IngestError as e:
        response = jsonify(error=e.message, **e.extra)
        response.status_code = e.status
        return response
    return jsonify(accepted=len(accepted), duplicates=len(duplicates), ids=accepted, duplicate_ids=duplicates)
