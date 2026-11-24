"""My work (`/my`): one page across every project the signed-in person can see (all projects for an admin).

- **Waiting for you:** plans in `plan_review` this person may decide (theirs, or a project where they are a manager,
  owner or admin), each with Approve on the card when the plan is short (INLINE_ITEMS items or fewer), and waiting
  blockers in projects where they answer them.
- **In progress:** their own unfinished tasks first, then every other task a runner is on, each once, with build
  progress and a warning when its runner went quiet.
- **Recently done:** the last finished tasks, with their pull requests.
`?project=<slug>` narrows the page to one project. Built from the same rows as the project pages; nothing is stored.
"""
from flask import Blueprint, g, render_template, request

from . import auth, blockers, projects, runs, tasks
from .db import get_db

bp = Blueprint("mywork", __name__)

RECENT = 8
INLINE_ITEMS = 2           # a plan this short is approved from its card; longer ones are read first


def collect(db, account, moment, only=None):
    """-> {approve, blockers, waiting, progress, done, projects}. `waiting` merges plans and blockers, oldest first;
    `progress` is mine first, then the team's live work; `only` is a project slug or None."""
    out = {"approve": [], "blockers": [], "mine": [], "team": [], "done": [], "projects": []}
    for p in projects.visible_to(db, account):
        out["projects"].append(p)
        if only and p["slug"] != only:
            continue
        role = projects.effective_role(db, p, account)
        task_runs = None
        for t in tasks.for_project(db, p["id"]):
            t = dict(t, project=p, describe=tasks.DESCRIBE.get(t["status"], t["status"]), live=tasks.claim_live(t, moment),
                     items_count=len(runs.item_ids(t["items"])))
            t["quiet"] = bool(t["claim_id"]) and not t["live"] and t["status"] in tasks.RUNNER_SIDE
            if t["status"] == "building" or t["live"]:
                if task_runs is None:
                    task_runs = runs.summarise(runs.project_events(db, p["id"]), moment)
                t["progress"] = runs.task_progress([r for r in task_runs if r["task"] == t["number"]], t["items"])
            else:
                t["progress"] = None
            if t["status"] == "plan_review" and (t["assignee_id"] == account["id"] or projects.allows(role, tasks.APPROVER_ROLE)):
                out["approve"].append(t)
            elif t["assignee_id"] == account["id"] and t["status"] not in ("done", "rejected"):
                out["mine"].append(t)
            elif t["live"] and t["status"] in tasks.RUNNER_SIDE:
                out["team"].append(t)
            if t["status"] == "done":
                out["done"].append(t)
        if projects.allows(role, "manager"):
            for b in db.query(blockers._ROW + " AND b.status IN (%s) ORDER BY b.created_at"
                              % ", ".join("?" * len(blockers.ANSWERABLE)), (p["id"],) + blockers.ANSWERABLE):
                out["blockers"].append(dict(b, project=p))
    out["waiting"] = sorted([{"kind": "plan", "task": t, "at": t["updated_at"]} for t in out["approve"]]
                            + [{"kind": "blocker", "blocker": b, "at": b["created_at"]} for b in out["blockers"]],
                            key=lambda w: w["at"] or "")
    order = {"building": 0, "planning": 1, "changes_requested": 2, "approved": 3, "open": 4}
    out["progress"] = sorted(out["mine"], key=lambda t: (order.get(t["status"], 9), -t["number"])) + out["team"]
    out["done"] = sorted(out["done"], key=lambda t: t["updated_at"], reverse=True)[:RECENT]
    return out


@bp.get("/my")
@auth.login_required
def my_page():
    only = (request.args.get("project") or "").strip() or None
    data = collect(get_db(), g.account, auth.now(), only)
    if only and not any(p["slug"] == only for p in data["projects"]):
        only = None                                     # an unknown or hidden project shows like "All"
        data = collect(get_db(), g.account, auth.now())
    return render_template("my_work.html", account=g.account, only=only, inline_items=INLINE_ITEMS, **data)
