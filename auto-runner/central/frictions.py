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
"""
from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import audit, auth, ingest, sessions, ui
from .admin import _require_admin
from .db import get_db

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


def record(db, project, kind, subject, at):
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
                           state=state, error=error, max_reason=MAX_REASON), status


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
