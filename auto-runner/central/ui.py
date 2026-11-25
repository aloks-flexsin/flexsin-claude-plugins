"""Shared presentation for every page (UX audit, PR 1): one way to show time, status, people, runners and reasons, a
small Markdown renderer for plans, and the confirm step for actions that cannot be undone.

- Filters (`init_app`): `ago` ("12 min ago", from the injectable clock), `when_abs` ("24 Nov, 09:58"),
  `reason_text` (runner stop reasons in words), `runner_label` ("alok · bhawish-claude-auto-runner" -> "Alok (bhawish
  account)"), `display_name`, `initials`, `md`.
- `STATUS`: (kind, value) -> (label, tone, shape). Tones: neutral, info, attention (a person must act), ok, bad.
  Shapes make status readable without colour (ring, dot, square).
- `md(text)`: escape-first Markdown subset (headings, bullet and numbered lists, **bold**, *italic*, `code`, fenced
  code blocks, pipe tables, http(s) links, paragraphs). Nothing else of the input becomes markup; no HTML.
- `confirm_or_page(...)`: a destructive POST without `confirm=1` renders confirm.html (the same form again with
  `confirm=1`, saying what will happen); with JavaScript the dialog in app.js adds `confirm=1` before posting.
"""
import datetime
import html
import re

from flask import render_template, request

from . import auth, identities

# ---------- time ----------


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        t = ts
    else:
        try:
            t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def ago(ts, now=None):
    """'just now', '12 min ago', '3 h ago', 'yesterday', '4 days ago', else the date."""
    t = _parse(ts)
    if t is None:
        return ""
    now = now or auth.now()
    s = (now - t).total_seconds()
    if s < 0:
        return "in " + _span(-s)
    if s < 45:
        return "just now"
    if s < 172800 and s >= 86400 and (now.date() - t.astimezone(datetime.timezone.utc).date()).days == 1:
        return "yesterday"
    if s < 7 * 86400:
        return _span(s) + " ago"
    return when_abs(t)


def _span(s):
    if s < 3600:
        return "%d min" % max(1, round(s / 60))
    if s < 86400:
        return "%d h" % round(s / 3600)
    return "%d days" % round(s / 86400)


def when_abs(ts):
    """'24 Nov, 09:58' (UTC); the year is added when it is not this year."""
    t = _parse(ts)
    if t is None:
        return ""
    t = t.astimezone(datetime.timezone.utc)
    fmt = "%d %b, %H:%M" if t.year == auth.now().year else "%d %b %Y, %H:%M"
    return t.strftime(fmt).lstrip("0")


def iso(ts):
    t = _parse(ts)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def duration(start, end):
    a, b = _parse(start), _parse(end)
    if a is None or b is None or b < a:
        return ""
    s = (b - a).total_seconds()
    if s < 3600:
        return "%d min" % max(1, round(s / 60))
    return "%d h %02d" % (s // 3600, (s % 3600) // 60)


# ---------- status, reasons, people ----------

STATUS = {
    # tasks
    ("task", "open"): ("Open", "neutral", "ring"),
    ("task", "planning"): ("Planning", "info", "dot"),
    ("task", "plan_review"): ("Needs approval", "attention", "dot"),
    ("task", "changes_requested"): ("Changes requested", "attention", "dot"),
    ("task", "approved"): ("Approved", "info", "ring"),
    ("task", "queued"): ("Queued", "info", "ring"),
    ("task", "building"): ("Building", "info", "dot"),
    ("task", "done"): ("Done", "ok", "dot"),
    ("task", "rejected"): ("Rejected", "neutral", "dot"),
    # a task's queue items
    ("item", "queued"): ("Not started", "neutral", "ring"),
    ("item", "running"): ("In progress", "info", "dot"),
    ("item", "done"): ("Done", "ok", "dot"),
    ("item", "blocked"): ("Blocked", "attention", "square"),
    ("item", "failed"): ("Failed", "bad", "square"),
    # tokens and accounts
    ("token", "active"): ("Active", "ok", "dot"),
    ("token", "revoked"): ("Revoked", "neutral", "square"),
    ("account", "enabled"): ("Enabled", "ok", "dot"),
    ("account", "disabled"): ("Disabled", "neutral", "square"),
    ("account", "invited"): ("Invited", "attention", "dot"),
    # runs
    ("run", "running"): ("Running", "info", "dot"),
    ("run", "finished"): ("Finished", "ok", "dot"),
    ("run", "stopped"): ("Stopped", "bad", "square"),
    ("run", "silent"): ("Silent", "bad", "square"),
    # watching runners (runners.words)
    ("runner", "watching"): ("Watching for work", "info", "ring"),
    ("runner", "planning"): ("Planning", "info", "dot"),
    ("runner", "building"): ("Building", "info", "dot"),
    ("runner", "paused"): ("Paused", "attention", "square"),
    ("runner", "silent"): ("Silent", "bad", "square"),
    # blockers
    ("blocker", "OPEN"): ("Waiting for an answer", "attention", "dot"),
    ("blocker", "ESCALATED"): ("Escalated", "attention", "dot"),
    ("blocker", "PENDING_DELIVERY"): ("Answered", "info", "ring"),
    ("blocker", "DELIVERED"): ("Answer picked up", "info", "dot"),
    ("blocker", "RESUMED"): ("Resumed", "ok", "dot"),
    # runner friction signatures (frictions.py)
    ("friction", "watching"): ("Watching", "info", "ring"),
    ("friction", "drafted"): ("Drafted", "attention", "dot"),
    ("friction", "muted"): ("Muted", "neutral", "square"),
    ("friction", "fixed"): ("Fixed", "ok", "dot"),
}

REASONS = {
    "queue_empty": "finished", "max_iterations": "finished (iteration limit)", "no_ready_items": "nothing ready to do",
    "red": "tests failing", "no_progress": "no progress twice in a row", "claim_lost": "taken back by an admin",
    "usage_limit": "paused: Claude usage limit", "leftovers": "uncommitted files left", "secret_leak": "a secret was in a commit",
    "stop_file": "stopped by a person", "docs": "docs check failed", "changelog": "no changelog sentence",
    "journey": "a user journey failed", "error": "the runner hit an error",
    "usage_threshold": "stopped early: plan usage threshold",
}
NEXT_STEP = {
    "red": "Fix the failing test, then run again.",
    "no_progress": "Read the last iteration's log (/auto-runner:log), adjust the item, then run again.",
    "claim_lost": "Nothing to do: the task went back to its owner.",
    "usage_limit": "Start the runner again after the usage limit resets.",
    "usage_threshold": "Start the runner again after the limit resets.",
    "leftovers": "Commit or remove the files left in the worktree, then run again.",
    "secret_leak": "Remove the secret from the commit, rotate it, then run again.",
    "docs": "Update the docs the doc map names, then run again.",
    "changelog": "Write the item's change sentence, then run again.",
    "journey": "Open the journey's evidence, fix the step, then run again.",
    "error": "Read the run log (/auto-runner:log).",
}


def status(kind, value):
    label, tone, shape = STATUS.get((kind, value), (str(value or "").replace("_", " ").capitalize(), "neutral", "dot"))
    return {"label": label, "tone": tone, "shape": shape}


def reason_text(reason):
    return REASONS.get(reason or "", (reason or "").replace("_", " "))


def next_step(reason):
    return NEXT_STEP.get(reason or "", "")


def display_name(account_or_email, name=None):
    """The account's name, else its email's local part made readable ('alok.sharma' -> 'Alok Sharma')."""
    if isinstance(account_or_email, dict):
        name = name or account_or_email.get("name")
        email = account_or_email.get("email") or ""
    else:
        email = account_or_email or ""
    if name and str(name).strip():
        return str(name).strip()
    local = email.split("@", 1)[0]
    return " ".join(p.capitalize() for p in re.split(r"[._+-]+", local) if p) or email


def initials(text):
    parts = [p for p in re.split(r"[\s._@+-]+", text or "") if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def runner_label(label):
    """'alok/bhawish-claude-auto-runner' -> 'Alok (bhawish account)'; anything else unchanged."""
    pair = identities.split_runner(label)
    if not pair:
        return label or ""
    return "%s (%s account)" % (display_name(pair[0]), pair[1])


# ---------- Markdown subset ----------

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_EM = re.compile(r"(?<![*\w])\*(?![\s*])([^*]+?)(?<![\s*])\*(?![*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def _inline(text):
    """Escape first; code spans and links are set aside so nothing else is applied inside them."""
    out = html.escape(text.replace("\x00", ""), quote=True)
    stash = []

    def keep(markup):
        stash.append(markup)
        return "\x00%d\x00" % (len(stash) - 1)
    out = _CODE.sub(lambda m: keep("<code>%s</code>" % m.group(1)), out)
    out = _LINK.sub(lambda m: keep('<a href="%s" rel="noopener noreferrer">%s</a>' % (m.group(2), m.group(1))), out)
    out = _BOLD.sub(lambda m: "<strong>%s</strong>" % m.group(1), out)
    out = _EM.sub(lambda m: "<em>%s</em>" % m.group(1), out)
    while "\x00" in out:
        out = re.sub("\x00(\\d+)\x00", lambda m: stash[int(m.group(1))], out)
    return out


def _cells(line):
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in s.split("|")]


def _table(head, rows):
    n = len(head)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in (r + [""] * n)[:n]) for r in rows)
    return ('<div class="table-wrap"><table><thead><tr>%s</thead><tbody>%s</tbody></table></div>'
            % ("".join("<th>%s</th>" % _inline(c) for c in head) + "</tr>", body))


def md(text):
    """Escape-first Markdown subset -> safe HTML (returned as Markup by the filter)."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out, para, lst = [], [], None

    def flush_para():
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para)))
            para.clear()

    def close_list():
        nonlocal lst
        if lst:
            out.append("</%s>" % lst)
            lst = None
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        s = line.strip()
        h = re.match(r"(#{1,4})\s+(.*)", s)
        b = re.match(r"[-*]\s+(?:\[[ xX]\]\s+)?(.*)", s)
        n = re.match(r"\d+[.)]\s+(.*)", s)
        if s.startswith("```"):
            flush_para()
            close_list()
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code), quote=True))
        elif "|" in s and i < len(lines) and "|" in lines[i] and _TABLE_SEP.match(lines[i].strip()):
            flush_para()
            close_list()
            rows = []
            i += 1
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            out.append(_table(_cells(s), rows))
        elif not s:
            flush_para()
            close_list()
        elif h:
            flush_para()
            close_list()
            level = min(len(h.group(1)) + 2, 6)
            out.append("<h%d>%s</h%d>" % (level, _inline(h.group(2)), level))
        elif b or n:
            flush_para()
            kind = "ul" if b else "ol"
            if lst != kind:
                close_list()
                out.append("<%s>" % kind)
                lst = kind
            out.append("<li>%s</li>" % _inline((b or n).group(1)))
        elif lst and line.startswith(("  ", "\t")):
            out[-1] = out[-1][:-5] + "<br>" + _inline(s) + "</li>"
        else:
            close_list()
            para.append(s)
    flush_para()
    close_list()
    return "\n".join(out)


# ---------- confirm step ----------

def confirmed():
    return request.form.get("confirm") == "1"


def confirm_page(question, detail, action_label, cancel_url):
    """The confirm step for a destructive POST without JavaScript: the same form, posted again with confirm=1."""
    fields = [(k, v) for k, v in request.form.items(multi=True) if k not in ("csrf_token", "confirm")]
    return render_template("confirm.html", question=question, detail=detail, action_label=action_label,
                           action_url=request.path, fields=fields, cancel_url=cancel_url), 200


def pr_label(url):
    """'https://github.com/o/r/pull/7' -> '#7'; any other address unchanged."""
    tail = (url or "").rstrip("/").rsplit("/", 2)
    return "#" + tail[2] if len(tail) == 3 and tail[1] == "pull" and tail[2].isdigit() else (url or "")


def safe_next(value):
    """A same-site path to return to after a form (`/my?project=x`), else None. Never another site: no scheme, no
    `//host`, no backslash tricks."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if any(c in value for c in "\r\n") or len(value) > 500:
        return None
    return value


def theme():
    """The colour theme for this request: `dark` only when the `theme` cookie says exactly that, else `light`.
    Light is the default; a missing or unknown cookie value never changes it."""
    return "dark" if request.cookies.get("theme") == "dark" else "light"


def init_app(app):
    from markupsafe import Markup
    app.context_processor(lambda: {"theme": theme()})
    env = app.jinja_env
    env.filters.update(ago=ago, when_abs=when_abs, iso=iso, reason_text=reason_text, next_step=next_step,
                       runner_label=runner_label, display_name=display_name, initials=initials, pr_label=pr_label,
                       md=lambda t: Markup(md(t)))
    env.globals.update(ui_status=status, ui_duration=duration)
