"""The project's runs page: who is running what, built only from the events the runners send (`event.v1` bodies stored by
`POST /api/v1/events`). No table of its own: each run is summarised from its events when the page is shown.

A run is the events that share one `run` id. Its start event (`run_start`, from plugin 0.17.0) names who ran it
(`user`, `account`), the branch and the base; `item_state` events give each item's latest state; `tests` the last
independent test result; `publish` the pushed branch and pull request; `run_end` how it ended. A run with no end is
*running* while its last event is less than STALE old, and *silent* after that (the runner stopped without saying so:
a crash, a closed laptop, a lost connection). Only the newest MAX_EVENTS events of a project are read.
"""
import datetime
import json
import re

from flask import Blueprint, g, render_template, request

from . import identities
from .access import project_access
from .db import get_db

bp = Blueprint("runs", __name__)

MAX_EVENTS = 5000
STALE = datetime.timedelta(hours=2)
ACCOUNT_CHECKS = ("match", "mismatch", "not_set", "unknown")
# the service's own copy of the runner's WINDOW_LABELS (auto_run.py)
WINDOW_LABELS = {"five_hour": "5-hour", "seven_day": "7-day"}
SHOWN_TYPES =("run_start", "run_end", "publish", "item_state", "tests", "blocker")


def _when(text):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def _short_time(text):
    t = _when(text)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else ""


def _display(r):
    """Short forms for the page: the branch without the `<user>/<account>-claude-auto-runner/` part the Who column already
    shows, times to the minute, and `#N` for a pull request."""
    prefix = "%s/%s-claude-auto-runner/" % (r["user"], r["account"]) if r["user"] and r["account"] else None
    branch = r["branch"] or ""
    r["branch_short"] = branch[len(prefix):] if prefix and branch.startswith(prefix) else branch
    r["started_at"], r["last_at"] = _short_time(r["started"]), _short_time(r["last"])
    tail = (r["pr_url"] or "").rstrip("/").rsplit("/", 2)
    r["pr_label"] = "#" + tail[2] if len(tail) == 3 and tail[1] == "pull" and tail[2].isdigit() else r["pr_url"]
    return r


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _usage_stop(usage, ended):
    """The `usage` of a `run_end` -> {text} for the page, or None when it is not well formed."""
    if not isinstance(usage, dict) or usage.get("window") not in WINDOW_LABELS:
        return None
    util, reset = usage.get("utilization"), usage.get("resets_at")
    if not _number(util) or not 0 <= util <= 1 or not isinstance(reset, int) or isinstance(reset, bool):
        return None
    try:
        at = datetime.datetime.fromtimestamp(reset, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    end = _when(ended)
    other_day = end is not None and end.astimezone(datetime.timezone.utc).date() != at.date()
    when = at.strftime("%d %b %H:%M UTC").lstrip("0") if other_day else at.strftime("%H:%M UTC")
    return "Stopped at %d%% of the %s limit (resets %s)" % (round(util * 100), WINDOW_LABELS[usage["window"]], when)


def summarise(events, now):
    """`events`: event.v1 dicts of one project, any order -> run summaries, newest start first."""
    runs = {}
    for e in sorted(events, key=lambda e: (e.get("time") or "", e.get("id") or "")):
        run_id, kind, detail = e.get("run"), e.get("type"), e.get("detail") or {}
        if not isinstance(run_id, str) or not isinstance(detail, dict):
            continue
        r = runs.setdefault(run_id, {
            "run": run_id, "user": None, "account": None, "branch": None, "base": None, "started": e.get("time"),
            "last": e.get("time"), "ended": None, "exit": None, "reason": None, "items": {}, "current": None,
            "tests": None, "pushed": None, "pr_url": None, "publish_error": None, "blockers": 0, "signed_in": None,
            "signed_in_name": None, "task": None, "account_check": None, "usage_stop": None})
        r["signed_in"] = r["signed_in"] or e.get("_signed_in")
        r["signed_in_name"] = r["signed_in_name"] or e.get("_signed_in_name")
        r["last"] = e.get("time") or r["last"]
        if kind in ("run_start", "run_end") and isinstance(detail.get("task"), int) and not isinstance(detail["task"], bool):
            r["task"] = detail["task"]                    # the central task this run builds (plugin 0.21.0+)
        if kind == "run_start":
            r["started"] = e.get("time")
            for k in ("user", "account", "branch", "base"):
                if isinstance(detail.get(k), str) and detail[k]:
                    r[k] = detail[k]
            if detail.get("account_check") in ACCOUNT_CHECKS:    # anything else (or an older runner) is ignored
                r["account_check"] = detail["account_check"]
        elif kind == "run_end":
            r["ended"], r["exit"], r["reason"] = e.get("time"), detail.get("exit"), detail.get("reason")
            r["usage_stop"] = _usage_stop(detail.get("usage"), e.get("time"))
        elif kind == "item_state" and e.get("item"):
            r["items"][e["item"]] = detail.get("to")
            r["current"] = e["item"] if detail.get("to") == "running" else (
                None if r["current"] == e["item"] else r["current"])
        elif kind == "tests":
            r["tests"] = detail.get("result")
        elif kind == "publish":
            url = detail.get("pr_url")
            # event text comes from runners: only an https:// address ever becomes a link
            r["pushed"], r["pr_url"] = detail.get("pushed") is True, url if isinstance(url, str) and url.startswith("https://") else None
            r["publish_error"] = detail.get("error") or detail.get("pr_error")
            r["branch"] = r["branch"] or detail.get("branch")
        elif kind == "blocker" and detail.get("action") == "raised":
            r["blockers"] += 1
    out = []
    for r in runs.values():
        states = list(r["items"].values())
        r["done"] = states.count("done")
        r["blocked"] = states.count("blocked")
        if r["ended"]:
            r["state"] = "finished" if r["exit"] == 0 else "stopped"
        else:
            last = _when(r["last"])
            r["state"] = "running" if last is not None and now - last < STALE else "silent"
        r["who"] = (r["user"] + (" via %s-claude-auto-runner" % r["account"] if r["account"] else "")) if r["user"] \
            else "unknown (runner older than 0.17.0)"
        out.append(_display(r))
    out.sort(key=lambda r: (r["started"] or "", r["run"]), reverse=True)
    return out


ITEM_ID = re.compile(r"^\s*- \[[ xX]\] \*\*([A-Za-z][A-Za-z0-9-]*?\d+)\b", re.M)


def item_ids(items_text):
    """The queue item ids of a task's approved items ('- [ ] **E1 Title.**' -> 'E1'), in order."""
    return ITEM_ID.findall(items_text or "")


ITEM_LINE = re.compile(r"^\s*- \[([ xX])\] \*\*([A-Za-z][A-Za-z0-9-]*?\d+)\b\s*(.*?)\*\*\s*(.*)$")
ACCEPTANCE = re.compile(r"^\s*\*Acceptance:\*\s*(.*)$")


def parse_items(items_text):
    """A task's queue items as a checklist -> [{id, title, text, acceptance, ticked}], in order. Lines that are not
    items or their acceptance line are kept as the item's text; the page escapes all of it."""
    out = []
    for line in (items_text or "").splitlines():
        m = ITEM_LINE.match(line)
        if m:
            out.append({"id": m.group(2), "title": m.group(3).strip().rstrip("."), "text": m.group(4).strip(),
                        "acceptance": "", "ticked": m.group(1) != " "})
        elif out and line.strip():
            a = ACCEPTANCE.match(line)
            if a:
                out[-1]["acceptance"] = a.group(1).strip()
            else:
                out[-1]["text"] = (out[-1]["text"] + " " + line.strip()).strip()
    return out


def task_progress(task_runs, items_text):
    """A task's build progress from its runs (newest first) over its own items -> {total, done, blocked, current,
    latest, states} or None when no run has worked on it yet. An item counts as done once any run finished it;
    `states` is each item's newest state (queued, running, done, blocked, ...)."""
    if not task_runs:
        return None
    ids = item_ids(items_text)
    state = {}
    for r in reversed(task_runs):                      # oldest first, so the newest state wins
        state.update(r["items"])
    mine = ids or list(state)
    latest = task_runs[0]
    return {"total": len(mine), "done": sum(1 for i in mine if state.get(i) == "done"),
            "blocked": [i for i in mine if state.get(i) == "blocked"], "current": latest["current"], "latest": latest,
            "states": {i: state[i] for i in mine if i in state}}


def for_task(db, project_id, number, now):
    """The runs that worked on task `number`, newest first."""
    return [r for r in summarise(project_events(db, project_id), now) if r["task"] == number]


def project_events(db, project_id, limit=MAX_EVENTS):
    rows = db.query("SELECT e.body, a.email, a.name FROM event e LEFT JOIN account a ON a.id = e.account_id"
                    " WHERE e.project_id = ? AND e.type IN (%s) ORDER BY e.ts DESC LIMIT %d"
                    % (", ".join("?" * len(SHOWN_TYPES)), int(limit)), (project_id,) + SHOWN_TYPES)
    events = []
    for row in rows:
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict):
            events.append(dict(body, _signed_in=row["email"], _signed_in_name=row["name"]))    # the account the service verified (personal token)
    return events


OUTCOMES = (("running", "Running"), ("stopped", "Stopped"), ("silent", "Silent"), ("finished", "Finished"))


@bp.get("/projects/<slug>/runs")
@project_access("viewer")
def runs_page(slug):
    """Live runs as cards, then the history with outcome chips (`?outcome=`): what ended, why in words, and what to do
    next for a run that stopped."""
    from . import auth, runners
    now = auth.now()
    runs = summarise(project_events(get_db(), g.project["id"]), now)
    outcome = request.args.get("outcome")
    if outcome not in [k for k, _ in OUTCOMES]:
        outcome = None
    claude = request.args.get("claude") or None
    claude_accounts = identities.team_view(get_db(), g.project["id"])["claude_accounts"]
    scoped = [r for r in runs if claude is None or r["account"] == claude]    # unknown value: no runs, no error
    counts = {k: sum(1 for r in scoped if r["state"] == k) for k, _ in OUTCOMES}
    history = [r for r in scoped if r["state"] != "running" and (outcome is None or r["state"] == outcome)]
    if outcome == "running":
        history = []
    return render_template("runs.html", project=g.project, runs=runs, history=history, outcomes=OUTCOMES,
                           outcome=outcome, counts=counts, claude=claude, claude_accounts=claude_accounts, now=sessions_iso(now),
                           runners=runners.views(get_db(), g.project, g.account, now),
                           active=[r for r in runs if r["state"] == "running"], stale_hours=int(STALE.total_seconds() // 3600))


def sessions_iso(moment):
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
