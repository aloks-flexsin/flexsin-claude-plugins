# Plan: run every project's work through the auto-runner, hands-free (central task #1)

## Intent
After a one-time setup, every request to build, fix or change code in an onboarded project is planned, recorded as a
central task, approved by a person on the central page, and built by a runner that keeps going on its own, with no
`/auto-runner:auto` typed per task. Success shows on the central page: a plain prompt such as "add CSV export" turns
into a task with a plan; a runner left watching picks it up, posts its plan, builds it after approval and opens a PR;
people see each runner's state and can pause it.
Out of scope: runners hosted by the service (runners stay local processes), notifications by email or chat, and
changing how a single build runs (worktree, items, tests, push and PR stay as they are).

## Context (what exists to build on)
- `autotasks.py`: take_next / post_plan / start / record / beat / finish against the central task API (personal token).
- `auto_run.py run()`: the whole build flow, `usage_limit()` and `usage_limit.wait`, per-iteration `max_budget_usd`,
  `.auto/STOP`, claim heartbeats through `autotasks.beat`, `RunnerError`, the `USAGE_LIMIT_EXIT` code.
- `autospec.py check`: the spec check as a second `claude -p`, with a fake-claude seam used by its tests.
- `auto_init.py`: preview/apply/--remove with `.auto/init-manifest.json`; already writes `AUTO_RULES.md` on request.
- Central: `tasks.py` (claims, CLAIM_TTL 2 h), `shell.py` (Overview), `runs.py`, `mywork.py`, `ui.confirm_page`,
  `db.COLUMNS_ADDED` / `init_db` for migrations, `tests/auto_runner/fake_central.py` for plugin tests.
- No plugin hooks exist yet (`auto-runner/hooks/` is new).

## Key decisions
- **Routing in two layers.** Layer 1 is a rule plus a `UserPromptSubmit` hook that reminds Claude, in onboarded
  projects only (`central.enabled`), that work goes through the runner. Layer 2 is an opt-in `PreToolUse` guard that
  blocks Edit/Write/MultiEdit/NotebookEdit in the main checkout. Hooks are one small script, `bin/autohook.py`, called
  from `hooks/hooks.json`; it exits 0 and prints nothing outside onboarded projects, so other projects are untouched.
- **Guard escape.** `.auto/run/guard-off-<session id>` (made by `autohook.py allow`, one session only) lets a person do a
  genuine quick fix; the runner's worktree and `.auto/` are never blocked.
- **Headless planner.** `bin/autoplan.py` runs `claude -p` with `models.planner` (default `opus`), the same plan sections
  as `/auto-runner:auto` step 1 (prompt kept in `docs/planner-prompt.md`), then `autospec.check`, one revision round at
  most, and a cost cap (`watch.plan_budget_usd`, default 2). Its plan still waits for approval on the page.
- **Watch loop.** `bin/autowatch.py` (and `/auto-runner:auto watch`): claim next → plan (autoplan) or build (the existing
  `auto_run.run` on the request `autotasks.start` makes) → report → repeat; idle: heartbeat and sleep
  `watch.poll_seconds` (default 300). It reuses `usage_limit.wait`, `.auto/STOP`, the claim handling, and the
  checkout lock (one build per checkout).
- **Daily spend cap.** Each `claude -p` result's `total_cost_usd` is added to `.auto/run/spend-<date>.json`; reaching
  `watch.daily_budget_usd` (default 20) pauses the runner until the next day and says so on the page.
- **Presence and controls on the service.** New table `runner` (account, project, label, state, task, last_seen, pause
  flag, spend today). API: `POST /api/v1/runners/beat` (personal token; returns `pause`/`stop`). Pages: a "Runners"
  block on Overview and Runs, and the person's own runners on My work; Pause/Resume/Stop buttons with confirmation.
  One migration (`init-db` adds the table; nothing else changes).
- **One PR for this task.** A central task builds on one branch with one PR, so the six deliverables become six
  slices committed item by item in that PR (reviewable commit by commit), not six PRs. Splitting into six central
  tasks is the alternative (see Needs a person).

## Slices
1. **Prompt routing reminder.** Init writes the project rule; in an onboarded project every ordinary prompt carries a
   one-line reminder, so Claude plans and records work instead of editing directly. Includes the decision record.
2. **Opt-in guard.** With `"enforce_runner": true`, edits in the main checkout are blocked with a message that points
   to the runner; worktrees and `.auto/` are not; a per-session escape exists.
3. **Headless planning.** `autoplan.py N` plans a claimed task without a person and posts the plan for approval.
4. **Watch mode.** `/auto-runner:auto watch` keeps going: plans new tasks, revises sent-back plans, builds approved
   ones, idles with heartbeats, waits out usage limits and stops at the daily cap or `.auto/STOP`.
5. **Runner presence and controls.** The central page shows each runner (watching, planning #N, building #N, paused,
   silent) on Overview, Runs and My work, and its person or an admin can pause, resume or stop it from the page.
6. **Start it from setup, documented.** `/auto-runner:central` offers to start watch mode as its last step; the guide
   has "Hands-free work".

## Journeys
- **J1 A plain prompt becomes a task.** Slice 1. Roles: developer.
  1. As developer, in an onboarded project, ask Claude "add a CSV export of the orders table".
  *Expect:* Claude answers with a plan and proposes it with `autotasks.py propose`, and the central Tasks page shows the task waiting for approval.
- **J2 The guard stops a direct edit.** Slice 2. Roles: developer.
  1. As developer, set `"enforce_runner": true`, then ask Claude to edit a file in the main checkout.
  *Expect:* the edit is refused with a message naming `/auto-runner:auto`; an edit inside `.auto/` still works.
- **J3 A watching runner builds an approved task.** Slice 4. Roles: developer, manager.
  1. As developer, start `/auto-runner:auto watch` and add a task on the central page.
  2. As manager, approve the plan that appears on the task page.
  *Expect:* the task shows building, then done with a PR link, with nobody typing in the terminal.
- **J4 Pause from the page.** Slice 5. Roles: developer.
  1. As developer, with a runner watching, press Pause on the Overview's Runners block.
  *Expect:* the runner shows paused within one step; Resume makes it watching again.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Prompt routing | Claude Code plugin hooks (`UserPromptSubmit`, `PreToolUse`) https://code.claude.com/docs/en/hooks | free | part of Claude Code | integrate | the documented way to add context and block tools; desktop-app support unverified |
| Headless planning | `claude -p` as the runner already uses it | usage | part of Claude Code | reuse | same engine, flags and cost reporting as builds |
| Scheduling / loop | the runner's own loop and `time.sleep`; OS schedulers (cron, Task Scheduler) | free | n/a | build (small) | a 5-minute poll in the runner needs no extra service or OS setup on Windows and macOS |

## End to end
- Data: one new table on the service (`runner`), added by `python -m central init-db` (a person runs it).
- Security: personal tokens only; a person can pause only their own runners, admins any; every control is audited
  and confirmed; hooks never read or print tokens.
- Errors: lost claim, red tests, blockers and usage limits stop or wait exactly as single runs do; the watch loop
  never exits on a single failed task and records why on the page.
- Rollback: `enforce_runner` off, hooks inert outside onboarded projects, `auto_init.py --remove` takes the rule away,
  stopping watch mode ends everything.
- Docs: README, CHANGELOG, central README and guide, all marked unverified until run on the live service.

## Queue items
- [ ] **H1 Decision record for hands-free work.** Write `docs/decisions/0009-hands-free-runner.md` with the two layers, the planner, the watch loop, presence, and a "Decisions" section with exactly these answers: "Approval: on the central page", "Guard: opt-in per project", "Daily cap: 20 USD per runner", "Poll: 5 minutes", "Pause: the runner's person and admins", "Planner: opus, may plan without a person". Do not change code.
  *Acceptance:* new test `tests/auto_runner/test_decision_0009.py` asserts the file exists and contains each of the six quoted answers; `python -m pytest -q` passes.
  *Slice:* 1 Prompt routing reminder
  *Kind:* internal
- [ ] **H2 Init writes the routing rule.** In `auto_init.py`, add a short "Work goes through the auto-runner" section to `CLAUDE.md` (or `AUTO_RULES.md` when there is none) for projects with `central.enabled`; the rule names `autotasks.py propose`. Show it in the preview, record it in the manifest, take it out with `--remove`. Existing CLAUDE.md content must stay byte-for-byte outside the section.
  *Acceptance:* new tests in `tests/auto_runner/test_init_rule.py` (preview, apply, idempotent, remove, untouched content) pass.
  *Slice:* 1 Prompt routing reminder
  *Kind:* feature
- [ ] **H3 Propose a planned task for approval on the page.** Central: `POST /api/v1/tasks` accepts `"approval": "page"`, storing the task as `plan_review` (plan and items kept, claim held by the runner label) instead of `building`; default unchanged. Plugin: `autotasks.py propose --title T --plan FILE --items FILE` uses it.
  *Acceptance:* `tests/central/test_tasks_propose.py` (stored as plan_review, then approve works, default still building) and a test in `tests/auto_runner/test_central_tasks.py` with `fake_central.py` showing `propose` makes a task that `list` shows as waiting for approval.
  *Slice:* 1 Prompt routing reminder
  *Kind:* feature
- [ ] **H4 UserPromptSubmit reminder hook.** Add `auto-runner/hooks/hooks.json` and `bin/autohook.py prompt`: in a project whose `.claude/auto.json` has `central.enabled`, print a one-line reminder that names `/auto-runner:auto` planning and `autotasks.py propose`; print nothing for other projects, prompts starting with `/`, or an unreadable config. Always exit 0.
  *Acceptance:* `tests/auto_runner/test_autohook.py` covers onboarded (text contains `autotasks.py propose`), not onboarded, slash command and broken config; a test loads `hooks.json` and checks each command points at `autohook.py`.
  *Slice:* 1 Prompt routing reminder
  *Kind:* feature
- [ ] **H5 Opt-in guard hook.** `autohook.py guard` (PreToolUse for Edit, Write, MultiEdit, NotebookEdit): with `"enforce_runner": true`, block paths in the main checkout (exit 2, message naming `/auto-runner:auto`); allow `.auto/`, runner worktrees and paths outside the project. `autohook.py allow` reads the hook input's `session_id` and writes `.auto/run/allow-<session_id>`, which lets that session edit until the file is deleted or 12 hours pass. The escape is turned on by typing `/auto-runner:allow-edits`: the UserPromptSubmit hook sees that prompt, takes the hook input's `session_id` and calls `allow`. Register the guard in `hooks.json` as a PreToolUse entry (matcher `Edit|Write|MultiEdit|NotebookEdit`, command `autohook.py guard`). Add `enforce_runner` (flag, default false) to `config.schema.json` and `autocfg.DEFAULTS`, and update the pinned DEFAULTS dict in `tests/auto_runner/test_config.py`.
  *Acceptance:* tests in `tests/auto_runner/test_autohook.py` prove block, worktree allowed, `.auto/` allowed, `/auto-runner:allow-edits` opens the escape for that session only and it expires after 12 hours, off by default; the hooks.json test checks the PreToolUse entry; `test_config.py` passes with `enforce_runner: true` loading.
  *Slice:* 2 Opt-in guard
  *Kind:* feature
- [ ] **H6 Watch settings and planner prompt.** Add a `watch` section (`poll_seconds` 300, `plan_budget_usd` 2, `daily_budget_usd` 20) to `config.schema.json` and `autocfg.SECTIONS`; write `auto-runner/docs/planner-prompt.md` with the plan sections of `/auto-runner:auto` step 1 (Intent, Key decisions, Slices, Journeys, Reuse check, Queue items, Needs a person).
  *Acceptance:* the schema/defaults sync test passes with `watch`; a config test loads all three keys and refuses an unknown one; a test checks the prompt file names every required section.
  *Slice:* 3 Headless planning
  *Kind:* internal
- [ ] **H7 Headless planner.** `bin/autoplan.py plan N`: build the prompt from the task text (and a sent-back comment) and `docs/planner-prompt.md`, run `claude -p` with `models.planner`, extract the plan and its Queue items, run `autospec.check` with one revision at most, stop at `watch.plan_budget_usd`, then `autotasks.post_plan`. If the revised plan still has findings, post it with them listed under **Needs a person** (a person decides on the page). If the cost cap is hit, post nothing, stop beating that claim (it lapses after 2 hours and the task can be taken again) and write the reason to `.auto/run/watch.json`.
  *Acceptance:* `tests/auto_runner/test_autoplan.py` with a fake claude and `fake_central.py`: a plan is posted with its items, a reply without Queue items is refused, the cost cap posts nothing and records the reason, a plan with remaining findings is posted with them under Needs a person, a sent-back comment reaches the prompt, the prompt file is loaded.
  *Slice:* 3 Headless planning
  *Kind:* feature
- [ ] **H8 Watch loop core.** `bin/autowatch.py`: loop of claim next, then plan (autoplan) or build (`autotasks.start` then `auto_run.run`), then report; when idle, write a local heartbeat to `.auto/run/watch.json` (state, time) and sleep `watch.poll_seconds`; stop on `.auto/STOP`; one failed task never ends the loop. No service presence yet.
  *Acceptance:* `tests/auto_runner/test_autowatch.py` with a fake claude, fake service and fake clock: plans a new task, builds an approved one, writes the idle heartbeat file, stops on STOP, continues after a failed build.
  *Slice:* 4 Watch mode
  *Kind:* feature
- [ ] **H9 Watch guardrails and command.** Daily spend: every `claude -p` the watcher starts (planner, spec check and builds) adds its `total_cost_usd` to `.auto/run/spend-<local date of this computer>.json`; at `watch.daily_budget_usd` it pauses until local midnight; usage-limit waiting reused from `auto_run`; the checkout lock so only one build runs per checkout; `/auto-runner:auto watch` in `commands/auto.md`; README and CHANGELOG.
  *Acceptance:* tests in `tests/auto_runner/test_autowatch.py` prove planning and build costs both count, the cap pauses until local midnight (fake clock across the boundary), a usage limit waits for the reset, a second watcher on the same checkout refuses to build.
  *Slice:* 4 Watch mode
  *Kind:* feature
- [ ] **H10 Runner presence on the service.** Central: `runner` table (schema and `init_db`), `POST /api/v1/runners/beat` (personal token; label, state, task, spend; returns pause/stop), silent after 2 hours; audit. The task API does not change.
  *Acceptance:* `tests/central/test_runners.py`: beat creates and updates, a token cannot beat another person's runner, silent after 2 hours, pause flag returned, `init-db` adds the table to an existing database.
  *Slice:* 5 Runner presence and controls
  *Kind:* feature
- [ ] **H11 Runners on the pages, with controls.** Show runners on Overview, Runs and My work; Pause, Resume and Stop for the runner's person and admins, confirmed (`ui.confirm_page` and `data-confirm`) and audited.
  *Acceptance:* `tests/central/test_runner_pages.py`: who sees which buttons, nothing changes without confirmation, audit rows written, states shown in words.
  *Slice:* 5 Runner presence and controls
  *Kind:* feature
- [ ] **H12 The watch loop reports and obeys.** `autowatch.py` sends `runners/beat` with its state (watching, planning #N, building #N, paused) each step and when idle, and obeys pause (waits) and stop (ends) between steps.
  *Acceptance:* tests in `tests/auto_runner/test_autowatch.py` with `fake_central.py`: beats carry the right states, a paused runner waits without claiming, stop ends the loop cleanly.
  *Slice:* 5 Runner presence and controls
  *Kind:* feature
- [ ] **H13 Start it from setup and document it.** `/auto-runner:central` offers watch mode as its optional last step; guide section "Hands-free work"; central README, plugin README and CHANGELOG updated, all marked unverified until run on the live service.
  *Acceptance:* `tests/auto_runner/test_central_status.py` checks the command and guide mention watch mode, and `python -m pytest -q` (doc-map check included) passes.
  *Slice:* 6 Start it from setup, documented
  *Kind:* internal

## Spec check
First run: 8 findings, all fixed. The planner prompt file and the `watch` config keys now come first (H6), with
`autocfg` updated as well as the schema. `enforce_runner` is added to `autocfg` (H5). The guard escape is defined:
`session_id`, a file under `.auto/run/`, 12 hours. H8's idle heartbeat is local, and the service beats move to H12.
The old H9 is split into pages (H11) and loop (H12). H1's test asserts six named answers. J1 now has its path: the
rule and reminder name `autotasks.py propose` (H2, H4), which H3 adds with a page-approval API flag.
Second run: 5 findings, all fixed. H5 registers the guard in `hooks.json` and says how the escape is turned on
(`/auto-runner:allow-edits`), and it updates the pinned defaults test. H7 says what happens when findings remain (post
them under Needs a person) or the cost cap is hit (post nothing, let the claim lapse). H9 counts planner and build costs
and uses the computer's local date. The two revision rounds allowed are used up; no third check was run.

## Needs a person
- **Decisions (recommended answers are what the items assume):** approve Desktop-recorded tasks on the central page
  (not in chat); guard opt-in per project; daily cap 20 USD per runner, changed in `.claude/auto.json` by whoever
  owns the repo; idle poll 5 minutes; pause: the runner's person and admins (owners not); planner model `opus`, and watch
  mode may plan without a person because the plan still waits for approval before building.
- **One PR or six:** this plan builds the six slices in one PR (the runner's one-branch-per-task flow). If you want six
  PRs, reject this plan and add six tasks instead; each can reuse its slice and items.
- **Desktop hooks:** check that the Claude desktop app runs plugin hooks like the CLI. If it does not, only slice 1's
  rule works there; the docs say so.
- **Live check:** after merging, run `python -m central init-db` (one new table), then start watch mode on one computer
  and follow J3 and J4 on the live service.
