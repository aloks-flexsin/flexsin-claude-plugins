"""Projects, their members and the access model.

- A project has a slug (lower-case letters, digits and dashes) and a name. Only admins create projects.
- Members hold one role: owner > manager > developer > viewer. A role includes everything the roles below it may do.
- Admins see and manage every project as if they were its owner, without being a member.
- Anyone else who is not a member with at least the needed role gets 404, never 403, so a project's existence is
  not revealed to people outside it.
- Owners and admins manage members. The last owner of a project cannot be removed or demoted.
- A project names its git repository (`repo_url`), so a runner can check it is pointed at the right checkout. Only
  `https://host/owner/repo`, `ssh://[user@]host[:port]/owner/repo` and `user@host:owner/repo` forms are taken, never
  with a password or token in them: the service never holds git credentials. Owners and admins change it.
"""
import re
import secrets
from urllib.parse import urlsplit

from . import accounts

MEMBER_ROLES = ("viewer", "developer", "manager", "owner")
RANK = {role: rank for rank, role in enumerate(MEMBER_ROLES)}
ADMIN = "admin"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
MAX_NAME = 200
MAX_REPO_URL = 500
_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)+/?$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SCP_RE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[^:/@]+):(?P<path>[^/].*)$")


class ProjectError(ValueError):
    """The project or membership cannot be created or changed as asked."""


def normalize_slug(slug):
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ProjectError("the slug must be 1-64 lower-case letters, digits or dashes, not starting or ending with a dash")
    return slug


REPO_URL_HELP = ("the repository URL must be https://host/owner/repo, ssh://host/owner/repo or git@host:owner/repo,"
                 " with no password or token in it")


def _repo_parts_ok(host, path):
    return bool(host and _HOST_RE.match(host) and _REPO_PATH_RE.match(path) and ".." not in path.split("/"))


def normalize_repo_url(url):
    """The trimmed repository URL, or ProjectError. Credentials (`user:pass@`, any user in an https URL), queries,
    fragments, other schemes and local paths are refused."""
    url = (url or "").strip()
    if not url or len(url) > MAX_REPO_URL or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ProjectError(REPO_URL_HELP)
    scp = _SCP_RE.match(url)
    if scp and "://" not in url:
        if not _repo_parts_ok(scp.group("host"), scp.group("path")):
            raise ProjectError(REPO_URL_HELP)
        return url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ProjectError(REPO_URL_HELP) from None
    if parts.scheme not in ("https", "ssh") or "?" in url or "#" in url:
        raise ProjectError(REPO_URL_HELP)
    if parts.password is not None or (parts.scheme == "https" and "@" in parts.netloc):
        raise ProjectError(REPO_URL_HELP)
    if parts.username is not None and not _USER_RE.match(parts.username):
        raise ProjectError(REPO_URL_HELP)
    if port == 0 or not parts.path.startswith("/") or not _repo_parts_ok(parts.hostname, parts.path[1:]):
        raise ProjectError(REPO_URL_HELP)
    return url


_KEY_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_KEY_PATH_RE = re.compile(r"^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)+$")


def repo_key(url):
    """`https://host/o/r(.git)`, `ssh://[user@]host[:port]/o/r` and `user@host:o/r` -> 'host/o/r' (lower case, no user,
    port or .git), so one repository compares equal in every form; None for anything else. The plugin's
    autocentral.repo_key does the same (a test keeps them equal)."""
    url = (url or "").strip()
    if not url or any(c.isspace() for c in url):
        return None
    m = _SCP_RE.match(url)
    if m and "://" not in url:
        host, path = m.group("host"), m.group("path")
    else:
        try:
            parts = urlsplit(url)
            parts.port
        except ValueError:
            return None
        if parts.scheme not in ("https", "http", "ssh", "git") or parts.query or parts.fragment:
            return None
        host, path = parts.hostname, parts.path
    path = (path or "").strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not host or not _KEY_HOST_RE.match(host) or not _KEY_PATH_RE.match(path) or ".." in path.split("/"):
        return None
    return "%s/%s" % (host.lower(), path.lower())


def slug_from_repo(url):
    """The slug a repository gets: its name, lower case, other characters as '-' -> e.g. 'case-study-va'.
    The plugin's autocentral.slug_from_repo does the same (a test keeps them equal). None when there is no key."""
    key = repo_key(url)
    if key is None:
        return None
    return _slugify(key.rsplit("/", 1)[1]) or None


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64].strip("-")


def get_by_repo_key(db, key):
    return db.query_one("SELECT * FROM project WHERE repo_key = ?", (key,)) if key else None


def _free_slug(db, url):
    """The repository's name as a slug, or '<owner>-<repo>' when that is taken; ProjectError when both are."""
    key = repo_key(url)
    parts = key.split("/")
    for candidate in (_slugify(parts[-1]), _slugify("-".join(parts[-2:]))):
        if candidate and SLUG_RE.match(candidate) and get_by_slug(db, candidate) is None:
            return candidate
    raise ProjectError("no free slug for this repository: enter one")


def _refuse_taken_repo(db, key, project_id=None):
    other = get_by_repo_key(db, key)
    if other is not None and other["id"] != project_id:
        raise ProjectError('this repository already belongs to project "%s": one project per repository' % other["slug"])


def create(db, slug, name, now, repo_url):
    """Create and commit a project; return its row. One project per repository: a repository that another project
    already has (in any URL form) is refused. An empty slug is filled in from the repository (slug_from_repo, or
    '<owner>-<repo>' when taken) and an empty name with the repository's name."""
    repo_url = normalize_repo_url(repo_url)
    key = repo_key(repo_url)
    if key is None:
        raise ProjectError(REPO_URL_HELP)
    _refuse_taken_repo(db, key)
    slug = normalize_slug(slug) if (slug or "").strip() else _free_slug(db, repo_url)
    name = (name or "").strip() or key.rsplit("/", 1)[1]
    if len(name) > MAX_NAME:
        raise ProjectError("the name must be 1-%d characters" % MAX_NAME)
    if get_by_slug(db, slug) is not None:
        raise ProjectError("a project with that slug already exists")
    project_id = secrets.token_hex(16)
    db.execute("INSERT INTO project (id, slug, name, repo_url, repo_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (project_id, slug, name, repo_url, key, now))
    db.commit()
    return get_by_slug(db, slug)


def set_repo_url(db, project_id, repo_url):
    """Change a project's repository URL and commit; return the previous value (None when it was not set)."""
    repo_url = normalize_repo_url(repo_url)
    key = repo_key(repo_url)
    if key is None:
        raise ProjectError(REPO_URL_HELP)
    before = db.query_one("SELECT repo_url FROM project WHERE id = ?", (project_id,))
    if before is None:
        raise ProjectError("no such project")
    _refuse_taken_repo(db, key, project_id)
    db.execute("UPDATE project SET repo_url = ?, repo_key = ? WHERE id = ?", (repo_url, key, project_id))
    db.commit()
    return before["repo_url"]


AUTONOMY = ("auto", "manual")


def autonomy(project):
    """A project's autonomy: "auto" (the default; the supervisor decides plans and merges) or "manual" (a person
    decides, as before decision record 0012). A database that has not run init-db yet has no column: "auto"."""
    value = (project or {}).get("autonomy")
    return value if value in AUTONOMY else "auto"


def set_autonomy(db, project_id, value):
    """Switch a project to "auto" or "manual"; -> the value before. The caller audits and commits."""
    if value not in AUTONOMY:
        raise ProjectError('autonomy is "auto" or "manual"')
    before = autonomy(db.query_one("SELECT * FROM project WHERE id = ?", (project_id,)))
    db.execute("UPDATE project SET autonomy = ? WHERE id = ?", (value, project_id))
    return before


def get_by_slug(db, slug):
    return db.query_one("SELECT * FROM project WHERE slug = ?", ((slug or "").lower(),))


def visible_to(db, account):
    """The projects an account may see: all for an enabled admin, else the ones it is a member of."""
    if not accounts.is_enabled(account):
        return []
    if account["role"] == ADMIN:
        return db.query("SELECT * FROM project ORDER BY slug")
    return db.query("SELECT p.*, m.role AS member_role FROM project p JOIN member m ON m.project_id = p.id"
                    " WHERE m.account_id = ? ORDER BY p.slug", (account["id"],))


def member_role(db, project_id, account_id):
    row = db.query_one("SELECT role FROM member WHERE project_id = ? AND account_id = ?", (project_id, account_id))
    return row["role"] if row else None


def effective_role(db, project, account):
    """'admin' for an enabled admin, the member role for a member, else None."""
    if project is None or not accounts.is_enabled(account):
        return None
    if account["role"] == ADMIN:
        return ADMIN
    return member_role(db, project["id"], account["id"])


def allows(role, min_role):
    """True when `role` (a member role, 'admin' or None) is at least `min_role`."""
    if min_role not in RANK:
        raise ValueError("unknown project role: %r" % (min_role,))
    if role == ADMIN:
        return True
    return role in RANK and RANK[role] >= RANK[min_role]


def members(db, project_id):
    """Members with `pending` = 1 while the account has no password yet (invited, link not used)."""
    return db.query("SELECT m.account_id, m.role, m.created_at, a.email, a.disabled,"
                    " CASE WHEN a.password_hash = '!' THEN 1 ELSE 0 END AS pending FROM member m"
                    " JOIN account a ON a.id = m.account_id WHERE m.project_id = ? ORDER BY a.email", (project_id,))


def _check_role(role):
    if role not in RANK:
        raise ProjectError("role must be one of %s" % ", ".join(MEMBER_ROLES))


def add_member(db, project_id, email, role, now):
    """Add an existing account by email and commit; return the account row."""
    _check_role(role)
    account = accounts.find_by_email(db, email)
    if account is None:
        raise ProjectError("no account with that email")
    if member_role(db, project_id, account["id"]) is not None:
        raise ProjectError("that account is already a member")
    db.execute("INSERT INTO member (project_id, account_id, role, created_at) VALUES (?, ?, ?, ?)",
               (project_id, account["id"], role, now))
    db.commit()
    return account


# Appended to an UPDATE/DELETE's WHERE so that, in the same statement, the row is left alone when it is the
# project's only owner: a project without an owner has nobody but admins to manage it.
_NOT_LAST_OWNER = (" AND NOT (role = 'owner' AND"
                   " (SELECT COUNT(*) FROM member WHERE project_id = ? AND role = 'owner') <= 1)")


def _require_member(db, project_id, account_id):
    role = member_role(db, project_id, account_id)
    if role is None:
        raise ProjectError("not a member")
    return role


def set_member_role(db, project_id, account_id, role):
    """Change a member's role and commit; return the previous role."""
    _check_role(role)
    before = _require_member(db, project_id, account_id)
    sql = "UPDATE member SET role = ? WHERE project_id = ? AND account_id = ?"
    params = [role, project_id, account_id]
    if role != "owner":
        sql += _NOT_LAST_OWNER
        params.append(project_id)
    if db.execute(sql, params).rowcount == 0:
        raise ProjectError("the last owner cannot be demoted")
    db.commit()
    return before


def remove_member(db, project_id, account_id):
    """Remove a member and commit; return the role it had."""
    before = _require_member(db, project_id, account_id)
    cur = db.execute("DELETE FROM member WHERE project_id = ? AND account_id = ?" + _NOT_LAST_OWNER,
                     (project_id, account_id, project_id))
    if cur.rowcount == 0:
        raise ProjectError("the last owner cannot be removed")
    db.commit()
    return before
