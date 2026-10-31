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

    from . import auth
    auth.init_app(app)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app
