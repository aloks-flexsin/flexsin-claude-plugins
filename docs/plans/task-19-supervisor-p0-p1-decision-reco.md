## Intent

**Outcome.** A Supervisor pass reads central (tasks, runs, runners, blockers, PRs) and does the routine work a person does today. It approves plans that pass policy, merges PRs that pass the merge gate, orders the queue, and pauses, resumes and wakes runners. It never writes code or pushes to a branch. Every decision goes back to central as an audit row with its reason.

**For whom and how success shows.** The people assigned tasks stop being the bridge for plan approval and PR merges. On a test project, one pass approves a policy-passing plan and merges a green PR, with one audit row per decision.

**Out of scope here:**
- P2–P5: blocker answers, independent review, UAT delivery, Supervisors talking to each other.
- Restart and reassign of stuck runners.
- Usage-limit rescheduling.
- Adding traced follow-up tasks.
- Showing "Queued" on the page.
- Launching a dead watcher process.

**Wording versus intent.** The task says "approve plans that pass policy". I read that as deterministic gates only, with no model judgement. 0010 says the model proposes and code enforces, and the model's part comes in P2/P3. The gates have to be right first.

**Plan sent back.** This revision answers the reviewer's findings. The changes:
- The supervisor's project role and the places that use `accounts.ROLES` are now explicit.
- The `merged_at` column and the whoami route move to S2.
- The revise-round count is derived from audit rows.
- `restricted_paths` and `base` move to S3.
- Every item that touches central or `bin/` updates its README in the same commit.
- S7 shows "Merged" on the pages.
- The priority and dependency rules are written out.
- S11 names its exact tests.

**Not verified.** I could not run shell commands while planning. I did not read `accounts.py`, `autowatch.py`, `autospec.py`, `.claude/auto.json` or the schema migration mechanism. Anything that depends on them is marked "verify". The reviewer's findings are taken as true for `accounts.ROLES`, `projects.MEMBER_ROLES`, `tasks.can_decide`, `tasks._can_run`, `tasks.create`, `invites.py`, `admin.py`, `tokens.py` and `docs/doc-map.json`.

## Key decisions

| # | Decision | Options | Choice and why |
|---|---|---|---|
| 1 | Supervisor identity | (a) a new project member role; (b) an account-level role `supervisor`, as `admin` is today; (c) a flag on an ordinary account | **(b).** `accounts.ROLES` becomes `('admin','user','supervisor')`. The supervisor is added to each project as a **`manager`** member, which satisfies `tasks.can_decide` (rank ≥ `APPROVER_ROLE`) and gives audit rows an `account_id`. Manager rank is more than the pass needs, so the supervisor's own routes check the account role, and the pass only calls `/api/v1/supervisor/*`. A manager member would pass `tasks._can_run`, so `tasks._can_run` and `tasks.create` explicitly reject an account whose role is `supervisor`. It can never be an assignee or create tasks. **Other users of `accounts.ROLES`:** `invites.py` must not offer or accept `supervisor` (it is made only by the CLI). `admin.py` must show the role but refuse role changes to or from `supervisor`. Verify each against the code and test it. |
| 2 | How it talks to central | New machine-token scope vs. the personal token it already has | **A personal token** from the existing device sign-in, with new routes under `/api/v1/supervisor/…` that require `account.role == supervisor`. This adds no new secret type. A new `GET /api/v1/supervisor/whoami` returns the caller's account role, so the command can tell "not a supervisor" from "unknown project". Verify how `tokens.bearer_token` exposes the account. |
| 3 | Pass is deterministic code | LLM pass vs. code | **Code only** (`bin/autosupervisor.py`, `bin/autogates.py`). It needs no Claude account yet. The model's judgement is added in P2 and can only propose. |
| 4 | Where policy lives | In the PR branch vs. on the base | **`.claude/supervisor-policy.json`, read from `origin/main` (the base), never from the PR branch.** The policy file is itself a restricted path. Otherwise a runner could widen the policy in the PR it asks the Supervisor to merge. The audit row stores the policy's blob SHA. A missing policy file means every plan is left for a person. |
| 5 | Plan gate | Trust the plan's prose vs. parse it | The gate reads structure. It passes only when all of these hold: (1) the spec check is clean, via `autospec.py` (verify its interface); (2) **Needs a person** is exactly "None."; (3) every item parses with `runs.parse_items` and has `*Acceptance:*`; (4) no backticked path in the plan or items matches a restricted glob; (5) the task is in `plan_review`. On failure it requests changes and lists the failed checks. **Round cap:** the round count is the number of `supervisor.plan.changes` audit rows for the task, with no new column. The route computes it server-side. With 0 or 1 such rows, a failing plan gets `changes`. With 2 or more, the route returns `escalate`, writes `supervisor.plan.escalate` and leaves the task in plan review for a person. So the third failing plan escalates. |
| 6 | Merge gate | One check vs. all of 0010's | **All of decision 4, as one pure function.** A PR is merged only if: CI is green on the head SHA; an approving review exists from a non-author; no changed file matches a restricted path; the PR author differs from the merging identity; the base is the policy base; and the PR is registered as `pr_url` on a central task in `done` state. The merge call passes `sha=<evaluated head>` so a push after the check cannot slip through. Gates read GitHub state, never PR text. |
| 7 | "Review approved" before P3 | Supervisor reviews its own gate vs. a person's review vs. none | **A GitHub APPROVED review from any non-author is required by default.** Policy key `merge.review: "required" \| "none"` lets a project opt into CI-only merging. That opt-in is a question under Needs a person. The Supervisor's own independent review arrives in P3. |
| 8 | Talking to GitHub | PyGithub, `gh` CLI, or `urllib` | **`urllib`**, because the repo is standard-library only. It sits behind a small `Forge` interface with no push or commit method, and a test asserts that. Tests use `FakeForge` and never touch the network. The token comes from `AUTO_SUPERVISOR_GITHUB_TOKEN` and is never logged. |
| 9 | Audit | New decision table vs. the existing `audit` table | **The existing `audit.record`.** Actions are `supervisor.plan.approve`, `.plan.changes`, `.plan.escalate`, `.merge.decided`, `.merge.done`, `.merge.failed`, `.merge.refused`, `.order`, `.runner.pause`, `.runner.resume`, `.runner.wake`. `detail` holds the reason, the evidence, the failed checks and the policy SHA. The `decided` row is written before the merge call, and the outcome row after it. |
| 10 | Queue order and fields | Supervisor sets claim order vs. server-side order | **Server-side order in `tasks.take_next`.** Three new task fields: **`priority`** is an integer set by people, where lower is more urgent (1 is highest) and NULL sorts last. **`rank`** is an integer set only by the Supervisor, lower first, NULL last. **`depends_on`** is a list of task numbers in the same project, given on create. Sort order: `priority` asc (NULL last), then `rank` asc (NULL last), then `number`, inside the existing status order. A person's priority always wins, and the Supervisor only reorders within a priority level. **Unfinished dependency:** `take_next` skips a task while any task in its `depends_on` is not `done` (verify the status name). Create rejects numbers that do not exist in the project, or that point at the task itself. Dependencies can only point at existing tasks, so cycles cannot be made. Known limit: `done` means the runner finished, not that the PR is merged. A dependent task can start before the merge, and the Supervisor ranks the merge of a depended-on task first. Stricter "merged" semantics are a later option. |
| 11 | Runner control | Reuse `control` vs. a new mechanism | **Reuse `runners.set_control`** (it already records `control_by`). `can_control` also allows a supervisor account. A new value `wake` makes an idle watcher poll at once. The Supervisor resumes only runners it paused itself (`control_by` is its account), so a person's pause is never undone. It pauses all of a project's runners only when CI on the base head is red, and resumes them when it is green. A pause ends after the current item, as 0010 requires. |
| 12 | "Start" | Launch processes remotely vs. wake an idle watcher | **Wake an idle watcher** (0009's watch loop already runs as a service). Central cannot start a dead process, so a dead watcher shows as silent and is flagged for a person. A launcher agent per machine is P5. |
| 13 | Docs and doc-map | Do all docs at the end vs. with each change | **With each change.** The runner's docs gate checks each item's own commits and stops the run with reason `docs`. So every item that changes `central/**` updates `central/README.md`, and every item that changes `bin/*.py` or `commands/` updates `auto-runner/README.md`, in the same commit. Each new route string is written in the central README, because the doc-map's `route` pattern requires it. S13 only reviews the docs and the decision record. |

**Left out on purpose:** the Queued label, restart and reassign, follow-up tasks, and cost and usage enforcement (no per-task spend exists yet). The policy file has no keys for features that do not exist yet.

**Real-use coverage:**
- **Data and migrations.** The new columns need a central schema migration: `merged_at` (S2), then `priority`, `depends_on` and `rank` (S9). All are nullable, so they are safe to add and roll back, and nothing reads them if the Supervisor is off. Applying the migration to Supabase is a person's job, since CLAUDE.md forbids touching Supabase. Each item's README note states the steps and the rollback.
- **Security.** No production or deploy credentials exist where the pass runs. The Supervisor holds no push rights, only merge and review rights, and branch protection is the final guard. A supervisor account cannot be created by invite or by the admin page. It is created only by CLI.
- **Errors.** A GitHub or central failure aborts that one decision and writes `*.failed`. The next pass retries, and decisions are idempotent through the audit rows plus the task state.
- **Configuration.** Environment variables only: `AUTO_SUPERVISOR_GITHUB_TOKEN` plus the existing central URL and token.
- **Monitoring.** Each pass ends with a one-line summary of decisions made, and the audit rows are the history.

## Slices

**Slice 1: Plan approval by policy.**
- Once done, an admin can create the supervisor account and a person can give it a token. One pass then approves a policy-passing plan and sends a failing one back with each failed check named.
- After two send-backs it escalates to a person.
- A task stops waiting on a person for plan approval, and a person can still override on the central page.

**Slice 2: Merge green PRs.**
- Once done, the same pass merges a task's PR when every merge gate passes, and refuses it with a recorded reason otherwise.
- The task page and list show "Merged <time>".
- This meets the Done-when.

**Slice 3: Queue order and runner control.**
- Once done, tasks can carry priority and dependencies, and the runner is offered them in that order.
- The Supervisor ranks the queue, with a person's priority winning.
- It pauses runners while `main` CI is red and resumes only the runners it paused. It wakes an idle watcher when a task is ready.

## Journeys

- **J1 Passing plan is approved.** Slice 1. Roles: developer, Supervisor.
  1. As developer, a runner posts a plan whose "Needs a person" is "None.", with valid items.
  2. Run one Supervisor pass.
  3. As developer, open the task page.
  *Expect:* the task is Approved, decided by the Supervisor account, and the audit shows `supervisor.plan.approve` with the policy SHA.

- **J2 Failing plan is sent back, then escalated.** Slice 1. Roles: developer, Supervisor, manager.
  1. Post a plan that names a path under the restricted globs.
  2. Run a pass. *Expect:* the task goes to "changes requested" with the failed check listed (one `changes` row).
  3. Post a still-failing plan and run a pass. *Expect:* "changes requested" again (two `changes` rows).
  4. Post a third still-failing plan and run a pass.
  *Expect:* the task stays in plan review with a `supervisor.plan.escalate` row. A manager sees it under "Needs approval", and a further pass does nothing to it.

- **J3 Green PR is merged.** Slice 2. Roles: developer, Supervisor, reviewer.
  1. On the test project, a task is done with a PR from the runner identity.
  2. CI is green on the head SHA and the reviewer has approved.
  3. Run a pass.
  4. As developer, open the task page and the task list.
  *Expect:* the forge records a merge at that exact SHA. Both pages show "Merged <time>". Audit rows `supervisor.merge.decided` and `.done` carry the evidence.

- **J4 Unsafe PRs are refused.** Slice 2. Roles: Supervisor.
  1. Run a pass over four PRs. The first has red CI. The second touches a restricted path. The third has no approval. The fourth is authored by the Supervisor's own identity.
  *Expect:* none is merged, and each has a `supervisor.merge.refused` row naming its reason.

- **J5 Done-when, one pass.** Slice 2. Roles: Supervisor.
  1. On the test project, one task has a passing plan and another has a green, approved PR.
  2. Run a single pass.
  *Expect:* the plan is approved and the PR is merged, with one audit row per decision (`supervisor.plan.approve`, and `supervisor.merge.decided` followed by `.done`).

- **J6 Queue order, and a person's priority wins.** Slice 3. Roles: developer, Supervisor.
  1. Create tasks #1 A (depends on #3), #2 B (priority 1), #3 C, #4 D, with no other priorities.
  2. Run a pass, then claim with the runner until the queue is empty. Mark each claimed task done before the next claim.
  *Expect:* the runner is offered B first (priority wins). It is then offered C, because the Supervisor ranks it first as it unblocks A. A is never offered while C is unfinished. After C is done it is offered A, then D. A `supervisor.order` row explains each rank.

- **J7 Pause when main is red.** Slice 3. Roles: developer, Supervisor.
  1. Two runners are watching, and the developer paused one of them.
  2. CI on the base head goes red. Run a pass, then the runners beat. *Expect:* the other runner pauses, and the Runs page says "Paused by the Supervisor" for it.
  3. CI goes green. Run a pass, then the runners beat.
  *Expect:* the runner the Supervisor paused resumes. The developer's paused runner stays paused. When a task is ready and a watcher is idle, the beat returns `wake`.

## Reuse check

**Reused from the project:**
- `audit.record`
- `tasks.decide`, `tasks.can_decide`, `tasks.take_next` and `tasks.report` (which stores `pr_url`)
- `runners.set_control` and `runners.can_control`
- `runs.parse_items`
- `autospec.py` for the spec check (verify its interface)
- `tokens.bearer_token`
- the Flask test client used in `tests/central`
- `tests/auto_runner/fake_central.py` and the pytest conftest

**New packages:** none. The standard library is required by CLAUDE.md for `bin/`.

| Requirement | Existing options | Cost | Licence | Recommendation and why |
|---|---|---|---|---|
| GitHub PR, checks, review and merge calls | `urllib` against the [REST API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request) | Free (API rate limits apply) | n/a | **Build** a small adapter of five calls, because the repo allows standard library only. |
| | [PyGithub](https://github.com/PyGithub/PyGithub) | Free | LGPL-3.0 (unverified) | **Reject.** It adds a dependency, and it is copyleft, so it would be a question for a person. |
| | [`gh` CLI](https://github.com/cli/cli) | Free | MIT (unverified) | **Reject for now.** It must be installed and signed in on the host, and it is harder to fake in tests. |
| Merge automation | GitHub native auto-merge (needs branch protection; plan limits unverified) | Unverified | n/a | **Reject.** It gives no per-decision policy checks or audit rows in central. Branch protection stays on as the enforcement layer. |

## Queue items

Test layout: central tests go in `tests/central/`. Runner and gate tests go in `tests/auto_runner/`. The end-to-end tests drive the pass's logic through the real Flask test client with `FakeForge` and an injected HTTP function, and live in `tests/central/test_supervisor_e2e.py`. Item IDs use the letter S. Verify that S and S1–S13 are unused in the queue file and `.auto/pending/`. If they are used, take the next free letter.

- [ ] **S1 Supervisor account role.** Add account role `supervisor` (`accounts.ROLES` becomes `('admin','user','supervisor')`) and a CLI command to create one (verify `accounts.py` and `cli.py`: `python -m central supervisor-create <email>`). It can be added to a project as a **manager** member. `tasks._can_run` and `tasks.create` reject an account with role `supervisor`, so it is never an assignee and never creates tasks. `invites.py` must not offer or accept `supervisor`. `admin.py` shows the role but refuses role changes to or from `supervisor`. Existing roles and routes must not change. Update `auto-runner/central/README.md` in the same commit (the role, the CLI command and the places above).
  *Acceptance:* `tests/central/test_supervisor_identity.py` passes. A supervisor who is a manager member is refused as an assignee (`_can_run`), refused task creation, allowed to decide a plan, cannot be invited, and cannot be set to or from `supervisor` on the admin page.
  *Slice:* 1 Plan approval by policy
  *Kind:* feature

- [ ] **S2 Supervisor read API, whoami and `merged_at`.** Add `GET /api/v1/supervisor/whoami` (returns the caller's account role, 200 for any valid token). Add `GET /api/v1/supervisor/state?project=<slug>` (supervisor account role required, 403 for any other role, 404 only for an unknown project). It returns the project's tasks with plan, items, status, branch, `pr_url`, `plan_rounds` (the number of `supervisor.plan.changes` audit rows for the task) and `merged_at`, plus runners and open blockers, using the existing row shapes. It is read-only, and no claim ids appear. Add the nullable `merged_at` column to `project_task` here (nothing writes it yet), with the migration note. Update `auto-runner/central/README.md` in the same commit (both route strings, the column, the migration and rollback).
  *Acceptance:* `tests/central/test_supervisor_api.py::test_state_is_read_only_and_supervisor_only` passes for a supervisor, a developer, an admin and no token. In the same file, `::test_whoami_returns_role` and `::test_state_plan_rounds_counts_changes_rows` pass.
  *Slice:* 1 Plan approval by policy
  *Kind:* feature

- [ ] **S3 Policy and plan gate.** Add `bin/autogates.py` with the policy loader (`.claude/supervisor-policy.json`, read from the base ref through an injected reader, returning the blob SHA). The policy keys here are `base` and `restricted_paths`. The loader validates the keys. Add the pure `plan_gate(task, policy)` function covering the five checks in decision 5. A missing or invalid policy file means the plan is left for a person. Update `auto-runner/README.md` in the same commit (the policy file, its keys, the fact that it is read from the base).
  *Acceptance:* `tests/auto_runner/test_supervisor_gates.py` passes, including a plan naming a restricted path, "Needs a person" not "None.", an item with no Acceptance line, a missing policy file, and an all-clear case.
  *Slice:* 1 Plan approval by policy
  *Kind:* feature

- [ ] **S4 Plan decision route and first pass.** Add `POST /api/v1/supervisor/tasks/<n>/plan-decision` (approve, changes or escalate). It calls `tasks.decide` and writes the audit rows with the policy SHA and reasons. It derives the round count from the `supervisor.plan.changes` audit rows for the task and converts a `changes` request into `escalate` when that count is already 2, with no new column. Add `bin/autosupervisor.py` with a plan step that uses S2, S3 and this route through an injected HTTP function. Re-running a pass must not duplicate decisions. Update both READMEs in the same commit (the route string, the plan step, the round cap).
  *Acceptance:* `tests/central/test_supervisor_e2e.py::test_plan_approved_or_sent_back` passes (J1 and J2). In `tests/central/test_supervisor_api.py`, `::test_third_failing_plan_escalates` passes (the third failing plan returns escalate and writes `supervisor.plan.escalate`). `::test_rerun_does_not_duplicate_decision` passes.
  *Slice:* 1 Plan approval by policy
  *Kind:* feature

- [ ] **S5 Forge adapter.** Add `bin/autoforge.py` with a `Forge` interface (`pr`, `changed_files`, `checks`, `reviews`, `merge(pr, sha, method)`), a `GithubForge` over `urllib` (token from `AUTO_SUPERVISOR_GITHUB_TOKEN`, never logged, injectable opener), and a `FakeForge` in `tests/auto_runner/`. The interface has no push or commit method. Update `auto-runner/README.md` in the same commit.
  *Acceptance:* `tests/auto_runner/test_autoforge.py` passes using a recorded fake opener only (no network), and includes a test that the interface exposes no write-to-branch method and one that the token never appears in a logged line.
  *Slice:* 2 Merge green PRs
  *Kind:* internal

- [ ] **S6 Merge gate.** Add the pure `merge_gate(task, pr, checks, reviews, policy, merger)` to `autogates.py` with the six conditions from decision 6. It returns a verdict plus the evidence and every reason it failed. `base` and `restricted_paths` already exist from S3, so this item adds only the policy keys `merge.review` and `merge.method`. Update `auto-runner/README.md` in the same commit.
  *Acceptance:* `tests/auto_runner/test_supervisor_gates.py::test_merge_gate_table` passes with one case per refusal reason, plus an all-clear case.
  *Slice:* 2 Merge green PRs
  *Kind:* feature

- [ ] **S7 Merge step, record route and Merged display.** Add `POST /api/v1/supervisor/tasks/<n>/merge` to write `merged_at` (the column exists from S2) plus the audit rows (`decided` before the forge call, `done` or `failed` after, and `refused` with the reasons). Add the merge step to the pass: it merges at the evaluated SHA and skips a task already merged. The task page and the task list show "Merged <time>" when `merged_at` is set. Update both READMEs in the same commit (the route string and the merge step).
  *Acceptance:* `tests/central/test_supervisor_e2e.py::test_done_when_one_pass` (J5) and `::test_unsafe_prs_refused` (J4) pass. `tests/central/test_tasks_pages.py::test_merged_task_shows_merged_time` passes (task page and task list, J3).
  *Slice:* 2 Merge green PRs
  *Kind:* feature

- [ ] **S8 Supervisor command.** Add `commands/supervisor.md` (`/auto-runner:supervisor`, run one pass and print a summary) and the `autosupervisor.py` entry point with its environment variables and exit codes. It calls `GET /api/v1/supervisor/whoami` first and refuses to run, with a clear message and a non-zero exit code, if the role is not `supervisor`. Update `auto-runner/README.md` in the same commit.
  *Acceptance:* `tests/auto_runner/test_supervisor_pass.py::test_command_refuses_non_supervisor_token` passes, and the doc-map check passes.
  *Slice:* 2 Merge green PRs
  *Kind:* feature

- [ ] **S9 Task priority and dependencies.** Add the nullable `priority`, `depends_on` and `rank` columns, with the migration note. `priority` and `depends_on` are optional on task create (form and API). Rules: `priority` is an integer, lower is more urgent, and NULL sorts last. `depends_on` is a list of task numbers in the same project. Create rejects numbers that do not exist, or that equal the task's own number. `take_next` orders by priority, then rank, then number, within the existing status order, and skips a task while any task in its `depends_on` is not `done`. Existing behaviour is unchanged when all are unset. Update `auto-runner/central/README.md` in the same commit (the fields, the rules, the migration and rollback).
  *Acceptance:* in `tests/central/test_tasks.py` (verify the file name): `::test_take_next_priority_lower_first_null_last`, `::test_take_next_rank_within_priority`, `::test_take_next_skips_unfinished_dependency`, and `::test_create_rejects_unknown_or_self_dependency` pass, and the existing task tests pass unchanged.
  *Slice:* 3 Queue order and runner control
  *Kind:* feature

- [ ] **S10 Queue order step.** Add the pure `order(tasks)` (a task that unblocks others first, then unblock count, then age, never crossing a person's priority) in `autogates.py`, and `PUT /api/v1/supervisor/order`. The pass writes ranks only when they change, with a `supervisor.order` audit row giving the reason for each. Update both READMEs in the same commit (the route string and the order rules).
  *Acceptance:* `tests/central/test_supervisor_e2e.py::test_queue_order_j6` passes. `tests/auto_runner/test_supervisor_gates.py::test_order_never_crosses_priority` passes.
  *Slice:* 3 Queue order and runner control
  *Kind:* feature

- [ ] **S11 Supervisor runner control.** `runners.can_control` also allows the supervisor account. Add `POST /api/v1/supervisor/runners/<id>/<pause|resume|wake>` with audit rows. Resume is allowed only when `control_by` is the supervisor. `wake` is a new `control` value returned by the beat. The Runs and Overview pages show "Paused by the Supervisor". Pause behaviour for people is unchanged. Update `auto-runner/central/README.md` in the same commit (the route strings, `wake`, the page text).
  *Acceptance:* these exact tests pass: `tests/central/test_supervisor_api.py::test_supervisor_cannot_resume_a_persons_pause`; `tests/central/test_runners.py::test_beat_returns_wake`; `tests/central/test_pages.py::test_runs_page_shows_paused_by_supervisor` (verify the pages test file name).
  *Slice:* 3 Queue order and runner control
  *Kind:* feature

- [ ] **S12 Pause, resume and wake step.** The pass pauses a project's runners when CI on the base head is red and resumes the ones it paused when it is green. It also wakes an idle watcher when a task is ready. Make `autowatch.py` poll at once on `wake` (verify its beat handling). A watcher that is silent is listed in the summary and is not touched. Update `auto-runner/README.md` in the same commit.
  *Acceptance:* `tests/central/test_supervisor_e2e.py::test_pause_when_main_red_j7` passes, and `tests/auto_runner/test_autowatch.py::test_wake_polls_at_once` passes.
  *Slice:* 3 Queue order and runner control
  *Kind:* feature

- [ ] **S13 Docs review.** Review that both READMEs already cover every route, column, environment variable, the policy file and the migration steps with rollback, and fix any gap. Update `docs/decisions/0010-supervisor.md`: record P0–P1b progress, the four questions with the recommended answers, and the note that policy is read from the base. Update `auto-runner/docs/delivery-contract.md` only if a contract changed. Do not edit `CLAUDE.md`, because 0010 says its rule changes wait for acceptance.
  *Acceptance:* `python -m pytest -q` is green, including the doc-map check.
  *Slice:* 3 Queue order and runner control
  *Kind:* internal

## Needs a person

**0010's four questions.** I settled each with a recommendation and an assumption, so the items above can run.

1. **UAT.**
   - Nothing in the repo shows a UAT environment, and CI deploys are unverified.
   - Recommendation: a separate Vercel project and Supabase database for UAT, a mail sink, and CI deploying `main` to UAT only. UAT-only credentials go in a GitHub Actions environment, never on the Supervisor's host.
   - This task does not need UAT, because tests use fakes. I assumed creating UAT is yours (CLAUDE.md forbids agents touching Vercel or Supabase) and belongs to a later phase.
2. **Branch model.**
   - Recommendation: confirm `main` as the integration branch and UAT source, with production promoted by you from a tag.
   - Assumed in the policy `base: "main"`.
3. **Host and identity.**
   - Recommendation: for the pilot, run `/auto-runner:supervisor` every 10–15 minutes from Windows Task Scheduler or a Desktop scheduled task on one named machine you pick.
   - Create the central supervisor account with `supervisor-create` (you give the email), add it to each project as a manager, and issue its token. For GitHub, create a dedicated machine user with a fine-grained token for this repository only (pull requests and checks read, merge, no push). A GitHub App is a later option.
   - No Claude account is needed until P2. Create the accounts and tokens yourself, since I cannot handle secrets.
4. **Limits.**
   - Recommendation: stop-and-ask above 5 USD per task, on top of 0009's 20 USD a day per runner.
   - Licence allowlist: MIT, BSD-2/3-Clause, Apache-2.0 and ISC.
   - Neither is enforced in this task (no per-task spend and no dependency gate exist yet), so I added no dead config keys. They belong to the later cost and dependency work.

**Other decisions and actions for you:**
- **Accept 0010.** Its status is "proposed". Accepting it triggers the `CLAUDE.md` rule changes ("an identity never merges a PR it opened"; "only CI deploys, only `main` to UAT"), which this task deliberately leaves alone.
- **Branch protection on `main`.** Turn it on (CI green plus an approving review). The gate relies on it as the final guard.
- **CI-only merging.** Decide whether any project may set `merge.review: "none"`. I recommend no until P3's independent review exists. The default is a required approval from a non-author.
- **Dependency meaning.** I assumed a dependency is finished when the task is `done`, not when its PR is merged (decision 10). Say if you want "merged" instead. That would block dependents in projects without a Supervisor.
- **Manager rank for the supervisor.** It is needed so the supervisor can decide plans, and it also allows manager actions in the central UI. I assumed this is acceptable for the pilot, with the pass using only `/api/v1/supervisor/*`. Say if you want a narrower role later.
- **Apply the central schema migrations to Supabase** (`merged_at` from S2, and `priority`, `depends_on` and `rank` from S9) and deploy central. Agents never do either.
- **Live check.** Run one pass against a real test repository after merge to confirm the GitHub token's permissions.
- **Starting a dead watcher.** It needs a launcher on each machine, which is P5. Until then, a silent watcher is reported and a person restarts it.
- Spec check, still open after one revision: S3 [missing_dependency] S3 says 'the five checks in decision 5', but docs/decisions/0010-supervisor.md has no list of five plan checks. Decision 5 is 'Branches and environments'. Plan approval is one row in the decision 6 table: 'spec check clean and plan within policy paths'. The agent would have to invent the other checks. Suggested fix: Write the five checks into S3 (or into 0010 in an earlier item), for example: restricted path, 'Needs a person' not None, every item has an Acceptance line, policy loaded and valid, spec check clean. Then point S3's acceptance at each one.
- Spec check, still open after one revision: S6 [missing_dependency] S6 says 'the six conditions from decision 6'. Decision 6 is the autonomy table, and its Merge row only points to 'the deterministic gate in decision 4'. Decision 4 lists four conditions (CI green, review approved, no restricted paths, PR author differs from merger), not six. The `merge.review` key and the 'two more' conditions are not defined anywhere. Suggested fix: List the six conditions in S6. Likely ones are the four above, plus head SHA equals the evaluated SHA and the PR is not already merged or is mergeable. Also define the allowed values of `merge.review` and `merge.method`.
- Spec check, still open after one revision: S2 [missing_dependency] `GET /api/v1/supervisor/whoami` is specified as 'any valid token', and S8 calls it first without a project. In tokens.py, `bearer_token` answers 403 for a personal token unless `?project=` or `?repo=` names a project where the account is developer or above. For a project token, `g.token_account` is None, so there is no account role to return. Suggested fix: State that whoami and state require a personal token and `?project=<slug>`, and that the supervisor is a manager member there. S8 must send `?project=`. A project token gets 403 on both routes.
- Spec check, still open after one revision: S11 [open_decision] `runners.set_control` writes a single `control` value, with `control_by`. `wake` is another value in that column, so a supervisor wake would overwrite a person's `pause` or `stop`. Nothing says who clears `wake`, or whether the beat returns it once or on every beat. `CONTROLS = ('pause','stop')` in runners.py and the `control in ('pause','stop')` filter in autowatch.py `beat()` would also drop it. Suggested fix: Decide that `wake` never replaces a non-null control, that the beat returns it once and clears it, and that `CONTROLS` is extended in S11. Add `::test_wake_does_not_overwrite_pause` to the acceptance.
- Spec check, still open after one revision: S11 [untestable_acceptance] The acceptance names `tests/central/test_pages.py`, which does not exist. The existing runner page tests are in `tests/central/test_runner_pages.py` and `tests/central/test_runs_and_blockers_pages.py`. The Overview page is named in the item but no test covers it. Suggested fix: Put `test_runs_page_shows_paused_by_supervisor` in `tests/central/test_runner_pages.py`, or say the file is new. Add an Overview-page assertion.
- Spec check, still open after one revision: S12 [missing_dependency] The step needs CI status on the base head (the red/green check). The S5 `Forge` interface is `pr`, `changed_files`, `checks`, `reviews` and `merge`, and has nothing that returns the base branch head or its checks. The item also needs 'a task is ready' and 'idle watcher' signals, and S2's state read provides neither (runner state and task status only). Suggested fix: Make S5 `checks` take a ref or SHA and add `head(branch)` to `Forge` and `FakeForge`. In S12, define 'idle watcher' as runner state `watching` and 'ready' as an approved or open task for that assignee.
- Spec check, still open after one revision: S4 [open_decision] The route accepts 'approve, changes or escalate', but `tasks.decide` only accepts approve, changes and reject, and `escalate` leaves the task in `plan_review`. The item does not say whether escalate calls `decide`, whether it notifies managers, or how the pass recognises a task it already escalated. The 'further pass does nothing' in J2 depends on that. Suggested fix: Say that escalate does not call `tasks.decide`, only writes `supervisor.plan.escalate` plus a manager notification, and that the pass skips a `plan_review` task whose latest supervisor audit row is `escalate` or `approve` for the current plan post.
