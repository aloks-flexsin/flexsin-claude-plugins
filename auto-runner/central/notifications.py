"""In-app notifications: a row per person for each thing that waits on them (a plan to approve, changes asked, a task
given to them, a blocker to answer).

`notify` adds rows for the given accounts, but only for enabled **members** of the project (a site admin who is not a
member gets none; they still see everything on My work). A notification is identified by its `dedupe` key per person:
while one with the same key is still unresolved, the same event again adds nothing. `resolve` closes every unresolved
notification with a key once the thing no longer waits (the plan is decided, the blocker answered).

Pages (queue item N2): the top bar's bell carries `unread_count` (from `shell`'s context processor);
`GET /notifications` lists the person's own, waiting ones first, then resolved ones marked "Done";
`POST /notifications/<id>/open` marks one read and goes to its link (same-site only); `POST /notifications/read-all`.

Push (queue item N5): `notify` also pushes each new notification to the person's browsers (`push.send_to_account`)
and records `push_state` (`off` without VAPID keys or subscriptions, `sent`, or `failed` with the reason in
`push_detail`). Pushing never raises into the caller. The push goes out before the caller commits, so if the caller
then rolls back, the person may have been pushed about a notification that is not stored.

Delivery (queue item N6): `delivery(row)` puts `push_state` into words ("in-app", "push sent", "push failed: <reason>",
"push off"), shown on each row of `/notifications` and on the admins' log `GET /admin/notifications` (`recent`).

Neither function commits: the caller commits with the change that caused the notification, so both land together.

Keys used by the service:
  plan     task:<project id>:<number>:plan       resolved by a decision on the plan, or a reassignment
  changes  task:<project id>:<number>:changes    resolved when the next plan is posted, or a reassignment
  assigned task:<project id>:<number>:assigned
  blocker  blocker:<project id>:<blocker id>     resolved by an answer (deliveries.record_decision)
"""
import secrets

from flask import Blueprint, abort, g, redirect, render_template, url_for

from . import auth, projects, push, sessions, ui
from .db import get_db

bp = Blueprint("notifications", __name__)

KINDS = ("plan", "changes", "assigned", "blocker")
MAX_TEXT, MAX_LINK = 500, 500


def _at(now):
    """`now` as stored: a datetime, or an ISO time text the caller already made."""
    return now if isinstance(now, str) else sessions.iso(now)


def task_key(project, number, kind):
    return "task:%s:%d:%s" % (project["id"], number, kind)


def blocker_key(project, blocker_id):
    return "blocker:%s:%s" % (project["id"], blocker_id)


def task_link(project, number):
    return "/projects/%s/tasks/%d" % (project["slug"], number)


def blocker_link(project, blocker_id):
    return "/projects/%s/blockers/%s" % (project["slug"], blocker_id)


def task_text(kind, row):
    lead = {"plan": "Plan waiting", "changes": "Changes asked on", "assigned": "Assigned to you"}[kind]
    sep = ":" if kind != "changes" else ""
    return "%s%s #%d %s" % (lead, sep, row["number"], row["title"])


def members_with(db, project, min_role):
    """Account ids of the enabled members whose role is at least `min_role`."""
    return [m["account_id"] for m in projects.members(db, project["id"])
            if not m["disabled"] and projects.allows(m["role"], min_role)]


def notify(db, account_ids, project, kind, text, link, dedupe, now):
    """Add a notification for each account that is an enabled member of `project` and has no unresolved one with this
    `dedupe` key; -> the account ids notified. Does not commit."""
    if kind not in KINDS:
        raise ValueError("unknown notification kind: %r" % (kind,))
    members = set(members_with(db, project, "viewer"))
    at = _at(now)
    done = []
    for account_id in dict.fromkeys(a for a in account_ids if a):     # keep order, drop repeats
        if account_id not in members:
            continue
        if db.query_one("SELECT id FROM notification WHERE account_id = ? AND dedupe = ? AND resolved_at IS NULL",
                        (account_id, dedupe)):
            continue
        notification_id, body, target = secrets.token_hex(16), (text or "")[:MAX_TEXT], (link or "")[:MAX_LINK]
        db.execute("INSERT INTO notification (id, account_id, project_id, kind, text, link, dedupe, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (notification_id, account_id, project["id"], kind, body, target, dedupe, at))
        _push(db, notification_id, account_id, project, body, target, dedupe, at)
        done.append(account_id)
    return done


def _push(db, notification_id, account_id, project, text, link, dedupe, at):
    """Push one new notification to the person's browsers and record how it went; never raises."""
    try:
        title = project["name"] or "auto-runner"
    except (KeyError, IndexError, TypeError):
        title = "auto-runner"
    state, detail = push.send_to_account(db, account_id, push.payload_for(title, text, link, dedupe), at)
    try:
        db.execute("UPDATE notification SET push_state = ?, push_detail = ? WHERE id = ?",
                   (state, detail, notification_id))
    except Exception:                             # noqa: BLE001 - recording must not break the caller either
        pass


def resolve(db, dedupe, now):
    """Mark every unresolved notification with this key resolved; -> how many. Does not commit."""
    cur = db.execute("UPDATE notification SET resolved_at = ? WHERE dedupe = ? AND resolved_at IS NULL",
                     (_at(now), dedupe))
    return cur.rowcount


def unread_count(db, account_id):
    """Notifications still waiting on the person that they have not opened."""
    row = db.query_one("SELECT COUNT(*) AS n FROM notification WHERE account_id = ? AND read_at IS NULL"
                       " AND resolved_at IS NULL", (account_id,))
    return row["n"] or 0


def for_account(db, account_id, limit=200):
    """The person's notifications with their project: waiting ones first, newest first within each."""
    return db.query("SELECT n.*, p.slug AS project_slug, p.name AS project_name FROM notification n"
                    " JOIN project p ON p.id = n.project_id WHERE n.account_id = ?"
                    " ORDER BY CASE WHEN n.resolved_at IS NULL THEN 0 ELSE 1 END, n.created_at DESC, n.id DESC"
                    " LIMIT %d" % int(limit), (account_id,))


def delivery(row):
    """How a notification reached the person, in words: "push sent", "push failed: <reason>", "push off", or
    "in-app" when no push was tried (rows from before pushing existed)."""
    state = row["push_state"]
    if state == "sent":
        return "push sent"
    if state == "failed":
        reason = (row["push_detail"] or "").strip()
        return "push failed: %s" % reason if reason else "push failed"
    if state == "off":
        return "push off"
    return "in-app"


def recent(db, account_id=None, kind=None, limit=200):
    """The newest notifications across people (for admins), with recipient and project; optionally only one
    account's and/or one kind's."""
    where, args = [], []
    if account_id:
        where.append("n.account_id = ?")
        args.append(account_id)
    if kind:
        where.append("n.kind = ?")
        args.append(kind)
    sql = ("SELECT n.*, p.slug AS project_slug, p.name AS project_name, a.email AS account_email,"
           " a.name AS account_name FROM notification n JOIN project p ON p.id = n.project_id"
           " JOIN account a ON a.id = n.account_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.query(sql + " ORDER BY n.created_at DESC, n.id DESC LIMIT %d" % int(limit), tuple(args))


def find_own(db, account_id, notification_id):
    """The person's notification with this id, else None (another person's id is treated as unknown)."""
    return db.query_one("SELECT * FROM notification WHERE id = ? AND account_id = ?", (notification_id, account_id))


def mark_read(db, account_id, notification_id, now):
    """Mark one of the person's notifications read (once; the first read time is kept). Does not commit."""
    db.execute("UPDATE notification SET read_at = ? WHERE id = ? AND account_id = ? AND read_at IS NULL",
               (_at(now), notification_id, account_id))


def mark_all_read(db, account_id, now):
    """Mark every unread notification of the person read; -> how many. Does not commit."""
    cur = db.execute("UPDATE notification SET read_at = ? WHERE account_id = ? AND read_at IS NULL",
                     (_at(now), account_id))
    return cur.rowcount


# ---------- pages ----------

@bp.get("/notifications")
@auth.login_required
def notifications_page():
    rows = [dict(r, delivery=delivery(r)) for r in for_account(get_db(), g.account["id"])]
    return render_template("notifications.html", waiting=[r for r in rows if r["resolved_at"] is None],
                           done=[r for r in rows if r["resolved_at"] is not None])


@bp.post("/notifications/<notification_id>/open")
@auth.login_required
def notification_open(notification_id):
    db = get_db()
    row = find_own(db, g.account["id"], notification_id)
    if row is None:
        abort(404)
    mark_read(db, g.account["id"], notification_id, auth.now())
    db.commit()
    return redirect(ui.safe_next(row["link"]) or url_for("notifications.notifications_page"))


@bp.post("/notifications/read-all")
@auth.login_required
def notifications_read_all():
    db = get_db()
    mark_all_read(db, g.account["id"], auth.now())
    db.commit()
    return redirect(url_for("notifications.notifications_page"))
