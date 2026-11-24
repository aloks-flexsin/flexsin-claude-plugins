"""Sign-in from Claude and personal tokens (decision record 0008, second part).

**Device sign-in** (the pattern `gh auth login` uses):
1. The plugin calls `POST /api/v1/device/start` with the computer's name and the repository. The service answers a long
   secret `device_code` (kept by the plugin), a short `user_code` such as `KQ4M-7RXD` (shown to the person), the
   address `/device`, the poll interval and the expiry (DEVICE_LIFETIME).
2. The person opens `/device` signed in, types the code, sees which computer and repository asked, and approves or denies.
3. The plugin polls `POST /api/v1/device/token` with the `device_code`. Until a decision: `authorization_pending`
   (`slow_down` when it polls faster than INTERVAL). Denied: `access_denied`. Expired, unknown or already used:
   `expired_token`. Approved: a new personal token for the approving account, returned **once**.
Both codes are stored only as SHA-256; a request works once. Starting a request is limited per address (in memory).

**Personal tokens** (`arp_` + 32 random bytes) belong to one account, not to one project. `tokens.bearer_token` accepts
them for a project named in the request (`?project=<slug>`) where the account is a developer or above (or an admin),
checked on every call, so losing membership or being disabled takes effect at once. A token unused for MAX_IDLE stops
working; each person revokes their own on **My tokens**, and an admin can revoke all of an account's. Only the SHA-256
and a short prefix are stored. Events sent with one are stamped with the account (`event.account_id`).
"""
import datetime
import hashlib
import secrets

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from . import accounts, audit, auth, identities, sessions, ui
from .db import get_db

bp = Blueprint("personal", __name__)

PREFIX = "arp_"
DISPLAY_LENGTH = 12
DEVICE_LIFETIME = datetime.timedelta(minutes=10)
INTERVAL = 5                                     # seconds between polls
MAX_IDLE = datetime.timedelta(days=90)
CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"   # no vowels (no words), no 0/O or 1/I
START_LIMIT = 10                                 # sign-in requests a minute from one address
MAX_NAME = 100


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_user_code(code):
    return "".join(ch for ch in (code or "").upper() if ch in CODE_ALPHABET)[:8]


def _user_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def _clean(text, limit=MAX_NAME):
    text = "".join(ch for ch in str(text or "") if ch.isprintable()).strip()
    return text[:limit]


# ---------- device sign-in ----------

def _name(value):
    """A username or Claude account as the runner reports it, or None when it is not a valid name."""
    return value if identities.valid(value) else None


def start(db, client_name, repo, moment, runner_user=None, claude_account=None):
    """Store a new sign-in request and commit -> (device_code, user_code). Only their hashes are kept. The username and
    Claude account the runner reports are stored when valid and dropped otherwise."""
    device_code, user_code = secrets.token_urlsafe(32), _user_code()
    db.execute("INSERT INTO device_login (id, device_hash, user_hash, client_name, repo, created_at, expires_at,"
               " runner_user, claude_account) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (secrets.token_hex(16), _hash(device_code), _hash(normalize_user_code(user_code)),
                _clean(client_name) or "a computer", _clean(repo, 300), sessions.iso(moment),
                sessions.iso(moment + DEVICE_LIFETIME), _name(runner_user), _name(claude_account)))
    db.commit()
    return device_code, user_code


def find_by_user_code(db, user_code, moment):
    """-> the request still waiting for a decision (not expired, not approved or denied), else None."""
    code = normalize_user_code(user_code)
    if len(code) != 8:
        return None
    row = db.query_one("SELECT * FROM device_login WHERE user_hash = ?", (_hash(code),))
    if row is None or row["expires_at"] <= sessions.iso(moment) or row["approved_at"] or row["denied_at"]:
        return None
    return row


def decide(db, row, account, approve, moment):
    column = "approved_at" if approve else "denied_at"
    cur = db.execute("UPDATE device_login SET %s = ?, approved_by = ? WHERE id = ? AND approved_at IS NULL"
                     " AND denied_at IS NULL" % column, (sessions.iso(moment), account["id"], row["id"]))
    db.commit()
    return cur.rowcount == 1


def poll(db, device_code, moment):
    """-> ('pending' | 'slow_down' | 'denied' | 'expired', None) or ('ok', (raw token, account))."""
    row = db.query_one("SELECT * FROM device_login WHERE device_hash = ?", (_hash(device_code or ""),)) \
        if device_code and len(device_code) <= 128 else None
    at = sessions.iso(moment)
    if row is None or row["delivered_at"] or row["expires_at"] <= at:
        return "expired", None
    if row["denied_at"]:
        return "denied", None
    if not row["approved_at"]:
        last = sessions.parse_iso(row["last_poll_at"]) if row["last_poll_at"] else None
        db.execute("UPDATE device_login SET last_poll_at = ? WHERE id = ?", (at, row["id"]))
        db.commit()
        if last is not None and (moment - last).total_seconds() < INTERVAL - 1:
            return "slow_down", None
        return "pending", None
    account = accounts.get(db, row["approved_by"])
    if not accounts.is_enabled(account):
        return "denied", None
    cur = db.execute("UPDATE device_login SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL", (at, row["id"]))
    if cur.rowcount != 1:
        db.rollback()
        return "expired", None
    raw = create_token(db, account["id"], row["client_name"], moment, commit=False,
                       runner_user=row["runner_user"], claude_account=row["claude_account"])
    identities.record(db, account["id"], row["runner_user"], row["claude_account"], at, "auto-runner")
    db.commit()
    return "ok", (raw, account)


# ---------- personal tokens ----------

def create_token(db, account_id, name, moment, commit=True, runner_user=None, claude_account=None):
    raw = PREFIX + secrets.token_urlsafe(32)
    db.execute("INSERT INTO personal_token (id, account_id, token_hash, prefix, name, created_at, runner_user,"
               " claude_account) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (secrets.token_hex(16), account_id, _hash(raw), raw[:DISPLAY_LENGTH], _clean(name) or "a computer",
                sessions.iso(moment), _name(runner_user), _name(claude_account)))
    if commit:
        db.commit()
    return raw


def lookup(db, raw, moment):
    """-> (token row, account) for an active personal token of an enabled account used within MAX_IDLE, else (None, None).
    A token past MAX_IDLE is revoked on the spot."""
    if not raw or not raw.startswith(PREFIX) or len(raw) > 128:
        return None, None
    row = db.query_one("SELECT * FROM personal_token WHERE token_hash = ? AND revoked_at IS NULL", (_hash(raw),))
    if row is None:
        return None, None
    last = sessions.parse_iso(row["last_used_at"] or row["created_at"])
    if moment - last >= MAX_IDLE:
        db.execute("UPDATE personal_token SET revoked_at = ? WHERE id = ?", (sessions.iso(moment), row["id"]))
        db.commit()
        return None, None
    account = accounts.get(db, row["account_id"])
    if not accounts.is_enabled(account):
        return None, None
    return row, account


def touch(db, row, moment):
    db.execute("UPDATE personal_token SET last_used_at = ? WHERE id = ?", (sessions.iso(moment), row["id"]))
    db.commit()


def tokens_of(db, account_id):
    return db.query("SELECT * FROM personal_token WHERE account_id = ? ORDER BY created_at DESC, id", (account_id,))


def revoke(db, account_id, token_id, moment):
    cur = db.execute("UPDATE personal_token SET revoked_at = ? WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
                     (sessions.iso(moment), token_id, account_id))
    db.commit()
    return cur.rowcount == 1


def revoke_all(db, account_id, moment):
    cur = db.execute("UPDATE personal_token SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
                     (sessions.iso(moment), account_id))
    db.commit()
    return cur.rowcount


# ---------- API ----------

def _start_limiter():
    from .ingest import RateLimiter
    return current_app.extensions.setdefault("device_start_limiter", RateLimiter(limit=START_LIMIT))


def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.post("/api/v1/device/start")
def api_start():
    moment = auth.now()
    allowed, wait = _start_limiter().allow(request.remote_addr or "?", moment)
    if not allowed:
        response = jsonify(error="too many sign-in requests; try again in a minute")
        response.status_code, response.headers["Retry-After"] = 429, str(wait)
        return response
    data = _json()
    device_code, user_code = start(get_db(), data.get("client_name"), data.get("repo"), moment,
                                  runner_user=data.get("user"), claude_account=data.get("claude_account"))
    base = (current_app.config.get("PUBLIC_URL") or "").strip().rstrip("/")
    uri = base + url_for("personal.device_page") if base else url_for("personal.device_page", _external=True)
    return jsonify(device_code=device_code, user_code=user_code, verification_uri=uri, interval=INTERVAL,
                   expires_in=int(DEVICE_LIFETIME.total_seconds()))


@bp.post("/api/v1/device/token")
def api_token():
    moment = auth.now()
    status, result = poll(get_db(), _json().get("device_code"), moment)
    if status != "ok":
        error = {"pending": "authorization_pending", "slow_down": "slow_down", "denied": "access_denied",
                 "expired": "expired_token"}[status]
        response = jsonify(error=error)
        response.status_code = 400
        return response
    raw, account = result
    db = get_db()
    audit.record(db, "personal_token.create", sessions.iso(moment), account_id=account["id"], detail={"via": "device"})
    db.commit()
    return jsonify(token=raw, email=account["email"])


@bp.get("/api/v1/projects")
def api_my_projects():
    """The signed-in person's projects (personal token; no project named): slug, name, repository and role, so the plugin
    can offer them to clone. Admins see every project."""
    from . import projects as _projects
    from .tokens import _bearer, _fail
    db, moment = get_db(), auth.now()
    row, account = lookup(db, _bearer() or "", moment)
    if row is None:
        _fail(401, "a valid personal token is required: sign this computer in from Claude")
    touch(db, row, moment)
    out = []
    for p in _projects.visible_to(db, account):
        role = _projects.effective_role(db, p, account)
        out.append({"slug": p["slug"], "name": p["name"], "repo_url": p["repo_url"], "role": role,
                    "can_run": _projects.allows(role, "developer")})
    return jsonify(account=account["email"], projects=out)


# ---------- pages ----------

@bp.get("/device")
@auth.login_required
def device_page():
    return render_template("device.html", row=None, error=None, done=None)


@bp.post("/device")
@auth.login_required
def device_lookup():
    row = find_by_user_code(get_db(), request.form.get("user_code", ""), auth.now())
    if row is None:
        return render_template("device.html", row=None, done=None,
                               error="That code is not waiting for approval: check it, or start again in Claude."), 400
    return render_template("device.html", row=row, error=None, done=None,
                           user_code=normalize_user_code(request.form.get("user_code", "")))


@bp.post("/device/decide")
@auth.login_required
def device_decide():
    db = get_db()
    moment = auth.now()
    row = find_by_user_code(db, request.form.get("user_code", ""), moment)
    approve = request.form.get("decision") == "approve"
    if row is None or not decide(db, row, g.account, approve, moment):
        return render_template("device.html", row=None, done=None,
                               error="That code is not waiting for approval any more: start again in Claude."), 400
    audit.record(db, "device.approve" if approve else "device.deny", sessions.iso(moment), account_id=g.account["id"],
                 detail={"client_name": row["client_name"], "repo": row["repo"]})
    db.commit()
    return render_template("device.html", row=None, error=None, done="approved" if approve else "denied")


@bp.get("/account/tokens")
@auth.login_required
def tokens_page():
    rows = [dict(r) for r in tokens_of(get_db(), g.account["id"])]
    return render_template("my_tokens.html", tokens=rows, idle_days=MAX_IDLE.days)


@bp.post("/account/tokens/<token_id>/revoke")
@auth.login_required
def token_revoke(token_id):
    db = get_db()
    if not ui.confirmed():
        row = db.query_one("SELECT name FROM personal_token WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
                           (token_id, g.account["id"]))
        if row is None:
            abort(404)
        return ui.confirm_page("Revoke the token of %s?" % row["name"],
                               "That computer stops working with the central service at once; sign it in again from Claude "
                               "to get a new token.", "Revoke token", url_for("personal.tokens_page"))
    if not revoke(db, g.account["id"], token_id, auth.now()):
        abort(404)
    audit.record(db, "personal_token.revoke", sessions.iso(auth.now()), account_id=g.account["id"],
                 detail={"token_id": token_id})
    db.commit()
    return redirect(url_for("personal.tokens_page"))
