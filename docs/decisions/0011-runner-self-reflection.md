# 0011: Runner self-reflection: the runner notices its own friction and drafts improvement tasks

Status: **accepted** (2026-11-25), with the owner's two answers below. Builds on 0007 (central tasks and claims) and
0009 (hands-free work: the headless planner plans open tasks). Feeds 0010 (Supervisor, proposed): this is the source of
the Supervisor's "notice" work. Nothing in this record is built yet; it lands in queue items R2 to R9 of central task
#18 (plan: `docs/plans/task-18-a-reflection-layer.md`), each in its own commit, and stays **unverified** until it runs
on the live service.

## Context

The auto-runner keeps stumbling on the same things: refused shell commands, docs-gate stops, expired claims, central
outages, a newer installed version. Today each one is found only when a person happens to look at a run. The intent is
to make noticing routine: every run, in every project, records each friction as a short signature with no project
content; the central service counts them across projects and shows them on a **Frictions** page; and when a signature
keeps coming back, central opens a task on the auto-runner's own project, so it is planned, approved and fixed like
any other work.

For: whoever looks after the auto-runner (today the owner; later the Supervisor of 0010).

Success shows as: (1) after a run in which the agent was refused `sed` three times, the Frictions page shows
`permission_denied:Bash:sed` with its count, projects and first and last seen; (2) once a signature crosses the
threshold, an open task "Runner friction: permission_denied:Bash:sed" appears on the auto-runner project with the
evidence, its assignee is notified, and it goes through planning and approval as usual; (3) rejecting that task mutes
the signature, so it is not proposed again.

Out of scope: the Supervisor itself (0010) and anything it approves; changing the runner's code automatically (this
layer only opens tasks); sending any project code, file paths, item text, command arguments or customer data.

### The owner's answers

1. **Runs in other people's projects may send friction signatures** to the auto-runner project (each project can opt
   out; see Counting).
2. **Drafted tasks are approved by a person until the Supervisor (0010) exists**, then by the Supervisor.

## Decisions

### 1. A `friction` event type in `event.v1`, not a new channel
Options: (a) a `friction` event in `event.v1`, sent with the other events; (b) a separate endpoint. **Chosen: (a).**
The ledger, the push during the run, retries, redaction, idempotency by event id and the project check all exist
already; this adds one type to both copies of the schema (runner `autoevents.py`, central `ingest.py`), and the
parity test keeps the two equal. A friction's `item` may be null (run-level frictions, such as a refused start) or an
item ID; `friction` is not a run-level type.

### 2. Signatures: eight closed kinds, fixed-vocabulary subjects
`detail = {"kind": K, "subject": S}` and nothing else; the signature is `K`, or `K:S` when there is a subject.

| Kind | Subject |
|---|---|
| `permission_denied` | the tool name; for Bash `Bash:<executable base name>` (env assignments and `cd … &&` prefixes skipped; anything not matching `[A-Za-z0-9._+-]{1,40}` becomes `Bash:other`) |
| `gate_stop` | `docs`, `changelog`, `browser`, `journey`, `suite`, `stale` |
| `fix_round` | `suite`, `journey` |
| `retry` | `exit_<code>` |
| `usage_stop` | `limit`, `budget`, `threshold` |
| `refused_start` | `newer_installed`, or the preflight group that failed (`worktree`, `central`, `settings`, …; `other`) |
| `central_error` | `http_<status>:<route>` with digits replaced by `N` and no query (`http_409:/api/v1/tasks/N/heartbeat`), or `unreachable` |
| `plan_changes` | none (recorded by central when a plan is sent back for changes) |

Both sides validate the kind against the list and the subject against `^[A-Za-z0-9_.+:/-]{0,80}$`, and refuse any
other key, so free text cannot ride along. The runner emits a signature at most once per item per run, so a loop
counts once.

### 3. Counting at ingest, into shared tables, with a per-project opt-out
Options: (a) scan stored event bodies when the page loads; (b) count when events are stored. **Chosen: (b).** Ingest
upserts small tables: `friction_count` (signature, project, count, first and last seen), `friction_day` (per UTC day,
for the 30-day window and counts after a fix) and `friction_signature` (kind, subject, state `watching`, `drafted`,
`muted` or `fixed`, the linked task, the previous task, who changed it, when and why). A duplicate event id changes
nothing. One migration (`init-db`, run by a person). A project can opt out with `project.share_frictions` (default on,
an owner's toggle in Settings, audited): its events are still stored as its own but are not counted in the shared
tables. The Frictions page is for admins only.

### 4. Drafting reuses the task flow
Options: (a) a reflection Claude call that writes a plan; (b) central opens an **open** task with the evidence and the
normal flow plans it (the assignee's runner or 0009's headless planner, with the spec check) and waits for approval.
**Chosen: (b):** deterministic and testable, no new cost or credentials on the hosting, and the same planning rules,
spec check and approval gate as any task. The evidence is the counts, the number of projects, first and last seen,
and a pointer to the code area for the kind (for example `permission_denied` → the `.claude/settings.json` allow list
and `auto-runner/bin/autohook.py`). The assignee is notified as for any assigned task; the audit row says it came
from reflection.

### 5. Thresholds and caps, configured by environment
- Drafting is on only when `CENTRAL_REFLECTION_PROJECT` names the auto-runner's project.
- A signature is due at `CENTRAL_REFLECTION_MIN_COUNT` (default **3**) occurrences or `CENTRAL_REFLECTION_MIN_PROJECTS`
  (default **2**) projects within the last **30 days**.
- At most `CENTRAL_REFLECTION_DAILY` (default **3**) drafts per UTC day.
- Never while the signature already has an open task or is muted.
- Assignee: `CENTRAL_REFLECTION_ASSIGNEE` (the e-mail of a member of that project), else the project's first owner.

### 6. Closing the loop
- The drafted task is **rejected** (at plan review, or closed while still open) → the signature becomes `muted`
  ("task #N rejected") and is not drafted again.
- The task is **done** → the signature becomes `fixed`. Only occurrences on days after the fix count toward the
  threshold; reaching it again drafts a **regression** task, "Runner friction came back: <signature>", linking the
  earlier task.
- Admins can mute (with a reason) and unmute a signature on the page (confirmed, CSRF, audited). Unmuting counts only
  occurrences after it, as a fix does.

### 7. Approval stays with people
Drafted tasks are planned and approved like any task, by a person, until 0010's Supervisor exists; it will then approve
them through the decide API (#38) like any plan. Nothing here changes for that.

## Consequences

- The runner's friction becomes visible across every project without anyone reading logs, and recurring friction
  turns into ordinary, reviewable tasks on the auto-runner project.
- Both copies of `event.v1` grow by one type and its validation; the parity test must cover it.
- Central gains three tables and one column; a person runs `python -m central init-db` after the counting item ships,
  and sets `CENTRAL_REFLECTION_PROJECT` (and optionally the assignee, thresholds and daily cap) to switch drafting on.
- Thresholds are deliberately simple (counts per 30 days); a noisy signature is handled by muting, not by tuning code.
- A drafted task costs nothing until someone plans it; the daily cap bounds the number of new tasks.
- A deliberate event (for example a reassignment that makes a heartbeat get 409) can still be counted; it is muted from
  the page if it ever drafts a task.
- 0010's Supervisor inherits this as its "notice" work: it reads the same signatures and approves drafted tasks instead
  of a person.

## Privacy

- **No project content leaves a project.** A friction carries only a kind from a closed list and a subject from a
  fixed vocabulary or a tool or executable name: never file paths, command arguments, item text, code, plan text or
  customer data.
- This is enforced by validation on both sides (runner on write, central on ingest), not by convention: an unknown
  kind, a subject outside `^[A-Za-z0-9_.+:/-]{0,80}$` or any extra key is refused.
- Frictions travel over the existing authenticated event push with its redaction; no new channel or secret.
- Each project's owner can switch sharing off; its frictions then stay with the project and are not counted in the
  shared tables.
- Only admins see the cross-project Frictions page; project names appear there, project content does not.
