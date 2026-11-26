"""The shared frame of every page (UX audit, PR 2): the top bar's decision badge, the project header and its
counts, the sidebar, and the board's lower panels.

- `nav_badge(db, account)`: decisions waiting for this person across projects (plans they may approve + blockers they
  may answer), for the My work link. Cheap: two queries per project, no event parsing.
- `side`: the sidebar's project list (`side_projects`).
- `notif_count`: the person's unread, unresolved notifications, for the bell in the top bar.
- `project_nav(db, project, account, role, now)`: what every project page's header shows: role, members, whether this
  person can run runners, and the tab counts (open tasks, plans waiting, live runs, waiting blockers).
- `board_lower(db, project, account, now)`: the board's Recent activity (audit rows in words) and runners.
Both are injected into templates by `init_app` whenever `g.project` / `g.account` are set, so routes stay unchanged.
"""
import json

from flask import current_app, g, request

from . import auth, blockers, notifications, projects, push, runners, runs, tasks, ui
from .db import get_db

RECENT = 12


def _decidable(db, project, account, role):
    rows = db.query("SELECT assignee_id FROM project_task WHERE project_id = ? AND status = 'plan_review'", (project["id"],))
    approve = sum(1 for r in rows if r["assignee_id"] == account["id"] or projects.allows(role, tasks.APPROVER_ROLE))
    answer = 0
    if projects.allows(role, "manager"):
        answer = db.query_one("SELECT COUNT(*) AS n FROM blocker WHERE project_id = ? AND status IN (%s)"
                              % ", ".join("?" * len(blockers.ANSWERABLE)), (project["id"],) + blockers.ANSWERABLE)["n"]
    return approve, answer


def nav_badge(db, account):
    total = 0
    for p in projects.visible_to(db, account):
        approve, answer = _decidable(db, p, account, projects.effective_role(db, p, account))
        total += approve + answer
    return total


def project_nav(db, project, account, role, now):
    counts = db.query("SELECT status, COUNT(*) AS n FROM project_task WHERE project_id = ? GROUP BY status", (project["id"],))
    by = {r["status"]: r["n"] for r in counts}
    live = sum(1 for r in runs.summarise(runs.project_events(db, project["id"]), now) if r["state"] == "running")
    waiting = db.query_one("SELECT COUNT(*) AS n FROM blocker WHERE project_id = ? AND status IN (%s)"
                           % ", ".join("?" * len(blockers.ANSWERABLE)), (project["id"],) + blockers.ANSWERABLE)["n"]
    members = db.query_one("SELECT COUNT(*) AS n FROM member WHERE project_id = ?", (project["id"],))["n"]
    return {"role": role, "members": members, "can_run": projects.allows(role, "developer"),
            "can_manage": projects.allows(role, "owner"), "can_tokens": projects.allows(role, "manager"),
            "open_tasks": sum(n for s, n in by.items() if s not in ("done", "rejected")),
            "approval": by.get("plan_review", 0), "live_runs": live, "blockers": waiting,
            "show_slug": projects._slugify(project["name"]) != project["slug"]}


# ---------- Board: recent activity ----------

def _sentence(row, project_slug):
    """One audit row in words for Recent activity, or None to leave it out."""
    d = json.loads(row["detail"] or "{}") if isinstance(row["detail"], str) else (row["detail"] or {})
    who = ui.display_name({"email": row["email"] or "", "name": row["name"]}) if row["email"] else "Someone"
    n = d.get("number")
    task = ("task #%s" % n) if n else "a task"
    texts = {
        "task.create": "%s added %s" % (who, task),
        "task.claim": "%s's runner took %s" % (who, task),
        "task.plan.post": "%s's runner posted a plan for %s" % (who, task),
        "task.report": ("%s's runner finished %s" if d.get("result") == "done" else "%s's runner stopped on %s") % (who, task),
        "task.plan.approve": "%s approved the plan for %s" % (who, task),
        "task.plan.changes": "%s asked for changes to %s" % (who, task),
        "task.plan.reject": "%s rejected %s" % (who, task),
        "task.close": "%s closed %s: %s" % (who, task, d.get("reason") or "no reason given"),
        "task.release":"%s took %s back from its runner" % (who, task),
        "task.reassign": "%s gave %s to %s" % (who, task, ui.display_name(d.get("to")) if d.get("to") else "the pool"),
        "task.take": "%s took %s%s" % (who, task, {"unassigned": " from the pool",
                                                   "no active runner": " (its person had no active runner)",
                                                   "claim expired": " (its runner had gone quiet)"}.get(d.get("reason"), "")),
        "task.done": "%s marked %s done: its pull request was merged" % (who, task),
        "runner.reassign": "%s gave the runner %s to %s" % (who, ui.runner_label(d.get("label") or ""),
                                                           ui.display_name(d.get("to") or "")),
        "task.attachment.add": "%s added a screenshot to %s" % (who, task),
        "task.attachment.delete": "%s deleted a screenshot from %s" % (who, task),
        "member.add": "%s added %s as %s" % (who, ui.display_name(d.get("email") or ""), d.get("role", "member")),
        "member.remove": "%s removed %s" % (who, ui.display_name(d.get("email") or "")),
        "member.role": "%s made %s %s" % (who, ui.display_name(d.get("email") or ""), d.get("to", "")),
        "invite.create": "%s invited %s" % (who, d.get("email") or "someone"),
        "decision.record": "%s answered a blocker" % who,
        "project.repo_url": "%s changed the repository URL" % who,
        "supervisor.deploy.done": "%s deployed %s" % (who, (d.get("sha") or "")[:10] or "the base branch"),
        "supervisor.deploy.failed": "%s's deploy of %s failed" % (who, (d.get("sha") or "")[:10] or "the base branch"),
        "supervisor.deploy.rolled_back": "%s rolled back the deploy of %s" % (who, (d.get("sha") or "")[:10]),
        "project.autonomy": "%s switched the project to %s mode" % (who, d.get("to") or "another"),
        "task.manual": ("%s marked %s manual: a person decides its plan" if d.get("manual")
                        else "%s let the supervisor decide %s") % (who, task),
        "runner.pause": "%s paused the runner %s" % (who, ui.runner_label(d.get("label") or "")),
        "runner.resume": "%s resumed the runner %s" % (who, ui.runner_label(d.get("label") or "")),
        "runner.stop": "%s stopped the runner %s" % (who, ui.runner_label(d.get("label") or "")),
    }
    text = texts.get(row["action"])
    if text and d.get("via") == "api":
        text += " (through the API)"
    link = None
    if n and row["action"].startswith("task."):
        link = ("tasks.task_page", {"slug": project_slug, "number": n})
    return {"text": text, "at": row["at"], "link": link, "tone": _tone(row["action"], d)} if text else None


def _tone(action, detail):
    """The coloured dot beside an activity line (design): done green, stopped or blocker red, waiting amber, runner
    work blue, everything else a hollow ring."""
    if action in ("task.done",) or (action == "task.report" and detail.get("result") == "done"):
        return "ok"
    if action in ("task.report", "task.plan.reject", "supervisor.deploy.failed", "runner.stop"):
        return "bad"
    if action in ("task.plan.post", "task.plan.changes"):
        return "attn"
    if action in ("task.claim", "task.take", "task.plan.approve", "decision.record", "supervisor.deploy.done"):
        return "info"
    return "off"


def board_lower(db, project, account, now):
    """The board's lower panels: Recent activity (audit rows in words, with a tone for their dot) and the runners."""
    rows = db.query("SELECT a.*, acc.email, acc.name FROM audit a LEFT JOIN account acc ON acc.id = a.account_id"
                    " WHERE a.project_id = ? ORDER BY a.at DESC, a.id DESC LIMIT 60", (project["id"],))
    activity = [s for s in (_sentence(r, project["slug"]) for r in rows) if s][:RECENT]
    return {"activity": activity, "runners": runners.views(db, project, account, now)}


SIDE_PROJECTS = 5


def _marker(db, project, account, role, now):
    """The sidebar marker of one project: blocked (a blocker waits), waits (a plan this person may approve), running
    (a live claim on a task being planned or built), else None. A few cheap queries; no event parsing."""
    approve, _ = _decidable(db, project, account, role)
    waiting = db.query_one("SELECT COUNT(*) AS n FROM blocker WHERE project_id = ? AND status IN (%s)"
                           % ", ".join("?" * len(blockers.ANSWERABLE)), (project["id"],) + blockers.ANSWERABLE)["n"]
    if waiting:
        return "blocked"
    if approve:
        return "waits"
    rows = db.query("SELECT * FROM project_task WHERE project_id = ? AND status IN ('planning', 'building',"
                    " 'changes_requested')", (project["id"],))
    return "running" if any(tasks.claim_live(dict(r), now) for r in rows) else None


def side_projects(db, account, current_slug=None, now=None):
    """The sidebar: its Projects list (up to SIDE_PROJECTS the person can see, the open one first, and the total) and
    My work's per-project filters with a marker each (only when the person has more than one project)."""
    rows = sorted(projects.visible_to(db, account), key=lambda p: p["slug"] != current_slug)   # stable sort
    out = {"items": [{"name": p["name"], "slug": p["slug"]} for p in rows[:SIDE_PROJECTS]], "total": len(rows),
           "mine": [], "markers": False}
    if len(rows) > 1:
        now = now or auth.now()
        shown = sorted(rows, key=lambda p: p["name"].lower())[:SIDE_PROJECTS]
        out["mine"] = [{"name": p["name"], "slug": p["slug"],
                        "marker": _marker(db, p, account, projects.effective_role(db, p, account), now)} for p in shown]
        out["markers"] = any(m["marker"] for m in out["mine"])
    return out


def init_app(app):
    @app.context_processor
    def _frame():
        account = g.get("account")
        if account is None or request.path.startswith("/api/"):
            return {}
        db = get_db()
        out = {"nav_badge": nav_badge(db, account), "nav_endpoint": request.endpoint or "",
               "notif_count": notifications.unread_count(db, account["id"]),
               "side": side_projects(db, account, g.project["slug"] if g.get("project") else None)}
        # Queue item A1: the "turn on push" bar on every signed-in page except the Account page (its own section).
        if push.configured() and request.endpoint != "admin.account_page":
            out["push_ask_key"] = (current_app.config.get("CENTRAL_VAPID_PUBLIC_KEY") or "").strip()
        project = g.get("project")
        if project is not None:
            out["pnav"] = project_nav(db, project, account, g.get("project_role"), auth.now())
        return out
