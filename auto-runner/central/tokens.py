"""Per-project API tokens and the `bearer_token(scope)` guard for `/api/` routes.

- A token is `art_` + 32 random bytes (urlsafe base64). It is shown once, when created or rotated; the database
  keeps only its sha256 and a short prefix for display, so a lost token cannot be shown again, only replaced.
- Scopes: `ingest` (send events) and `pull` (fetch and acknowledge deliveries). A token has one or both.
- Revoke sets `revoked_at`; rotate revokes the old token and creates a new one with the same scopes, in one commit.
- `bearer_token(scope)` answers 401 for a missing, unknown or revoked token and 403 when the token lacks the scope.
  It sets `g.token` (the row) and `g.token_project` (the project row).
"""
import functools
import hashlib
import secrets

from flask import abort, g, jsonify, make_response, request

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
    """Decorate an API view: 401 without a valid active token, 403 when the token lacks `scope`."""
    if scope not in SCOPES:
        raise ValueError("unknown token scope: %r" % (scope,))

    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            db = get_db()
            row = lookup(db, _bearer())
            project = db.query_one("SELECT * FROM project WHERE id = ?", (row["project_id"],)) if row else None
            if project is None:
                _fail(401, "a valid bearer token is required")
            if scope not in scopes_of(row):
                _fail(403, "this token does not have the %s scope" % scope)
            g.token, g.token_project = row, project
            return view(*args, **kwargs)
        return wrapped
    return decorate
