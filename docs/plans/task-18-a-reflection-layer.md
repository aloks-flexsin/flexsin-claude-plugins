# Plan: a reflection layer: the runner notices its own friction and drafts improvement tasks (central task #18)

## Intent
The auto-runner keeps stumbling on the same things (refused shell commands, docs-gate stops, expired claims, central
outages, a newer installed version), and today each one is found only when a person happens to look. This makes
noticing routine: every run, in every project, records each friction as a short signature with no project content;
central counts them across projects on a **Frictions** page; and when a signature keeps coming back, central opens a
task on the auto-runner's own project so it gets planned, approved and fixed like any other work.

For: whoever looks after the auto-runner (today the owner; later the Supervisor of decision record 0010).
Success: (1) after a run in which the agent was refused `sed` three times, the Frictions page shows
`permission_denied:Bash:sed` with its count, projects and first/last seen; (2) once a signature crosses the threshold,
an open task "Runner friction: permission_denied:Bash:sed" appears on the auto-runner project with the evidence, its
assignee is notified, and it goes through planning and approval as usual; (3) rejecting that task mutes the signature,
so it is not proposed again.

Out of scope: the Supervisor itself (0010) and anything it approves; editing the runner's code automatically (the
layer only opens tasks); sending any project code, file paths, item text, commands' arguments or customer data.

Owner's answers (recorded on the task): runs in other people's projects **may** send friction signatures to the
auto-runner project; drafted tasks are **approved by a person until the Supervisor exists**, then by the Supervisor.

## Context (what exists to build on)
- Runner ledger `auto-runner/bin/autoevents.py`: `event.v1`, `TYPES`, `Ledger.emit`, validated on write; pushed to
  central by `autodeliver.push` during the run (`push()` in `auto_run.run`), resumable from a cursor.
- Central ingest `auto-runner/central/ingest.py`: its own copy of `event.v1` (parity test in `tests/central/test_ingest.py`),
  all-or-nothing batches, redaction, idempotent by event id, `store(db, project, events, at)`.
- Where friction already shows in `auto_run.py`: `permission_denials(out)` (≈line 1527), the docs gate (`entry['result']
  == 'docs'`), changelog refusal, suite fix rounds (`fix_rounds`, `max_fix_rounds`), journey fix rounds, the early-exit
  retry (`agent exited with <code>; retried next iteration`), `usage_limit(...)` / budget / plan-usage threshold stops,
  `RunnerError` refusals before start (newer version installed; `autosafe.preflight` groups: worktree, runner_name,
  model, base_url, journey, central, env_file, path_prepend, publish, principles, capability, no commit, no test
  command, settings), `push()` errors and skipped events, and task heartbeat/report failures (`autotasks.beat`/`finish`, 409).
- Central tasks `tasks.create(db, project, by, assignee, title, description, moment, status="open")`,
  `tasks.notify_assigned`, `tasks.decide` (approve / changes / reject), task statuses up to `done` / `rejected`;
  `audit`; `notifications` (push since #31/#42); `db.COLUMNS_ADDED` + `schema.sql` for migrations (`init-db`).
- Decision record 0010 (Supervisor, proposed): its "adding tasks" power and the decide API (#38) are where the
  Supervisor will later approve these tasks. Decision 0009: the watcher plans open tasks headlessly.

## Key decisions
1. **A new event type `friction`, not a new channel.** Options: (a) a `friction` event in `event.v1`, sent with the
   others; (b) a separate endpoint. **(a):** the ledger, the push, retries, redaction, idempotency and the project check
   all exist; one type more in both copies of the schema (the parity test keeps them equal).
2. **Signatures carry no project content (privacy by construction).** `detail = {"kind": K, "subject": S}`, signature
   `K` or `K:S`. Kinds are a closed list: `permission_denied`, `gate_stop`, `fix_round`, `retry`, `usage_stop`,
   `refused_start`, `central_error`, `plan_changes`. Subjects come only from fixed vocabularies or tool names:
   - `permission_denied`: the tool name, and for Bash the executable's base name only (`Bash:sed`, `Bash:pnpm`; env
     assignments and `cd … &&` prefixes skipped; anything not matching `[A-Za-z0-9._+-]{1,40}` becomes `Bash:other`).
   - `gate_stop`: `docs`, `changelog`, `browser`, `journey`, `suite`, `stale`.
   - `fix_round`: `suite`, `journey`. `retry`: `exit_<code>`. `usage_stop`: `limit`, `budget`, `threshold`.
   - `refused_start`: `newer_installed`, or the preflight group that failed (`worktree`, `central`, `settings`, …; `other`).
   - `central_error`: `http_<status>:<route>` with numbers replaced by `N` (`http_409:/api/v1/tasks/N/heartbeat`), or
     `unreachable`. `plan_changes`: no subject (recorded by central when a plan is sent back).
   Both copies validate kind and subject (`^[A-Za-z0-9_.+:/-]{0,80}$`), so free text cannot ride along. Emitted at most
   once per signature per item per run, so a loop counts once.
3. **Counting happens at ingest, into two small tables**, not by scanning event bodies at page time:
   `friction_count` (signature, project, count, first/last seen) and `friction_signature` (signature, kind, subject,
   state `watching|drafted|muted|fixed`, linked task, who/when/why). One migration (`init-db`). A project can opt out
   (`project.share_frictions`, default on, owner toggle in Settings): its events are still stored as its own, but
   not counted in the shared tables.
4. **Drafting reuses the task flow instead of a new planning step.** Options: (a) a reflection Claude call that writes a
   plan; (b) central opens an *open* task with the evidence, and the normal flow plans it (the assignee's runner or
   the watcher's headless planner, with the spec check) and waits for approval. **(b):** deterministic, testable,
   no new cost or credentials on Vercel, and it gets the same planning rules, spec check and approval gate as any
   task. The evidence is counts, number of projects, first/last seen, and a pointer to the code area for the kind.
5. **Thresholds and caps (environment):** drafting is on only when `CENTRAL_REFLECTION_PROJECT` names the
   auto-runner's project. A signature is due at `CENTRAL_REFLECTION_MIN_COUNT` (default 3) occurrences or
   `CENTRAL_REFLECTION_MIN_PROJECTS` (default 2) projects, within 30 days; at most `CENTRAL_REFLECTION_DAILY` (default 3)
   drafts per UTC day; never while the signature already has an open task or is muted. Assignee:
   `CENTRAL_REFLECTION_ASSIGNEE` (an e-mail of a member), else the project's first owner.
6. **Closing the loop.** Task rejected → signature `muted` ("task #N rejected"). Task done → `fixed`; if it reaches the
   threshold again with events after the fix, a new task is drafted, titled as a regression and linking the earlier
   one. Admins can mute and unmute on the page (confirmed, audited).
7. **Approval stays with people** for now. When 0010's Supervisor exists it approves these through the decide API
   (#38) like any plan; nothing here changes for that. Recorded in decision record **0011**.

## Slices
1. **Notice and see:** friction events from the runner, counted by central, shown on a Frictions page.
2. **Draft:** a signature over the threshold opens a task on the auto-runner project.
3. **Close the loop:** reject mutes, done marks fixed, a recurrence drafts a regression task; admins mute and unmute.

## Journeys
- **J1 See where the runner stumbles.** Slice 1. Roles: developer, admin.
  1. As developer, run the runner on a project where the agent is refused `sed` on three items.
  2. As admin, open Frictions. *Expect:* a row `permission_denied:Bash:sed`, count 3, 1 project, first/last seen, state
     Watching; no file paths, commands' arguments or item text anywhere on the page.
- **J1b Opt out.** Slice 1. Roles: owner, admin.
  1. As owner of a project, switch off "Share runner frictions" in Settings. 2. Run a runner there that is refused `sed`.
  *Expect:* the Frictions counts do not change.
- **J2 A task is drafted.** Slice 2. Roles: admin, owner.
  1. With `CENTRAL_REFLECTION_PROJECT` set, let `permission_denied:Bash:sed` reach 3.
  *Expect:* the auto-runner project has an open task "Runner friction: permission_denied:Bash:sed" with the evidence;
  its assignee gets a notification; the Frictions row says Drafted with a link to the task.
- **J3 Reject mutes it.** Slice 3. Roles: owner.
  1. As owner, reject the drafted task (it is still open, before anyone plans it). 2. Let the signature occur 3 more times on a later day.
  *Expect:* the row says Muted ("task #N rejected"); no new task.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Sending frictions | `event.v1` ledger + `autodeliver.push` + ingest | free | n/a | reuse | transport, retries, redaction exist |
| Planning a fix | the task flow (headless planner, spec check, approval) | free | n/a | reuse | same rules as any task, no new cost |
| Telling the assignee | `notifications` / push | free | n/a | reuse | already sent for assigned tasks |

## End to end
- Data: two tables and one column (`init-db`, a person runs it). No new secrets; four optional environment settings.
- Security: subjects validated on both sides from fixed vocabularies; the Frictions page is admins only; mute and
  unmute are POST with CSRF, confirmed, audited; drafting is off unless configured.
- Docs: decision record 0011, `auto-runner/README.md`, `auto-runner/central/README.md`, `auto-runner/docs/central-guide.md`,
  root `CHANGELOG.md`; plugin released as the next minor version as earlier runner changes were.

## Queue items
- [ ] **R1 Decision record 0011 "Runner self-reflection".** Write `docs/decisions/0011-runner-self-reflection.md` (status accepted, with the owner's two answers) from this task's plan (the copy the runner adds under `docs/plans/`, sections Intent and Key decisions 1–7). Owner's answers to record: runs in other people's projects may send friction signatures to the auto-runner project; drafted tasks are approved by a person until the Supervisor (0010) exists, then by the Supervisor. Decisions in short: a `friction` event type in event.v1; signatures from eight closed kinds with fixed-vocabulary subjects and no project content; counting at ingest into shared tables with a per-project opt-out; drafting reuses the task flow (an open task with evidence, planned and approved as usual); thresholds 3 occurrences or 2 projects in 30 days, at most 3 drafts a day, configured by environment; reject mutes, done marks fixed, a recurrence drafts a regression task; approval stays with people. Link it from `docs/decisions/0010-supervisor.md` as the source of the Supervisor's "notice" work. No code.
  *Acceptance:* the record exists with Context, Decisions (the closed kinds and subject rules, counting, drafting, thresholds, closing the loop, approval), Consequences and Privacy sections; `python -m pytest -q` passes (doc-map check).
  *Slice:* 1 Notice and see
  *Kind:* internal
- [ ] **R2 `friction` event type on both sides.** In `auto-runner/bin/autoevents.py` add `friction` to `TYPES`, `FRICTION_KINDS` = (`permission_denied`, `gate_stop`, `fix_round`, `retry`, `usage_stop`, `refused_start`, `central_error`, `plan_changes`), a `friction_detail(kind, subject='')` helper that builds `{"kind", "subject"}`, and validation of a friction event's detail (its `item` may be null, for run-level frictions such as a refused start, or a non-empty item ID: keep `friction` out of `RUN_TYPES` and add this optional-item rule on both sides; kind in the list; subject matching `^[A-Za-z0-9_.+:/-]{0,80}$`; no other keys); add `signature(detail)` -> `kind` or `kind:subject`. Mirror the type, the same eight kinds and the validation in `auto-runner/central/ingest.py`. Update `auto-runner/README.md` (event types) and `auto-runner/central/README.md`.
  *Acceptance:* tests: valid friction events pass on both sides; a friction event with `item` null and one with an item both pass; subjects `exit_1` and `http_409:/api/v1/tasks/N/heartbeat` are accepted; an unknown kind, a subject with a space or over 80 characters, or an extra key is refused on both sides; the ingest parity test covers the new type and kinds; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R3 The runner records friction during a run.** In `auto-runner/bin/auto_run.py` add one helper that emits a `friction` event at most once per signature per item per run, and use it for: each permission denial (`permission_denied`, subject from a new `friction_subject(tool_call)` in `autoevents.py`: the tool name, or `Bash:<executable base name>` skipping env assignments and `cd … &&` prefixes, else `Bash:other`); docs-gate, changelog, browser, journey, suite and stale stops (`gate_stop`); suite and journey fix rounds (`fix_round`); early-exit retries (`retry`, `exit_<code>`); usage-limit, budget and plan-usage-threshold stops (`usage_stop`: `limit`, `budget`, `threshold`). Update `auto-runner/README.md`.
  *Acceptance:* tests in `tests/auto_runner/` with a fake claude: refusals of `sed -i x` on two items emit two `permission_denied:Bash:sed` events (one per item; repeated refusals in one item emit one; none contains the argument); `FOO=1 cd /a/b && pnpm i` gives `Bash:pnpm` and `"$(x)"` gives `Bash:other`; a docs-gate stop emits `gate_stop:docs`; a fix round emits `fix_round:suite`; an early exit with 1 emits `retry:exit_1`; a usage stop emits `usage_stop:limit`; no friction detail contains a path of the test project; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R4 Refused starts are recorded.** In `auto-runner/bin/autosafe.py` add `preflight_groups(...)` (same arguments as `preflight`) returning `[(group, message)]`, with a group code per check (`worktree`, `runner_name`, `model`, `base_url`, `journey`, `central`, `env_file`, `path_prepend`, `publish`, `principles`, `capability`, `no_commit`, `no_tests`, `settings`); `preflight()` keeps returning the same list of message strings, built from it. In `auto_run.run`, before raising `RunnerError` for a newer installed version or a preflight refusal, write `refused_start` (`newer_installed`, or the first failed group) to the ledger with `item` null, so the next push sends it. Update `auto-runner/README.md`.
  *Acceptance:* tests: a newer installed version writes `refused_start:newer_installed` to `.auto/events.jsonl` and the run still refuses with today's message; a preflight refusal for a missing test command writes `refused_start:no_tests`; `preflight_groups` names the group of each problem; existing preflight tests pass unchanged; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R5 Central errors are recorded; plugin release.** Give `autotasks.beat`, `finish` and `wait_note` an optional `on_error(status, route)` callback (status an HTTP code or None when central was unreachable; route the request path with digits replaced by `N` and no query string), called on every failed call while their return values stay as today. In `auto_run.run` pass a callback that emits `central_error` (`http_<status>:<route>`, or `unreachable`), and emit the same for `push()` errors and skipped events: `autodeliver.push` also returns `skipped_status` (the refusal's HTTP status, one per push call) and `error_status` (the HTTP status, or None when the service was unreachable), next to today's keys, and `auto_run.push()` uses them. A 409 on a heartbeat (the claim was lost: released, reassigned or expired) counts too, as the task asks for 409s; a reassignment done on purpose is muted from the page if it ever drafts a task. Add `POST /api/v1/tasks/N/heartbeat` to `tests/auto_runner/fake_central.py`, answering 409 when the claim does not match, as the service does. Release the runner changes of R2–R5 as the next minor plugin version (plugin.json, marketplace entry, CHANGELOG) and update `auto-runner/README.md`.
  *Acceptance:* tests: `beat` with a failing transport calls `on_error` with the status and normalised route and still returns `lost`/`unavailable` as today; a heartbeat with a stale claim gets 409 from the new `fake_central.py` route and emits `central_error:http_409:/api/v1/tasks/N/heartbeat`; an unreachable central emits `central_error:unreachable`; an event refused with 400 gives `central_error:http_400:/api/v1/events`; a push that cannot reach the service gives `central_error:unreachable`; `autodeliver.push` keeps its existing keys and tests; the version is the same in plugin.json and the marketplace entry; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R6 Central counts frictions.** Add tables `friction_count` (signature, project_id, count, first_seen, last_seen; key signature+project), `friction_day` (signature, project_id, day as UTC date text, count; key all three, for the 30-day window and after-fix counts) and `friction_signature` (signature key, kind, subject, state `watching|drafted|muted|fixed`, task_project_id, task_number, previous_task_number, changed_by, changed_at, reason) to `schema.sql`/`db.TABLES`, and `project.share_frictions` (default true) via `db.COLUMNS_ADDED`. In `ingest.store`, for each newly stored `friction` event of a sharing project, upsert `friction_count`, `friction_day` and `friction_signature` (duplicates change nothing). In `tasks.decide`, a "changes" decision records `plan_changes` the same way. Owners switch "Share runner frictions with the auto-runner project" on Settings › Repository (POST, CSRF, audited). Update `auto-runner/central/README.md` (tables, migration note).
  *Acceptance:* `tests/central/test_frictions.py`: three friction events from one project make count 3 with first/last seen and a `friction_day` row of 3; the same event id twice counts once; two projects make two count rows and one signature row (watching); an opted-out project's events are stored as events but not counted; a "changes" decision counts `plan_changes`; the Share setting defaults to on, an owner switching it off writes an audit row, and a manager, a non-member and a POST without the CSRF token are refused; `test_skeleton`'s table count is updated; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R7 Frictions page.** `GET /frictions` (admins only; others 404) in a new `auto-runner/central/frictions.py`: one row per signature with kind, subject, total count, number of projects (project names on hover), first and last seen (the `when` macro of `templates/_ui.html`, imported as `ui`), state chip and linked task; filter chips by kind and state; newest last-seen first; linked from Accounts' header as "Runner frictions". Admins can Mute (with a reason) and Unmute a row (Unmute sets `watching`, clears the reason and sets `changed_at`, so only occurrences after it count toward drafting, the same rule as `fixed`): POST with CSRF, `data-confirm`, audited `friction.mute` / `friction.unmute`. Uses existing `_ui.html` macros and styles; works at 390 px.
  *Acceptance:* tests: an admin sees rows with counts and states; a non-admin gets 404; mute and unmute change the state and write audit rows; Unmute sets state `watching`, clears the reason and sets `changed_at`; mute without confirm=1 renders the confirm page; filters work; the template scan finds no inline script or style; `python -m pytest -q` passes.
  *Slice:* 1 Notice and see
  *Kind:* feature
- [ ] **R8 Draft a task when a signature keeps coming back.** In `frictions.py` add `draft_due(db, now)` called at the end of ingest (and after `plan_changes` is counted): when `CENTRAL_REFLECTION_PROJECT` names a project, each `watching` (or `fixed`, see R9) signature with ≥ `CENTRAL_REFLECTION_MIN_COUNT` (default 3) occurrences or ≥ `CENTRAL_REFLECTION_MIN_PROJECTS` (default 2) projects in the last 30 days (summed from `friction_day`; for a signature with a `changed_at` (unmuted or fixed), only rows with day > the UTC date of `changed_at` count, so the change day itself is left out), under `CENTRAL_REFLECTION_DAILY` (default 3) drafts that UTC day, gets an open task via `tasks.create` in that project: title "Runner friction: <signature>", description with the counts, project count, first/last seen and the code area for its kind (a fixed map, e.g. `permission_denied` → `.claude/settings.json` allow list and `auto-runner/bin/autohook.py`), assignee `CENTRAL_REFLECTION_ASSIGNEE` (an account e-mail) if that account is a member, else the project's first owner (the owner `member` row with the earliest `created_at`, ties broken by account id), with `by` = that same assignee (no person is acting; the audit row says `"via": "reflection"`); the assignee is notified with the `assigned` kind through `notifications.notify` directly (not `tasks.notify_assigned`, which assumes someone else assigned it); state `drafted` with the task linked; audit `friction.draft`. Settings read in `config.py`; the Frictions page says "Drafting is off: set CENTRAL_REFLECTION_PROJECT" when it is unset. Update `auto-runner/central/README.md` (settings) and `auto-runner/docs/central-guide.md`.
  *Acceptance:* tests with the settings in app config: the third event creates exactly one open task with the evidence and a notification to the assignee; the fourth creates none (already drafted); two projects reach it at count 2; the daily cap stops the fourth draft that day and allows it the next day (injected clock); without `CENTRAL_REFLECTION_PROJECT` nothing is drafted; a muted signature is never drafted; an unmuted signature with old occurrences over the threshold is not drafted until new ones, sent on a later UTC day with the injected clock, reach it; `python -m pytest -q` passes.
  *Slice:* 2 Draft
  *Kind:* feature
- [ ] **R9 Close the loop.** When a task linked to a signature is rejected, through `tasks.decide` (reject, from plan review) or `tasks.close` (an open task), the signature becomes `muted` with reason "task #N rejected"; when `tasks.report` records result `done`, it becomes `fixed` (changed_at = then). A `fixed` signature counts only `friction_day` rows after its fix day toward the threshold (the rule in R8); reaching it again drafts a new task titled "Runner friction came back: <signature>" that links the earlier task (its number moves to `previous_task_number` before the new one is linked). The Frictions page shows Fixed and the regression link. Update `auto-runner/central/README.md`, `CHANGELOG.md`.
  *Acceptance:* tests: rejecting the drafted task mutes the signature and later events draft nothing; a done task marks it fixed; three new events on a later UTC day after the fix (injected clock) draft a regression task linking the first; events from before the fix do not count; rejecting the drafted task while it is still open (before any plan) mutes it too; an admin's `GET /frictions` then shows the Fixed or Drafted chip with links to both tasks; `python -m pytest -q` passes.
  *Slice:* 3 Close the loop
  *Kind:* feature

## Spec check
Seven rounds; every finding fixed (item sizes, the friction item rule, counting windows by day, error statuses carried
out of `autotasks` and `autodeliver`, the drafting account, unmute and fixed behaviour). Last run: no findings.

## Needs a person
- Run `python -m central init-db` after R6 is deployed (three tables, one column).
- Set `CENTRAL_REFLECTION_PROJECT=flexsin-claude-plugins` (and optionally the assignee, thresholds and daily cap) in
  Vercel to switch drafting on after R8 ships.
- Approve or reject drafted tasks until the Supervisor (0010) exists.
