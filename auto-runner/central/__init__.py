"""auto-runner central service: blockers, decisions and deliveries away from the terminal (Flask app factory)."""
from flask import Flask, jsonify

from . import config as _config
from . import db

CSP = (
    "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'; img-src 'self'; script-src 'self'; style-src 'self'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


# Plain HTML on purpose: no template, so nothing on this page needs the database (the top bar's badge would).
UPDATE_NEEDED_PAGE = """<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Update needed</title><link rel="stylesheet" href="{css}"></head>
<body><main><div class="auth wide"><h1>This page needs a database update</h1><div class="form-card">
<p>The service was updated, but its database was not yet. Nothing is lost: the page works again as soon as an
administrator runs, once, from the <code>auto-runner</code> folder with the production <code>DATABASE_URL</code>:</p>
<pre class="wrap">python -m central init-db</pre>
<p class="muted">It only adds what is missing and keeps all data. Other pages may still work meanwhile.</p>
<a class="btn" href="/my">Try again</a></div></div></main></body></html>
"""


def _schema_guard(app):
    """A query that needs a table or column the database does not have yet (the code was deployed before
    `python -m central init-db`) answers 503 with what to run, instead of a crash. Every other error is untouched."""
    import logging
    import sqlite3

    from flask import Response, request, url_for
    log = logging.getLogger(__name__)
    kinds = [sqlite3.OperationalError]
    try:
        from psycopg import errors as pg_errors
        kinds += [pg_errors.UndefinedTable, pg_errors.UndefinedColumn]
    except (ImportError, AttributeError):                # SQLite-only installs (or a stand-in module) have none
        pass

    def handle(exc):
        if not db.schema_missing(exc):
            raise exc
        log.error("database schema is behind the code (run python -m central init-db): %s", exc)
        try:
            db.get_db().rollback()
        except Exception:                               # the connection may be gone; the page still answers
            pass
        if request.path.startswith("/api/"):
            response = jsonify(error="the service's database needs an update: an administrator runs "
                                     "python -m central init-db")
            response.status_code = 503
            return response
        css = url_for("static", filename="style.css")
        return Response(UPDATE_NEEDED_PAGE.replace("{css}", css), status=503, mimetype="text/html")

    for kind in kinds:
        app.register_error_handler(kind, handle)


def create_app(config=None):
    """Build the app. `config` overrides the environment (tests pass TESTING and DATABASE_URL)."""
    app = Flask(__name__)
    app.config.update(_config.load(config))

    @app.after_request
    def _security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    app.teardown_appcontext(db.close_db)

    from . import (access, admin, attachments, auth, blockers, deliveries, ingest, invites, mywork, notifications,
                   personal, push, runners, runs, shell, supervisor, tasks, ui)
    attachments.init_app(app)                           # MAX_CONTENT_LENGTH and the 413 page
    auth.init_app(app)
    ui.init_app(app)
    shell.init_app(app)
    app.register_blueprint(admin.bp)
    app.register_blueprint(access.bp)
    app.register_blueprint(ingest.bp)
    app.register_blueprint(deliveries.bp)
    app.register_blueprint(blockers.bp)
    app.register_blueprint(runs.bp)
    app.register_blueprint(invites.bp)
    app.register_blueprint(personal.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(supervisor.bp)
    app.register_blueprint(attachments.bp)
    app.register_blueprint(mywork.bp)
    app.register_blueprint(runners.bp)
    app.register_blueprint(notifications.bp)
    app.register_blueprint(push.bp)

    _schema_guard(app)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app
