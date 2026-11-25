"""Runner frictions (decision record 0011): counting at ingest into tables shared across projects.

- `record(db, project, kind, subject, at)` counts one occurrence of the signature `kind` or `kind:subject` for a
  project that shares its frictions (`project.share_frictions`, on by default; owners switch it on Settings ›
  Repository). It adds one to `friction_count` (signature + project: count, first and last seen) and to
  `friction_day` (signature + project + UTC day), and creates the `friction_signature` row (state `watching`) the first
  time a signature is seen. A signature row that already exists is left as it is (its state belongs to people and to
  drafting).
- `at` is the service's own time (when the event was received or the decision was made), not the runner's clock, so a
  runner with a wrong clock cannot move a count into another day. Duplicated events never reach here (ingest skips them).
- The caller commits.
"""

STATES = ("watching", "drafted", "muted", "fixed")


def signature(kind, subject=""):
    """-> `kind` or `kind:subject` (the same rule as autoevents.signature)."""
    return "%s:%s" % (kind, subject) if subject else kind


def shares(project):
    """True unless the project switched sharing off (a row read before the column existed counts as on)."""
    value = project.get("share_frictions")
    return value is None or bool(int(value))


def record(db, project, kind, subject, at):
    """Count one friction for `project` at `at` (an ISO UTC time); -> the signature, or None when not shared."""
    row = db.query_one("SELECT share_frictions FROM project WHERE id = ?", (project["id"],))
    if row is None or not shares(row):
        return None
    sig = signature(kind, subject)
    day = at[:10]
    if db.query_one("SELECT signature FROM friction_count WHERE signature = ? AND project_id = ?", (sig, project["id"])):
        db.execute("UPDATE friction_count SET count = count + 1, last_seen = ? WHERE signature = ? AND project_id = ?",
                   (at, sig, project["id"]))
    else:
        db.execute("INSERT INTO friction_count (signature, project_id, count, first_seen, last_seen)"
                   " VALUES (?, ?, 1, ?, ?)", (sig, project["id"], at, at))
    if db.query_one("SELECT day FROM friction_day WHERE signature = ? AND project_id = ? AND day = ?",
                    (sig, project["id"], day)):
        db.execute("UPDATE friction_day SET count = count + 1 WHERE signature = ? AND project_id = ? AND day = ?",
                   (sig, project["id"], day))
    else:
        db.execute("INSERT INTO friction_day (signature, project_id, day, count) VALUES (?, ?, ?, 1)",
                   (sig, project["id"], day))
    if not db.query_one("SELECT signature FROM friction_signature WHERE signature = ?", (sig,)):
        # changed_at stays empty: it marks a person's unmute or a fix, after which only newer days count (R8)
        db.execute("INSERT INTO friction_signature (signature, kind, subject, state) VALUES (?, ?, ?, 'watching')",
                   (sig, kind, subject or ""))
    return sig


def set_sharing(db, project_id, share):
    """Switch sharing on or off; -> the value before (True/False)."""
    row = db.query_one("SELECT share_frictions FROM project WHERE id = ?", (project_id,))
    before = shares(row)
    db.execute("UPDATE project SET share_frictions = ? WHERE id = ?", (1 if share else 0, project_id))
    return before
