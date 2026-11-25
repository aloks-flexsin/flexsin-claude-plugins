"""Central service configuration, read from environment variables only."""
import os
import secrets

VAPID_VARS = ("CENTRAL_VAPID_PUBLIC_KEY", "CENTRAL_VAPID_PRIVATE_KEY", "CENTRAL_VAPID_SUBJECT")

REFLECTION_VARS = ("CENTRAL_REFLECTION_PROJECT", "CENTRAL_REFLECTION_ASSIGNEE", "CENTRAL_REFLECTION_MIN_COUNT",
                   "CENTRAL_REFLECTION_MIN_PROJECTS", "CENTRAL_REFLECTION_DAILY")
REFLECTION_DEFAULTS = {"min_count": 3, "min_projects": 2, "daily": 3}

ENV_VARS = ("SECRET_KEY", "DATABASE_URL", "CENTRAL_ENV", "PUBLIC_URL", "VERCEL_ENV") + VAPID_VARS + REFLECTION_VARS


def push_configured(cfg):
    """Browser push is set up when all three VAPID settings are present (public key, private key, subject)."""
    return all((cfg.get(name) or "").strip() for name in VAPID_VARS)


def _positive_int(value, default):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def reflection(cfg):
    """Drafting settings for runner frictions (decision record 0011): `project` (a project slug, or None when drafting
    is off), `assignee` (an account e-mail or None), `min_count`, `min_projects` and `daily` (whole numbers above 0; a
    missing or unusable value takes the default: 3, 2 and 3)."""
    return {"project": (cfg.get("CENTRAL_REFLECTION_PROJECT") or "").strip() or None,
            "assignee": (cfg.get("CENTRAL_REFLECTION_ASSIGNEE") or "").strip() or None,
            "min_count": _positive_int(cfg.get("CENTRAL_REFLECTION_MIN_COUNT"), REFLECTION_DEFAULTS["min_count"]),
            "min_projects": _positive_int(cfg.get("CENTRAL_REFLECTION_MIN_PROJECTS"),
                                          REFLECTION_DEFAULTS["min_projects"]),
            "daily": _positive_int(cfg.get("CENTRAL_REFLECTION_DAILY"), REFLECTION_DEFAULTS["daily"])}

MIN_SECRET_KEY_LENGTH = 32

# Values that turn up in examples and tutorials; never acceptable as a production key.
DEFAULT_SECRET_KEYS = frozenset({
    "dev", "development", "secret", "secret-key", "secret_key", "changeme", "change-me", "change_me",
    "please-change-me", "your-secret-key", "your_secret_key", "replace-me", "test", "testing",
})


class ConfigError(RuntimeError):
    """The service cannot start with this configuration."""


def load(overrides=None, environ=None):
    """Build the config dict: env vars first, then `overrides` (used by tests and the app factory).

    Without SECRET_KEY the service refuses to start, unless TESTING is set, where a random key is used.
    In production the production check below must also pass.
    """
    environ = os.environ if environ is None else environ
    cfg = {name: environ.get(name) or None for name in ENV_VARS}
    cfg["TESTING"] = False
    cfg.update(overrides or {})
    if is_production(cfg):
        check_production(cfg)
    if not cfg.get("SECRET_KEY"):
        if not cfg.get("TESTING"):
            raise ConfigError("SECRET_KEY is not set; the central service refuses to start without it")
        cfg["SECRET_KEY"] = secrets.token_hex(32)
    return cfg


def is_production(cfg):
    """Production when CENTRAL_ENV=production, or on Vercel's production environment (VERCEL_ENV=production)."""
    return "production" in ((cfg.get("CENTRAL_ENV") or "").strip().lower(),
                            (cfg.get("VERCEL_ENV") or "").strip().lower())


def check_production(cfg):
    """Refuse a production start without an https PUBLIC_URL or with a default or short SECRET_KEY."""
    url = (cfg.get("PUBLIC_URL") or "").strip()
    if not url.lower().startswith("https://") or len(url) <= len("https://"):
        raise ConfigError("PUBLIC_URL must be an https:// address in production")
    key = cfg.get("SECRET_KEY") or ""
    if not key:
        raise ConfigError("SECRET_KEY is not set; the central service refuses to start without it")
    if key.strip().lower() in DEFAULT_SECRET_KEYS or len(set(key)) < 8:
        raise ConfigError("SECRET_KEY is a default or placeholder value; set a random key in production")
    if len(key) < MIN_SECRET_KEY_LENGTH:
        raise ConfigError("SECRET_KEY is shorter than %d characters; set a longer random key in production"
                          % MIN_SECRET_KEY_LENGTH)
