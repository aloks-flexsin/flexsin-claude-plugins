"""Blocker pages: what the runner is waiting for, and the form where a person answers it.

- `/projects/<slug>/blockers` (any member) lists the project's blockers, waiting ones first.
- `/projects/<slug>/blockers/<blocker_id>` (any member) shows the question, the options with their trade-offs, the
  recommendation, the decisions so far and their delivery state. The content is what the runner sent in the
  `raised` event (already redacted at ingest); a blocker raised by an older runner shows only its id.
- `POST .../answer` (owners, managers and admins) records the answer with `deliveries.record_decision`, which queues
  its delivery to the runner and writes the audit row. Only a blocker that is still waiting (OPEN or ESCALATED) can
  be answered, like `autoblock.py answer`; an option must be one of the blocker's own labels.
"""
import json

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import auth, deliveries, projects
from .access import project_access
from .db import get_db

bp = Blueprint("blockers", __name__)

ANSWERABLE = ("OPEN", "ESCALATED")
MAX_TEXT = 5000
_DESCRIBE = {"OPEN": "waiting for an answer", "ESCALATED": "escalated, waiting for an answer",
             "PENDING_DELIVERY": "answered, waiting for the runner to pick it up", "RESUMED": "the runner resumed the item"}


def _content(row):
    """-> the blocker content the runner sent (a dict, empty when it sent none)."""
    try:
        body = json.loads(row["body"])
    except (TypeError, ValueError):
        return {}
    content = body.get("detail", {}).get("blocker") if isinstance(body, dict) else None
    return content if isinstance(content, dict) else {}


def _options(content):
    out = []
    for o in content.get("options") or []:
        if isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip():
            out.append({"label": o["label"], "tradeoffs": o.get("tradeoffs") if isinstance(o.get("tradeoffs"), str) else ""})
    return out


def _texts(value):
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


_ROW = ("SELECT b.*, t.item AS task FROM blocker b LEFT JOIN task t ON t.id = b.task_id"
        " WHERE b.project_id = ?")


def _row(db, blocker_id):
    row = db.query_one(_ROW + " AND b.blocker_id = ?", (g.project["id"], blocker_id))
    if row is None:
        abort(404)
    return row


def _decisions(db, row):
    rows = db.query("SELECT c.version, c.option_label, c.text, c.created_at, a.email, d.delivery_id, d.status AS delivery"
                    " FROM decision c LEFT JOIN account a ON a.id = c.account_id"
                    " LEFT JOIN delivery d ON d.decision_id = c.id WHERE c.blocker_ref = ? ORDER BY c.version",
                    (row["id"],))
    return [dict(r) for r in rows]


def describe(status):
    return _DESCRIBE.get(status, status.replace("_", " ").lower())


@bp.get("/projects/<slug>/blockers")
@project_access("viewer")
def blockers_page(slug):
    rows = get_db().query(_ROW + " ORDER BY b.created_at DESC, b.blocker_id", (g.project["id"],))
    items = [dict(r, describe=describe(r["status"]), waiting=r["status"] in ANSWERABLE) for r in rows]
    items.sort(key=lambda r: not r["waiting"])            # stable: waiting first, newest first within each group
    return render_template("blockers.html", project=g.project, blockers=items)


def _detail_page(blocker_id, error=None, status=200, form=None):
    db = get_db()
    row = _row(db, blocker_id)
    content = _content(row)
    page = render_template(
        "blocker.html", project=g.project, blocker=row, content=content, options=_options(content),
        assumptions=_texts(content.get("assumptions")), evidence=_texts(content.get("evidence")),
        decisions=_decisions(db, row), describe=describe(row["status"]),
        can_answer=projects.allows(g.project_role, deliveries.DECIDER_ROLE) and row["status"] in ANSWERABLE,
        error=error, form=form or {})
    return page, status


@bp.get("/projects/<slug>/blockers/<blocker_id>")
@project_access("viewer")
def blocker_page(slug, blocker_id):
    return _detail_page(blocker_id)


@bp.post("/projects/<slug>/blockers/<blocker_id>/answer")
@project_access(deliveries.DECIDER_ROLE)
def blocker_answer(slug, blocker_id):
    db = get_db()
    row = _row(db, blocker_id)
    option = request.form.get("option", "").strip()
    text = request.form.get("text", "").strip()
    form = {"option": option, "text": text[:MAX_TEXT]}
    if row["status"] not in ANSWERABLE:
        return _detail_page(blocker_id, "This blocker is %s and cannot be answered again." % describe(row["status"]),
                            409, form)
    if option and option not in [o["label"] for o in _options(_content(row))]:
        return _detail_page(blocker_id, "Choose one of the listed options.", 400, form)
    if not option and not text:
        return _detail_page(blocker_id, "Choose an option or write an answer.", 400, form)
    if len(text) > MAX_TEXT:
        return _detail_page(blocker_id, "The answer is longer than %d characters." % MAX_TEXT, 400, form)
    try:
        deliveries.record_decision(db, g.project, blocker_id, g.account, option or None, text or None,
                                    now=auth.now())
    except deliveries.DecisionRefused:
        abort(404)
    except deliveries.DecisionError as exc:
        return _detail_page(blocker_id, str(exc), 400, form)
    return redirect(url_for("blockers.blocker_page", slug=slug, blocker_id=blocker_id))
