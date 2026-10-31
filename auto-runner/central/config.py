"""Central service configuration, read from environment variables only."""
import os
import secrets

ENV_VARS = ("SECRET_KEY", "DATABASE_URL")


class ConfigError(RuntimeError):
    """The service cannot start with this configuration."""


def load(overrides=None, environ=None):
    """Build the config dict: env vars first, then `overrides` (used by tests and the app factory).

    Without SECRET_KEY the service refuses to start, unless TESTING is set, where a random key is used.
    """
    environ = os.environ if environ is None else environ
    cfg = {name: environ.get(name) or None for name in ENV_VARS}
    cfg["TESTING"] = False
    cfg.update(overrides or {})
    if not cfg.get("SECRET_KEY"):
        if not cfg.get("TESTING"):
            raise ConfigError("SECRET_KEY is not set; the central service refuses to start without it")
        cfg["SECRET_KEY"] = secrets.token_hex(32)
    return cfg
