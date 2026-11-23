"""Per-project API tokens and the `bearer_token(scope)` guard for `/api/` routes.

- A token is `art_` + 32 random bytes (urlsafe base64). It is shown once, when created or rotated; the database
  keeps only its sha256 and a short prefix for display, so a lost token cannot be shown again, only replaced.
- Scopes: `ingest` (send events) and `pull` (fetch and acknowledge deliveries). A token has one or both.
- Revoke sets `revoked_at`; rotate revokes the old token and creates a new one with the same scopes, in one commit.
- `bearer_token(scope)` answers 401 for a missing, unknown or revoked token and 403 when the token lacks the scope;
  `bearer_token(None)` takes a token with any scope. It sets `g.token` (the row), `g.token_project` (the project row) and
  `g.token_account` (None for a project token).
- A **personal token** (`personal.PREFIX`, decision record 0008) has both scopes and no project of its own: the request
  names the project (`?project=<slug>`, or `?repo=<host/owner/repo>` when the slug is not known yet), and the account must be an enabled developer or above there, or an admin
  (403 otherwise; 401 for an unknown, revoked, idle-expired or disabled one). A project token may also be sent with
  `?project=`, which must then be its own project.
"""
import functools
import hashlib
import secrets

from flask import abort, g, jsonify, make_response, request

from . import auth, personal, projects
from .db import get_db

PREFIX = "art_"
SCOPES = ("ingest", "pull")
DISPLAY_LENGTH = 12


class TokenError(ValueError):
    """The token cannot be created or changed as asked."""


def generate():
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_scopes(scopes):
    """A sorted, de-duplicated tuple of known scopes; at least one."""
    chosen = sorted(set(scopes or ()))
    unknown = [s for s in chosen if s not in SCOPES]
    if unknown:
        raise TokenError("unknown scope: %s (use %s)" % (", ".join(unknown), " and/or ".join(SCOPES)))
    if not chosen:
        raise TokenError("choose at least one scope (%s)" % " and/or ".join(SCOPES))
    return tuple(chosen)


def scopes_of(row):
    return tuple((row["scopes"] or "").split())


def _insert(db, project_id, scopes, account_id, now):
    raw = generate()
    token_id = secrets.token_hex(16)
    db.execute("INSERT INTO token (id, project_id, token_hash, prefix, scopes, created_by, created_at)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)",
               (token_id, project_id, hash_token(raw), raw[:DISPLAY_LENGTH], " ".join(scopes), account_id, now))
    return raw, get(db, project_id, token_id)


def create(db, project_id, scopes, account_id, now):
    """Create and commit a token; return (raw token, row). The raw token is not stored anywhere."""
    raw, row = _insert(db, project_id, normalize_scopes(scopes), account_id, now)
    db.commit()
    return raw, row


def get(db, project_id, token_id):
    return db.query_one("SELECT * FROM token WHERE project_id = ? AND id = ?", (project_id, token_id))


def for_project(db, project_id):
    return db.query("SELECT * FROM token WHERE project_id = ? ORDER BY created_at DESC, id", (project_id,))


def _revoke(db, project_id, token_id, now):
    cur = db.execute("UPDATE token SET revoked_at = ? WHERE project_id = ? AND id = ? AND revoked_at IS NULL",
                     (now, project_id, token_id))
    if cur.rowcount == 0:
        raise TokenError("that token is already revoked" if get(db, project_id, token_id) else "no such token")


def revoke(db, project_id, token_id, now):
    """Revoke an active token and commit; return the row."""
    _revoke(db, project_id, token_id, now)
    db.commit()
    return get(db, project_id, token_id)


def rotate(db, project_id, token_id, account_id, now):
    """Revoke an active token and create a new one with the same scopes, in one commit; return (raw, new row)."""
    old = get(db, project_id, token_id)
    try:
        _revoke(db, project_id, token_id, now)
        raw, row = _insert(db, project_id, scopes_of(old), account_id, now)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return raw, row


def lookup(db, raw):
    """The active token row for a raw token, or None (unknown, revoked or malformed)."""
    if not raw or not raw.startswith(PREFIX):
        return None
    return db.query_one("SELECT * FROM token WHERE token_hash = ? AND revoked_at IS NULL", (hash_token(raw),))


def _fail(status, message):
    response = make_response(jsonify(error=message), status)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="auto-runner"'
    abort(response)


def _bearer():
    header = request.headers.get("Authorization", "")
    kind, _, value = header.partition(" ")
    if kind.lower() != "bearer":
        return None
    return value.strip() or None


def bearer_token(scope):
    """Decorate an API view: 401 without a valid active token, 403 when the token lacks `scope` (None: any scope)."""
    if scope is not None and scope not in SCOPES:
        raise ValueError("unknown token scope: %r" % (scope,))

    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            db = get_db()
            raw = _bearer()
            named = (request.args.get("project") or "").strip()
            if raw and raw.startswith(personal.PREFIX):
                moment = auth.now()
                row, account = personal.lookup(db, raw, moment)
                if row is None:
                    _fail(401, "a valid bearer token is required")
                repo = (request.args.get("repo") or "").strip()
                project = projects.get_by_slug(db, named) if named else projects.get_by_repo_key(db, repo.lower() or None)
                if project is None or not projects.allows(projects.effective_role(db, project, account), "developer"):
                    _fail(403, "name a project (?project=<slug>) where this account is a developer or above")
                personal.touch(db, row, moment)
                g.token = {"id": row["id"], "scopes": " ".join(SCOPES), "kind": "personal"}
                g.token_project, g.token_account = project, account
                return view(*args, **kwargs)
            row = lookup(db, raw)
            project = db.query_one("SELECT * FROM project WHERE id = ?", (row["project_id"],)) if row else None
            if project is None:
                _fail(401, "a valid bearer token is required")
            if named and named != project["slug"]:
                _fail(403, "this token belongs to another project")
            if scope is not None and scope not in scopes_of(row):
                _fail(403, "this token does not have the %s scope" % scope)
            g.token, g.token_project, g.token_account = row, project, None
            return view(*args, **kwargs)
        return wrapped
    return decorate
