"""Projects, their members and the access model.

- A project has a slug (lower-case letters, digits and dashes) and a name. Only admins create projects.
- Members hold one role: owner > manager > developer > viewer. A role includes everything the roles below it may do.
- Admins see and manage every project as if they were its owner, without being a member.
- Anyone else who is not a member with at least the needed role gets 404, never 403, so a project's existence is
  not revealed to people outside it.
- Owners and admins manage members. The last owner of a project cannot be removed or demoted.
"""
import re
import secrets

from . import accounts

MEMBER_ROLES = ("viewer", "developer", "manager", "owner")
RANK = {role: rank for rank, role in enumerate(MEMBER_ROLES)}
ADMIN = "admin"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
MAX_NAME = 200


class ProjectError(ValueError):
    """The project or membership cannot be created or changed as asked."""


def normalize_slug(slug):
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ProjectError("the slug must be 1-64 lower-case letters, digits or dashes, not starting or ending with a dash")
    return slug


def create(db, slug, name, now):
    """Create and commit a project; return its row."""
    slug = normalize_slug(slug)
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ProjectError("the name must be 1-%d characters" % MAX_NAME)
    if get_by_slug(db, slug) is not None:
        raise ProjectError("a project with that slug already exists")
    project_id = secrets.token_hex(16)
    db.execute("INSERT INTO project (id, slug, name, created_at) VALUES (?, ?, ?, ?)", (project_id, slug, name, now))
    db.commit()
    return get_by_slug(db, slug)


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
    return db.query("SELECT m.account_id, m.role, m.created_at, a.email, a.disabled FROM member m"
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
