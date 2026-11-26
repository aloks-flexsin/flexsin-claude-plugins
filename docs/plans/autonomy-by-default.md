# Autonomy by default, manual mode on request

## Status and hand-over (2026-11-26, end of the first session)
- **Phase 1** (runner pushes as it goes, takes over a task's branch): merged, PR #60.
- **Phase 2** (shared task pool, Take/Reassign/Release, merged → done): merged, PR #59. After deploying central run
  `python -m central init-db` once (from `auto-runner/`, with `DATABASE_URL` set).
- **Phase 4** (item agents in Claude Code's sandbox; allowlist kept on native Windows): PR #61 open, stacked on #60
  (now merged), 1410 runner tests green locally. Unverified against a live sandbox.
- **Phase 3a** (client: `autonomy` key, plan_gate/merge_gate decide in auto mode, self-merge, default policy): PR #62
  open. The full `tests/auto_runner` run had not finished when it was pushed; CI runs it.
- **Next: Phase 3b** (central, `auto-runner/central/supervisor.py plan_decision` and `tasks.py`): project `autonomy`
  column (owner/admin switch on Settings › Repository, audited), per-task `manual` flag (task page), and in auto mode no
  escalation for supervisor-added tasks and an approve (with reasons) instead of escalate after `PLAN_ROUND_CAP`; let
  the supervisor decide without manager membership (`tasks.can_decide`). Existing tests in
  `tests/central/test_supervisor_api.py` that pin escalation must set manual mode. Then Phases 5, 6, 7, 8 below.
- Worktrees used: `../flexsin-claude-plugins-p1` (Phase 1), `-p4` (Phase 4), `-p3` (Phase 3a); each is on its branch.
  Branch names follow `alok/alok-flexsin-dsk-claude-desktop/<type>/<brief>`.
- The 15-minute supervisor loop was stopped at the user's request.

## Context
Alok wants auto-runner to finish work end to end with no person in the loop: plan, approve, build, merge, deploy,
recover stuck work. A person steps in only when they explicitly ask for manual mode. Today 30+ gates force a person in
(inventory from this session): plans wait for approval, "Needs a person" escalates, supervisor-added tasks escalate, the
merge gate refuses self-merges, blockers need an owner, CLAUDE.md forbids merging/deploying, only admins reassign, a
runner takes only its own person's tasks, and the runner pushes only at the end of a clean run, so stuck work is
invisible to everyone else. Decisions taken with Alok: **fully automatic production deploys**, **self-merge allowed**,
**Claude Code's built-in sandbox** as the default execution environment, **shared task pool** (a runner may take another
person's task only when that person's watcher is not active). This work is done in this session (not queued to a
runner), as several pull requests, each on its own branch `alok/alok-flexsin-dsk-claude-desktop/<type>/<brief>`.

Kept on purpose (security, not process): secrets never read or committed (`.env*`, keys, `.auto/run/accounts.json` deny
rules), no force-push, no direct push to `main` (everything lands through a PR with green CI), tokens only from env.

## One switch: `autonomy`
New `.claude/auto.json` key `autonomy: "auto" | "manual"` (default **auto**, `autocfg.py`, `config.schema.json`), plus a
per-task `manual` flag on central tasks (a person ticks it; the supervisor then leaves that task alone). Every gate below
reads it; `manual` restores today's behaviour exactly.

## Phase 1 – Runners push as they go; anyone can continue a branch (`auto-runner/bin`)
- `auto_run.py`: after every committed item, push the branch (reuse `publish()` 766-813 push part, no PR) and open the
  PR as a **draft** on the first push; mark it ready at the end of a clean run. Push also in the `finally` block
  (1834-1849) and on every early `return` (claim_lost, usage_limit, red, budget, no_progress…).
- Replace local stash handovers (`set_aside` 286-294, `restore_handover` 302-340) with a pushed `wip(<item>): unfinished`
  commit; the next attempt (any runner) resets it softly and continues.
- `autotasks.start` (191-206) and `run()` (1178-1191): when the task has a `branch`, fetch it and continue on it instead
  of starting from `origin/main`; `publish()` (780) accepts a branch the task hands over, not only the own prefix.
- `autosafe.PUSH_DENIED` stays (the agent never pushes; the runner does).

## Phase 2 – Easy assign, reassign, take-up; no stuck tasks (`auto-runner/central`)
- `tasks.take_next` (187-218): own tasks first, then the pool: unassigned tasks, and other people's ready tasks whose
  assignee has **no active watcher** (no runner beat within `runners` liveness window) or whose claim expired. Taking
  one reassigns it and records `task.take` (from, to, reason). Assignee becomes optional (`create` 143+).
- Routes + buttons on the task page: **Take it** (any developer+), **Reassign** and **Release** (assignee, manager+,
  supervisor) – today `task_release` (846-871) is admin-only. Supervisor API gets `release`/`reassign`.
- Reconcile: when the supervisor records a merge `done` (`supervisor.py` 287), set the task `done`; `report(stopped)`
  releases the claim instead of keeping it, so stalled tasks (like 27) return to the pool with their branch.
- Supervisor may run as a pool member's helper: `_can_run` (tasks.py 88-91) unchanged for the supervisor account; the
  runner label ownership check (`runners.py` 101) allows a label's owner to change when that person reassigns it.

## Phase 3 – Gates decide instead of escalating (auto mode)
- `autogates.plan_gate`: open "Needs a person" lines no longer escalate; they become reasons ("decided by supervisor:
  …"). `merge_gate`: identity check skipped (`merge.allow_self_merge`, default true in auto). `restricted_paths` shrink to
  secrets only (`.env*`, `*.pem`, `*.key`); CI green stays mandatory.
- `supervisor.py plan_decision`: no escalation for supervisor-added tasks or after `PLAN_ROUND_CAP`; after the cap the
  supervisor approves the latest plan with its reasons. `tasks.decide`/`can_decide`: the supervisor may decide without a
  manager membership.
- Blockers (`blockers.py`, runner `.auto/blockers`): in auto mode the supervisor answers each blocker with its
  `recommendation` (recorded as `blocker.answer` by supervisor); a person can override later.
- Planner (`docs/planner-prompt.md`, `commands/auto.md`): "Needs a person" becomes **Decisions taken** (with the
  choice and why); deploy, live checks and account setup become items or supervisor steps, not person work.
- `autoplan.py`: spec-check findings that are still open become Decisions taken, not Needs a person.
- Supersedes task 29 (its X1–X4 are folded in); close task 29 with a pointer to this work.

## Phase 4 – Sandbox by default (`auto_run.py`, `autoplan.py`, `autosafe.py`)
- Every `claude -p` the runner starts gets `--settings` with Claude Code's sandbox on (`sandbox.enabled: true`,
  `autoAllowBashIfSandboxed: true`, write access limited to the worktree, network allowlist for git host + package
  registries). Inside it the shell allowlist `BASE_ALLOWED` (101, 1207) is dropped: any command may run; `git push`
  stays denied.
- Preflight (`autosafe.py` 527-561): detect whether the sandbox works on this OS (Windows needs WSL); if not, refuse in
  auto mode with the exact fix, and fall back to today's allowlist only in manual mode. Verify current sandbox setting
  names against the Claude Code docs before coding.

## Phase 5 – Supervisor merges, deploys and verifies (`autosupervisor.py`, `autoforge.py`)
- After a merge, a new **deploy step** runs the project's `deploy` config (new `.claude/auto.json` section:
  `command`, `smoke_url`, `rollback_command`; central: `vercel deploy --prod` with `VERCEL_TOKEN` from env). Smoke check
  fails → rollback command + a new fix task added to the pool (source: the failed deploy). Audited as
  `supervisor.deploy.*`.
- Supervisor loop runs unattended (`/auto-runner:supervisor` gets `watch` like the runner), covering every project in
  `AUTO_SUPERVISOR_PROJECTS` with each project's own repo dir (fixes the ERP pass reading this repo's policy).
- `/auto-runner:init` adds a minimal CI workflow when a project has none (fixes ERP task 4 "no CI checks").

## Phase 6 – Rules and docs
- `CLAUDE.md`: replace "never merge your own PR / never deploy / ask before branch identity" with the autonomy rules
  (PR + green CI, supervisor merges and deploys, manual mode on request; identity from env, ask only when missing).
- `docs/decisions/0012-autonomy-by-default.md` (amends 0009 and 0010 sections 4, 6, 7), READMEs, doc-map.
- `.claude/supervisor-policy.json` and `.claude/settings.json` updated to match (settings deny rules for secrets kept).

## Phase 7 – Fewer, clearer commands (`auto-runner/commands`, README)
With autonomy the default, most of today's 13 commands are steps a person no longer runs. New set (verbs a person
would guess), each previewing before it changes anything:

| New | Replaces | Does |
|---|---|---|
| `/auto-runner:setup` | `init`, `setup`, `central`, `join`, `doctor` | one guided setup/check: config, sign-in, identity, sandbox, CI, deploy settings; `setup check` is read-only |
| `/auto-runner:start` | `auto watch` | start working on the pool hands-free (runner; `start supervisor` runs the supervisor loop) |
| `/auto-runner:stop` | creating `.auto/STOP` by hand | stop this computer's runner or supervisor cleanly |
| `/auto-runner:add <what>` | `auto <task>` | add a task to the pool (planned, approved and built automatically in auto mode) |
| `/auto-runner:status` | `status`, `log` | queue, runs, branches, PRs, deploys, anything waiting on a person |
| `/auto-runner:manual [on/off] [task N]` | `allow-edits`, `resume` | switch the project or one task to manual mode |
| `/auto-runner:update` | `update` | unchanged |

**Persistent work through `/loop` (added by Alok, 2026-11-26).** A person keeps the whole lifecycle running from their
own Claude session with Claude Code's `/loop`: `/auto-runner:start` offers (and its docs show) `/loop 15m
/auto-runner:tick`, where `tick` is one idempotent step of the lifecycle – take or continue a task from the pool, plan
it, build the next items, supervise (decide plans, merge green PRs, release, deploy) – and returns. No separate
background process or extra account is needed; stopping is ending the loop (or `/auto-runner:stop`). The background
watcher (`autowatch.py`) stays for machines nobody sits at.

`spec`, `resume` and `supervisor` become internal steps. Old names stay one release as one-line stubs that say the new
name and run it. Script entry points (`bin/*.py`) keep working; only the commands and docs change.

## Phase 8 – User-level access; the supervisor becomes a lifecycle stage (added by Alok, 2026-11-26)
- **User-level auth only in the plugin.** The plugin works with the signed-in person's own access: it lists and offers
  only projects that person is a member of (never projects they are not assigned to), and adding a member follows that
  person's own project role. No "log in as supervisor" step.
- **Any developer adds tasks.** Adding a task (for themselves or the pool) needs only a developer sign-in in the plugin;
  no supervisor, manager or other special role (Phase 2 already allows it on the central page and the pool).
- **Supervisor as a stage, not a role.** One lifecycle per task, run by the auto-runner under the person's identity:
  **Task → Plan** (requirement analysis, estimation, specs) **→ Build** (architect skill, design skill, coding)
  **→ Supervise** (delivery manager: task management, merge PRs, release, tests). The separate supervisor account and
  sign-in go away (the `supervisor` role stays only as a back-compat alias); Phase 3 gates are evaluated by the
  Supervise stage. Central's supervisor-only routes also accept a developer+ member's token acting as that stage,
  audited with the stage name.
- **Zero setup for the Supervise stage.** A person sets up no environment variable for it (no `AUTO_SUPERVISOR_*`,
  no supervisor token, no supervisor account). Everything it needs comes from the person's existing plugin sign-in
  (device-code personal token), the project's `.claude/auto.json`, the repository's git remote and the central
  project settings. Existing supervisor env vars become optional overrides only; secrets that a deploy needs (e.g. a
  Vercel token) are stored per project on central or read from the forge, never typed into an env var by the person.
- **Folded in from closed central tasks 20, 24, 29** (closed 2026-11-26 at Alok's request; their scope lives here now):
  - From **#29** (X1–X4: approve plans with open "Needs a person", self-merge, decide plans of tasks it added, owner
    toggle): covered by Phase 3 and the `autonomy` switch; the per-task `manual` flag replaces the owner toggle.
  - From **#24** (supervisor mode): the per-project switch becomes `autonomy: auto|manual` (audited, owner/admin on the
    settings page, optional "auto for N days" expiry). The supervisor ↔ watcher channel (control words resume, wake,
    release, replan with a comment; a per-runner command queue pending → delivered → acknowledged reusing the delivery
    pattern; presence levels silent/dead; every instruction audited) becomes part of the Supervise stage, Phase 5.
  - From **#20** (watcher robustness): detect the base branch with `git ls-remote --symref origin HEAD` as a fallback;
    check the base exists before planning (no spend on a task that can never build); setup errors pause the watcher
    and raise one idempotent blocker (`watch-setup:<project>:<branch>`) instead of retrying forever; an exhausted task
    halts claiming until restart. And release versioning for every project auto-runner builds: changelog on by default
    in init/setup, the project's own version file (package.json, pyproject.toml, plugin.json, VERSION or
    `release.version_files`), SemVer bump from [Unreleased] through a PR, configurable `release.tag_format`
    ("v{version}" default). These join Phase 1 (watcher) and Phase 5 (release).

## One-time setup only a person can do (cannot be automated)
- Sign this computer in with Alok's own account (device code in the browser), restart Claude Code, delete `.auto/STOP`.
- Give each project its deploy credentials once on the central project settings page (no environment variable on any
  machine; see Phase 8, zero setup for the Supervise stage).
- Install WSL if the Claude Code sandbox is not available natively on Windows.

## Order and overlaps
Phase 1 → 2 → 3 → 4 → 5 → 6 → 7, each its own PR. Task 28 (prompt paragraph about Edit/Write) is being built by another
runner and touches `build_prompt`; Phase 4 removes the allowlist it describes, so Phase 4 updates that paragraph.
Release a new plugin version after Phase 3 and after Phase 5; deploy central after Phases 2, 3 and 5.

## Verification
- Per phase: targeted pytest (`tests/auto_runner/test_runner.py`, `test_supervisor_gates.py`, `test_supervisor_pass.py`,
  `tests/central/test_supervisor_api.py`, new `test_task_pool.py`, `test_autonomy.py`), all with the existing fakes
  (`fake_central.py`, fake claude, fake forge); full suite on each PR (`tests` check).
- End to end (fakes): a runner stops mid-task after 2 of 4 items → branch pushed with a draft PR → task returns to the
  pool → a second runner (different person, first one's watcher inactive) takes it, continues the same branch, finishes
  → supervisor approves nothing by hand, merges with self-merge, deploy step runs the fake deploy and smoke check.
- Manual mode: the same flow with `autonomy: "manual"` reproduces today's escalations exactly (existing tests unchanged).
- Live (after deploy and release): one small real task in flexsin-claude-plugins goes from creation to merged and
  deployed with no person, watched on the Runs and task pages.
