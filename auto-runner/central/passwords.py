"""Password hashing and policy (standard library only: hashlib.scrypt).

A stored hash is `scrypt$<n>$<r>$<p>$<salt b64>$<key b64>`, so the parameters travel with it and can be
raised later without breaking existing accounts.
"""
import base64
import hashlib
import hmac
import os
import secrets

MIN_LENGTH = 12
SALT_BYTES = 16
N, R, P = 2 ** 14, 8, 1
DKLEN = 64
COMMON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common-passwords.txt")

_common = None


class PasswordError(ValueError):
    """The password does not meet the policy."""


def common_passwords():
    """The bundled list, lower-cased; loaded once."""
    global _common
    if _common is None:
        with open(COMMON_PATH, encoding="utf-8") as f:
            _common = frozenset(line.strip().lower() for line in f if line.strip() and not line.startswith("#"))
    return _common


def check_policy(password):
    """Raise PasswordError when the password is too short or on the common list."""
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise PasswordError("a password needs at least %d characters" % MIN_LENGTH)
    if password.lower() in common_passwords():
        raise PasswordError("that password is on the list of common passwords; choose another")


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password, salt, n, r, p, dklen):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen,
                          maxmem=128 * r * n * p * 2 + 1024 * 1024)


def hash_password(password):
    """Check the policy, then hash with a fresh random salt."""
    check_policy(password)
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, N, R, P, DKLEN)
    return "scrypt$%d$%d$%d$%s$%s" % (N, R, P, _b64(salt), _b64(key))


def verify_password(password, stored):
    """True when `password` matches `stored`; False for a wrong password or a malformed hash."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        salt, key = _unb64(salt), _unb64(key)
        candidate = _derive(password, salt, n, r, p, len(key))
    except (AttributeError, ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, key)
