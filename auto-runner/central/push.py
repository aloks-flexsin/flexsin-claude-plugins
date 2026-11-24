"""Browser push subscriptions (queue item N3): where a person's notifications may be pushed.

- `POST /push/subscribe` and `POST /push/unsubscribe` take JSON from the Account page's script, signed in, with the
  page's CSRF token in the `X-CSRF-Token` header (checked in `auth._check_csrf`, like every POST).
- Subscribe takes the browser's `PushSubscription` as JSON (`{"endpoint": "https://...", "keys": {"p256dh": ...,
  "auth": ...}}`). The endpoint must be https. One row per endpoint: subscribing again updates the keys, and when a
  different person turns push on in the same browser the endpoint becomes theirs (the browser now belongs to whoever
  last signed in and turned it on, so the earlier person's notifications stop going there).
- Unsubscribe deletes the endpoint only when it is the caller's own; someone else's endpoint is left alone and the
  answer is the same, so it does not reveal whether it exists.
- Push is "set up" when all three VAPID settings are present (`config.push_configured`); without them the Account page
  says "push is not set up on this service" and subscribe answers 409.
- Sending (queue item N5): `send_to_account` pushes one payload to each of a person's subscriptions through the
  sender (`app.config["PUSH_SENDER"]` when set, a fake in tests; else `push_sender`, which uses pywebpush) with a
  `PUSH_TIMEOUT` of 5 seconds. A 404 or 410 from the push service deletes the subscription; any other failure counts in
  `failures`; success sets `last_ok_at`. It never raises: it answers (state, detail) for the notification row.
- `GET /sw.js` serves `static/sw.js` (queue item N4) as `text/javascript` with `Service-Worker-Allowed: /`; the
  Account page's script (`static/app.js`) registers it, subscribes and posts the subscription here.
"""
import json
import re
import secrets

from flask import Blueprint, current_app, g, has_app_context, jsonify, request, send_from_directory

from . import auth, sessions
from . import config as _config
from .db import get_db

bp = Blueprint("push", __name__)

MAX_ENDPOINT, MAX_P256DH, MAX_AUTH, MAX_USER_AGENT = 1000, 200, 100, 300
_B64URL = re.compile(r"^[A-Za-z0-9_\-=]+$")


class Invalid(ValueError):
    """The request's subscription is not acceptable; the message says why."""


def configured(cfg=None):
    return _config.push_configured(current_app.config if cfg is None else cfg)


def subscriptions_of(db, account_id):
    return db.query("SELECT * FROM push_subscription WHERE account_id = ? ORDER BY created_at, id", (account_id,))


def account_state(db, account):
    """What the Account page's "Notifications on this browser" section needs."""
    on = configured()
    return {
        "configured": on,
        "public_key": (current_app.config.get("CENTRAL_VAPID_PUBLIC_KEY") or "").strip() if on else "",
        "count": len(subscriptions_of(db, account["id"])),
    }


def _endpoint(value):
    if not isinstance(value, str) or not value.strip():
        raise Invalid("The subscription has no endpoint.")
    value = value.strip()
    if not value.lower().startswith("https://") or len(value) <= len("https://"):
        raise Invalid("The endpoint must be an https:// address.")
    if len(value) > MAX_ENDPOINT or any(c.isspace() for c in value):
        raise Invalid("The endpoint is not a valid address.")
    return value


def _key(keys, name, limit):
    value = keys.get(name) if isinstance(keys, dict) else None
    if not isinstance(value, str) or not value or len(value) > limit or not _B64URL.match(value):
        raise Invalid("The subscription's %s key is missing or not valid." % name)
    return value


def parse(body):
    """-> (endpoint, p256dh, auth) from a PushSubscription JSON object, or raise Invalid."""
    if not isinstance(body, dict):
        raise Invalid("Send the subscription as a JSON object.")
    endpoint = _endpoint(body.get("endpoint"))
    keys = body.get("keys")
    return endpoint, _key(keys, "p256dh", MAX_P256DH), _key(keys, "auth", MAX_AUTH)


def subscribe(db, account_id, endpoint, p256dh, auth_key, user_agent, now):
    """Store or update the row for `endpoint` (now owned by `account_id`). Returns 'created' or 'updated'.
    Does not commit."""
    user_agent = (user_agent or "")[:MAX_USER_AGENT] or None
    existing = db.query_one("SELECT id FROM push_subscription WHERE endpoint = ?", (endpoint,))
    if existing:
        db.execute("UPDATE push_subscription SET account_id = ?, p256dh = ?, auth = ?, user_agent = ?, failures = 0"
                   " WHERE id = ?", (account_id, p256dh, auth_key, user_agent, existing["id"]))
        return "updated"
    db.execute("INSERT INTO push_subscription (id, account_id, endpoint, p256dh, auth, user_agent, created_at)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)",
               (secrets.token_hex(16), account_id, endpoint, p256dh, auth_key, user_agent, sessions.iso(now)))
    return "created"


def unsubscribe(db, account_id, endpoint):
    """Delete `endpoint` only if it is `account_id`'s. Returns the number of rows removed (0 or 1). Does not commit."""
    cur = db.execute("DELETE FROM push_subscription WHERE endpoint = ? AND account_id = ?", (endpoint, account_id))
    return cur.rowcount or 0


# ---------- sending (queue item N5) ----------

PUSH_TIMEOUT = 5          # seconds for each request to a push service
GONE = (404, 410)         # the push service says the subscription no longer exists
MAX_DETAIL = 300


class PushFailed(Exception):
    """A push service refused a push; `status` is its HTTP status when there was one."""

    def __init__(self, reason, status=None):
        super().__init__(reason)
        self.status = status


def push_sender(subscription, payload, vapid, timeout=PUSH_TIMEOUT):
    """Send one Web Push with pywebpush. `subscription` is {"endpoint", "keys": {"p256dh", "auth"}}, `payload` a str,
    `vapid` {"private_key", "subject"}. Raises PushFailed (with the status) when the push service refuses."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError as exc:                    # listed in requirements.txt; missing only in a broken install
        raise PushFailed("pywebpush is not installed") from exc
    try:
        webpush(subscription_info=subscription, data=payload, vapid_private_key=vapid["private_key"],
                vapid_claims={"sub": vapid["subject"]}, timeout=timeout)
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise PushFailed(("HTTP %s" % status) if status else str(exc), status) from exc


def payload_for(title, text, link, tag=None):
    """The JSON the service worker (static/sw.js) reads: title, body, link, and a tag so a repeat replaces the last."""
    return json.dumps({"title": title, "body": text, "link": link, "tag": tag}, separators=(",", ":"))


def send_to_account(db, account_id, payload, now, cfg=None):
    """Push `payload` to every subscription of the account. Never raises. Does not commit.
    -> ("off", reason) without VAPID keys or subscriptions, ("sent", None) when at least one push went out,
    else ("failed", reason)."""
    try:
        if cfg is None:
            cfg = current_app.config if has_app_context() else {}
        if not _config.push_configured(cfg):
            return "off", "push is not set up on this service"
        subs = subscriptions_of(db, account_id)
        if not subs:
            return "off", "no browser has push turned on"
        vapid = {"private_key": cfg["CENTRAL_VAPID_PRIVATE_KEY"].strip(), "subject": cfg["CENTRAL_VAPID_SUBJECT"].strip()}
        send = cfg.get("PUSH_SENDER") or push_sender
        at = now if isinstance(now, str) else sessions.iso(now)
        sent, reasons = 0, []
        for sub in subs:
            info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
            try:
                send(info, payload, vapid, timeout=PUSH_TIMEOUT)
            except Exception as exc:              # noqa: BLE001 - a push must never break the caller's request
                status = getattr(exc, "status", None)
                if status in GONE:
                    db.execute("DELETE FROM push_subscription WHERE id = ?", (sub["id"],))
                    reasons.append("subscription gone (HTTP %s), removed" % status)
                else:
                    db.execute("UPDATE push_subscription SET failures = failures + 1 WHERE id = ?", (sub["id"],))
                    reasons.append(str(exc) or exc.__class__.__name__)
                continue
            db.execute("UPDATE push_subscription SET last_ok_at = ?, failures = 0 WHERE id = ?", (at, sub["id"]))
            sent += 1
        if sent:
            return "sent", None
        return "failed", "; ".join(dict.fromkeys(reasons))[:MAX_DETAIL]
    except Exception as exc:                      # noqa: BLE001 - e.g. a database error while recording
        return "failed", ("push error: %s" % (str(exc) or exc.__class__.__name__))[:MAX_DETAIL]


def _error(message, status):
    return jsonify(ok=False, error=message), status


def _body():
    body = request.get_json(silent=True)
    return body if body is not None else {}


@bp.get("/sw.js")
def service_worker():
    """The push service worker (queue item N4), served from the site root so its scope can be `/`."""
    response = send_from_directory(current_app.static_folder, "sw.js", mimetype="text/javascript", max_age=0)
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.post("/push/subscribe")
def subscribe_post():
    if g.get("account") is None:
        return _error("Sign in first.", 401)
    if not configured():
        return _error("Push is not set up on this service.", 409)
    try:
        endpoint, p256dh, auth_key = parse(_body())
    except Invalid as exc:
        return _error(str(exc), 400)
    db = get_db()
    result = subscribe(db, g.account["id"], endpoint, p256dh, auth_key, request.headers.get("User-Agent"), auth.now())
    db.commit()
    return jsonify(ok=True, result=result)


@bp.post("/push/unsubscribe")
def unsubscribe_post():
    if g.get("account") is None:
        return _error("Sign in first.", 401)
    body = _body()
    try:
        endpoint = _endpoint(body.get("endpoint") if isinstance(body, dict) else None)
    except Invalid as exc:
        return _error(str(exc), 400)
    db = get_db()
    removed = unsubscribe(db, g.account["id"], endpoint)
    db.commit()
    return jsonify(ok=True, removed=removed)
