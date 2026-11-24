"""Task screenshots: images attached to a central task, stored in the database (`task_attachment`).

Only PNG, JPEG, GIF and WebP are kept, recognised by their first bytes (never by the name or the type the browser
sends), at most MAX_SIZE each and MAX_PER_TASK per task. Every request to the service is capped at MAX_SIZE in total
(`MAX_CONTENT_LENGTH`, see `init_app`), so one upload request carries at most 4 MB of files.

Who may do what: every member of the project sees a task's screenshots; members except viewers add them; the uploader
or an admin deletes one (after a confirm step, audited). Served images carry the stored type, `nosniff`,
`Content-Disposition: inline` and `Cache-Control: private`.
"""
import secrets

from flask import Blueprint, Response, abort, current_app, g, jsonify, redirect, request, url_for

from . import audit, auth, projects, sessions, tasks, ui
from .access import project_access
from .db import get_db

bp = Blueprint("attachments", __name__)

MAX_SIZE = 4 * 1024 * 1024
MAX_PER_TASK = 10
MAX_FILENAME = 200
LIMIT_TEXT = "4 MB"
UPLOAD_ROLE = "developer"        # members except viewers

# (first bytes, offset, content type). WebP is "RIFF", four size bytes, then "WEBP".
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"GIF87a", 0, "image/gif"),
    (b"GIF89a", 0, "image/gif"),
)


class AttachmentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def sniff(data):
    """The image type from the first bytes -> 'image/png' and so on, or None when it is not one of the four."""
    for magic, offset, kind in SIGNATURES:
        if data[offset:offset + len(magic)] == magic:
            return kind
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_name(filename):
    """The name as shown: the last path part only, no control characters, at most MAX_FILENAME characters."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[:MAX_FILENAME] or "screenshot"


def _columns(with_data=False):
    return ("SELECT t.id, t.project_id, t.task_number, t.account_id, t.filename, t.content_type, t.size, t.created_at,"
            " a.email AS uploader_email, a.name AS uploader_name%s FROM task_attachment t"
            " LEFT JOIN account a ON a.id = t.account_id" % (", t.data" if with_data else ""))


def for_task(db, project_id, number):
    """The task's screenshots, oldest first, without their bytes."""
    return db.query(_columns() + " WHERE t.project_id = ? AND t.task_number = ? ORDER BY t.created_at, t.id",
                    (project_id, number))


def get(db, project_id, number, attachment_id, with_data=False):
    """One screenshot of this task (with `data` as bytes when asked), or None."""
    row = db.query_one(_columns(with_data) + " WHERE t.project_id = ? AND t.task_number = ? AND t.id = ?",
                       (project_id, number, attachment_id))
    if row is not None and with_data:
        row["data"] = bytes(row["data"])                  # Postgres hands back a memoryview
    return row


def add(db, project, number, account, filename, data, now):
    """Store one screenshot for task `number` and commit -> its row (without bytes). Raise AttachmentError when the
    task does not exist (404), the bytes are not PNG, JPEG, GIF or WebP, it is over MAX_SIZE, or the task already has
    MAX_PER_TASK."""
    if tasks.get(db, project["id"], number) is None:
        raise AttachmentError("no such task", 404)
    name = clean_name(filename)
    data = bytes(data or b"")
    if not data:
        raise AttachmentError("%s is empty" % name)
    if len(data) > MAX_SIZE:
        raise AttachmentError("%s is larger than %s" % (name, LIMIT_TEXT))
    kind = sniff(data)
    if kind is None:
        raise AttachmentError("%s is not a PNG, JPEG, GIF or WebP image" % name)
    count = db.query_one("SELECT COUNT(*) AS n FROM task_attachment WHERE project_id = ? AND task_number = ?",
                         (project["id"], number))["n"]
    if count >= MAX_PER_TASK:
        raise AttachmentError("task %d already has %d screenshots, the most it can have" % (number, MAX_PER_TASK))
    new_id = "%02d%s" % (count, secrets.token_hex(15))   # sorts in upload order among files added the same second
    db.execute("INSERT INTO task_attachment (id, project_id, task_number, account_id, filename, content_type, size, data,"
               " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (new_id, project["id"], number, account["id"], name, kind, len(data), data, sessions.iso(now)))
    db.commit()
    return get(db, project["id"], number, new_id)


def can_delete(row, account):
    return account is not None and (row["account_id"] == account["id"] or account["role"] == projects.ADMIN)


def delete(db, project, number, attachment_id, account):
    """Delete one screenshot and commit -> its row. Only the uploader or an admin (403 otherwise, 404 if unknown)."""
    row = get(db, project["id"], number, attachment_id)
    if row is None:
        raise AttachmentError("no such screenshot", 404)
    if not can_delete(row, account):
        raise AttachmentError("only the person who added a screenshot or an admin deletes it", 403)
    db.execute("DELETE FROM task_attachment WHERE id = ?", (row["id"],))
    db.commit()
    return row


# ---------- the request size limit ----------

TOO_LARGE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Too large</title><link rel="stylesheet" href="{css}"></head>
<body><main><div class="auth wide"><h1>That was too large</h1><div class="form-card">
<p>One upload can be at most {limit} in total. Send fewer or smaller files at a time.</p>
<a class="btn" href="/my">Back to your work</a></div></div></main></body></html>
"""


def init_app(app):
    """Cap every request at MAX_SIZE and answer 413 with the limit in words (JSON under /api/)."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_SIZE

    def too_large(_exc):
        message = "the request is larger than %s, the most one request can carry" % LIMIT_TEXT
        if request.path.startswith("/api/"):
            response = jsonify(error=message)
            response.status_code = 413
            return response
        page = TOO_LARGE_PAGE.replace("{css}", url_for("static", filename="style.css")).replace("{limit}", LIMIT_TEXT)
        return Response(page, status=413, mimetype="text/html")

    app.register_error_handler(413, too_large)


def uploaded_files():
    """The request's multipart `file` parts that carry a file (an empty file input sends one with no name)."""
    return [f for f in request.files.getlist("file") if f and f.filename]


def store_files(db, project, number, account, files, moment):
    """Store each uploaded file on task `number` (audited, committed one by one) -> the reasons for the refused ones.
    A refused file never stops the others."""
    refused = []
    for f in files:
        try:
            row = add(db, project, number, account, f.filename, f.read(MAX_SIZE + 1), moment)
        except AttachmentError as e:
            refused.append(str(e))
            continue
        audit.record(db, "task.attachment.add", sessions.iso(moment), account_id=account["id"],
                     project_id=project["id"],
                     detail={"number": number, "attachment": row["id"], "filename": row["filename"]})
        db.commit()
    return refused


def refused_text(refused):
    return "Not added: " + "; ".join(refused) + "."


# ---------- the refusals after "Add task" ----------
# The New task form posts, the task is created, then the browser is sent to the task page (so a reload never adds the
# task twice). The refusals travel in `?refused=`, signed with SECRET_KEY, so a crafted link cannot put words on the page.

REFUSED_SALT = "task-attachments-refused"


def _signer():
    from itsdangerous import URLSafeSerializer          # ships with Flask
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=REFUSED_SALT)


def sign_refused(project_id, number, refused):
    return _signer().dumps({"p": project_id, "n": number, "r": refused})


def read_refused(project_id, number, value):
    """The refusals signed for this very task -> the text for the page, or None (missing, tampered or another task's)."""
    if not value:
        return None
    from itsdangerous import BadData
    try:
        data = _signer().loads(value)
    except BadData:
        return None
    if not isinstance(data, dict) or data.get("p") != project_id or data.get("n") != number:
        return None
    refused = [str(r)[:400] for r in (data.get("r") or [])[:MAX_PER_TASK * 2]]
    return refused_text(refused) if refused else None


# ---------- pages ----------

def _task_or_404(db, number):
    row = tasks.get(db, g.project["id"], number)
    if row is None:
        abort(404)
    return row


def _back(slug, number):
    return redirect(url_for("tasks.task_page", slug=slug, number=number))


@bp.post("/projects/<slug>/tasks/<int:number>/attachments")
@project_access("viewer")
def attachment_upload(slug, number):
    """Multipart `file`, one or more. Each good image is kept; the refused ones are named with the reason."""
    db = get_db()
    _task_or_404(db, number)
    if not projects.allows(g.project_role, UPLOAD_ROLE):
        abort(403)
    files = uploaded_files()
    if not files:
        return tasks._detail(number, error="Choose an image to upload.", status=400)
    refused = store_files(db, g.project, number, g.account, files, auth.now())
    if refused:
        return tasks._detail(number, error=refused_text(refused), status=400)
    return _back(slug, number)


@bp.get("/projects/<slug>/tasks/<int:number>/attachments/<attachment_id>")
@project_access("viewer")
def attachment_get(slug, number, attachment_id):
    row = get(get_db(), g.project["id"], number, attachment_id, with_data=True)
    if row is None:
        abort(404)
    response = Response(row["data"], mimetype=row["content_type"])
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private"
    return response


@bp.post("/projects/<slug>/tasks/<int:number>/attachments/<attachment_id>/delete")
@project_access("viewer")
def attachment_delete(slug, number, attachment_id):
    """The uploader or an admin, after the confirm step; audited."""
    db = get_db()
    row = get(db, g.project["id"], number, attachment_id)
    if row is None:
        abort(404)
    if not can_delete(row, g.account):
        abort(403)
    if not ui.confirmed():
        return ui.confirm_page("Delete the screenshot %s?" % row["filename"],
                               "It is removed from task #%d for everyone. This cannot be undone." % number,
                               "Delete screenshot", url_for("tasks.task_page", slug=slug, number=number))
    delete(db, g.project, number, attachment_id, g.account)
    audit.record(db, "task.attachment.delete", sessions.iso(auth.now()), account_id=g.account["id"],
                 project_id=g.project["id"], detail={"number": number, "attachment": row["id"],
                                                     "filename": row["filename"]})
    db.commit()
    return _back(slug, number)
