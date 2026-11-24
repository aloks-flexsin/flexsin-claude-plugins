"""My work (`/my`): one page across every project the signed-in person can see (all projects for an admin).

- **Plans waiting for my approval:** tasks in `plan_review` that this person may decide (theirs, or a project where they
  are a manager, owner or admin).
- **Blockers I can answer:** waiting blockers in projects where they are a manager, owner or admin.
- **My tasks:** assigned to them and not finished, with build progress for the ones being built.
- **Being worked on now:** every task with a live claim, with its runner and progress, so people and admins see what
  each runner is doing.
- **Recently done:** the last finished tasks, with their pull requests.
Built from the same rows as the project pages; nothing of its own is stored.
"""
from flask import Blueprint, g, render_template

from . import auth, blockers, projects, runs, tasks
from .db import get_db

bp = Blueprint("mywork", __name__)

RECENT = 10


def collect(db, account, moment):
    out = {"approve": [], "blockers": [], "mine": [], "active": [], "done": []}
    for p in projects.visible_to(db, account):
        role = projects.effective_role(db, p, account)
        rows = tasks.for_project(db, p["id"])
        task_runs = None
        for t in rows:
            t = dict(t, project=p, describe=tasks.DESCRIBE.get(t["status"], t["status"]), live=tasks.claim_live(t, moment))
            if t["status"] == "building" or t["live"]:
                if task_runs is None:
                    task_runs = runs.summarise(runs.project_events(db, p["id"]), moment)
                t["progress"] = runs.task_progress([r for r in task_runs if r["task"] == t["number"]], t["items"])
            else:
                t["progress"] = None
            if t["status"] == "plan_review" and (t["assignee_id"] == account["id"] or projects.allows(role, tasks.APPROVER_ROLE)):
                out["approve"].append(t)
            if t["assignee_id"] == account["id"] and t["status"] not in ("done", "rejected"):
                out["mine"].append(t)
            if t["live"] and t["status"] in ("planning", "building", "changes_requested", "approved"):
                out["active"].append(t)
            if t["status"] == "done":
                out["done"].append(t)
        if projects.allows(role, "manager"):
            for b in db.query(blockers._ROW + " AND b.status IN (%s) ORDER BY b.created_at"
                              % ", ".join("?" * len(blockers.ANSWERABLE)), (p["id"],) + blockers.ANSWERABLE):
                out["blockers"].append(dict(b, project=p))
    out["done"] = sorted(out["done"], key=lambda t: t["updated_at"], reverse=True)[:RECENT]
    return out


@bp.get("/my")
@auth.login_required
def my_page():
    data = collect(get_db(), g.account, auth.now())
    return render_template("my_work.html", account=g.account, **data)
