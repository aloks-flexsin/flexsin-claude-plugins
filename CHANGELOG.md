# Changelog

All notable changes to this project are written here (Keep a Changelog).

## [Unreleased]

### Fixed
- Central: before `init-db` adds the runner-friction tables, event pushes with friction events, task reports, plan
  decisions and closing a task failed with 503; the friction bookkeeping is now skipped (in a savepoint, with a
  warning) until the migration runs, and everything else works.

### Added
- Central: runner frictions close the loop (decision record 0011). Rejecting a drafted friction task (rejecting its plan
  or closing it) mutes the signature with "task #N rejected"; a task reported done marks it `fixed`. A fixed signature
  counts only days after the fix, and reaching the threshold again drafts "Runner friction came back: <signature>",
  linking the earlier task (`previous_task_number`); the Frictions page shows Fixed and the earlier task's link.
  Unverified on the live service.

## [0.23.0] - 2026-11-25

### Added
- Runner self-reflection, first slice (decision record 0011): the runner records what got in its way as `friction`
  events in event.v1, with one of eight closed kinds and a fixed-vocabulary subject, never project content; the central
  service accepts and validates the same type. Recorded at most once per signature per item per run:
  `permission_denied` (`Bash:<executable>` or the tool name), `gate_stop` (docs, changelog, browser, journey, suite,
  stale), `fix_round` (suite, journey), `retry` (`exit_<code>`), `usage_stop` (limit, budget, threshold),
  `refused_start` (`newer_installed` or the failed preflight group, from the new `autosafe.preflight_groups`) and
  `central_error` (`http_<status>:<route>`, e.g. `http_409:/api/v1/tasks/N/heartbeat` for a lost claim, or
  `unreachable`) for failed heartbeats, reports, wait notes and event pushes. `autotasks.beat`, `finish` and
  `wait_note` take an optional `on_error(status, route)`; `autodeliver.push` also returns `skipped_status` and
  `error_status`. Counting and drafting on the central side come in later items.
- Central: a "turn on push" bar under the top bar of every signed-in page (not the Account page) when push is set up,
  shown once per browser: only when push is supported, not blocked, not yet subscribed here and not answered before
  (`localStorage["push-ask"]`). Turn on uses the same `enablePush` steps as the Account page; Not now hides it for
  good in this browser. Unverified in a real browser.

## [0.22.0] - 2026-11-25

### Fixed
- Runner publishing: when `gh pr create` fails because GraphQL is blocked (as behind the cloud-session proxy), the runner
  opens the pull request with the REST API (`gh api repos/<owner>/<repo>/pulls -X POST`), owner and repository taken from
  the push remote's URL; the `publish` event records the REST URL, or both errors when that fails too.
- Central service: a page that needs a table or column `init-db` has not added yet answers 503 "This page needs a
  database update" with the command to run, instead of crashing (the live site broke this way after the runner table).
- Every plugin command writes UTF-8 to its console (`autosafe.utf8_console()`), so a task, plan or log with characters
  such as "→" no longer crashes on a Windows cp1252 console.
- Tests: `test_init_rule.py` runs on Python 3.12 (it used a 3.13-only `read_text(newline=)`), and the service worker
  test compares bytes, so it passes on a Windows checkout with CRLF line endings.

### Added
- Central **Tasks** page opens as a Kanban **board** with six columns (Open, Planning, Needs approval, Ready, Building,
  Done) and a Board | List toggle; the list stays as it was. The choice is kept in the `task_view` cookie, "Mine only"
  and the search work on both, and the Done column shows the 20 most recent with a link to all of them. Read-only cards.
- Runner usage stop: after each item the runner reads the Claude plan's usage from the agent's output (`rate_limit_event`:
  5-hour and 7-day windows) and stops before the next item once either window reaches `usage_limit.stop_at_percent`
  (default 90; `100` turns it off), exit code 75 with reason `usage_threshold`. With `usage_limit.wait` it waits for that
  window's reset instead, when the reset is within `max_wait_minutes`. `/auto-runner:status` shows the last plan usage, and
  the central Runs page shows "Stopped at N% of the 5-hour limit (resets HH:MM UTC)". Unverified until a real run nears the limit.
- Claude identities carry their surface (`auto-runner`, `cli`, `desktop`, `browser`; today the runner reports
  `auto-runner`), shown on the Account and Members pages. `init-db` rebuilds an older `claude_identity` table to add it.
- The runner checks which Claude account the CLI is signed in to (`claude auth status --json`) against the optional
  `AUTO_RUNNER_ACCOUNT_EMAIL`: a mismatch prints a warning and the run continues. `run_start` carries only
  `account_check` (`match`, `mismatch`, `not_set`, `unknown`), never the email. `/auto-runner:central` status shows it,
  and the Runs page marks a mismatched run "Claude account not confirmed".
- Central tasks: a task that waits in a runner's queue shows **Queued** and keeps its claim until its run starts (then
  Building). Heartbeats take `state` (`queued` | `building`) and a `note` (at most 300 characters) that the task page shows
  as the reason it waits; the runner sends them when a request waits for a branch and when its run starts. An admin
  release moves a queued task back to approved.
- Close a task from its page: the assignee, a manager, owner or admin closes an open, planning, approved, queued or
  changes-requested task with a reason (`POST /projects/<slug>/tasks/<n>/close`, confirm step, audited as `task.close`);
  it becomes rejected and no runner takes it. A task being built is refused ("stop its runner first").
- Continuous integration: `.github/workflows/tests.yml` runs `python -m pytest -q` on Python 3.12 for every pull request
  to `main` and every push to `main` (read-only permissions, no secrets).
- Decide a waiting plan from the terminal: `autotasks.py decide N approve|changes|reject [--comment TEXT]` calls
  `POST /api/v1/tasks/<N>/decide` (personal token; the task page's rules, recorded "through the API") and prints
  "Task N: approved" / "changes requested" / "rejected", or "Not done: <reason>". Unverified until used against the
  live service.
- The runner gets a task's screenshots: `GET /api/v1/tasks/<N>` lists `attachments` (id, filename, content type,
  size) and `GET /api/v1/tasks/<N>/attachments/<id>` returns the bytes (personal token, same project rules as the task
  API). `autotasks.py next` downloads them to `.auto/run/tasks/<N>/attachments/<id>-<safe name>` (once; a name can
  never leave the folder) and prints their paths under "Screenshots"; `/auto-runner:auto` opens them before planning.
  Unverified until used against the live service.
- Central notifications: each notification on `/notifications` says how it was delivered ("in-app", "push sent",
  "push failed: <reason>", "push off"), and admins get a **Notification log** (`GET /admin/notifications`, linked from
  Accounts) of recent notifications across everyone with recipient, project, kind, delivery and time, filterable by
  `?account=` and `?kind=`. The central guide gains a "Notifications" section. Unverified against a live push service.
- Central pages: a dark mode toggle in the top bar ("Dark mode" / "Light mode", signed in and signed out). It posts to
  `POST /theme`, which takes `light` or `dark` (anything else is 400), keeps the choice in the `theme` cookie for a
  year and returns to the page it came from (same-site paths only, else `/my`). Light stays the default.
- Propose a terminal plan for approval on the page: `POST /api/v1/tasks` accepts `"approval": "page"` and stores the
  task as `plan_review` (plan and items kept, claim held by the runner) instead of `building`; the default is unchanged.
  `autotasks.py propose --title T --plan FILE --items FILE` uses it, and `autotasks.py list` shows such a task as
  "waiting for approval". Unverified against the live service.
- Auto-runner watch mode from setup: `/auto-runner:central` offers to start the watcher as its optional last step once
  the checkout is connected and signed in; the central guide gains a "Hands-free work" section (starting it, limits,
  runners on the pages, Pause, Resume and Stop), and the plugin and central READMEs say so. Unverified against the live
  service.
- Auto-runner watch mode reports and obeys: the watcher beats the central service (`runners/beat`) with what it is
  doing (watching, planning #N, building #N, paused) and its spend, and between steps obeys Pause (waits without taking
  work) and Stop (ends cleanly) pressed on the pages. Unverified against the live service.
- Auto-runner watch mode guardrails and command: `/auto-runner:auto watch` starts the watcher (`bin/autowatch.py`). Every
  `claude -p` it starts (plans, spec checks, builds) adds its reported cost to `.auto/run/spend-<local date>.json`; at
  `watch.daily_budget_usd` (default 20 USD) it pauses until local midnight. At Claude's usage limit it waits for the
  reset; only one watcher (so one build) runs per checkout (`.auto/run/watch.lock`). Unverified against the live CLI.
- Central service, sign-in and account (UX audit PR 6): a focused **sign-in** with Show and a lockout explanation that
  never reveals whether an email exists; **invite** links ask for your name and show a live password checklist; the
  **device** page shows the code large with a countdown; **Account** (was Password) sets your name and password, and
  admins can set anyone's name. Every page checked at phone width.
- Central service, people and access (UX audit PR 5): **Members** shows when each person was last active, a role select
  that saves itself, Remove under "⋯", an **Invite people** form whose link comes in a copy field, and **Pending
  invites** with expiry, New invite link and **Cancel invite**. **Machine tokens** and **My tokens** get copy fields,
  relative times and confirmations; **Admin › Accounts** gets search, project counts, last active and a "⋯" menu.
- Central service, Runs and Blockers (UX audit PR 4): **Runs** shows live runs as cards with progress and how long they
  have run, then a history with outcome chips, reasons in words, durations and the next step for a stopped or silent run.
  **Blockers** list the question first with how long each has waited; the **answer page** names the task and the waiting
  runner, offers the options as cards with the recommended one marked and chosen in advance, and folds the context away.
- Central service, My work and tasks (UX audit PR 3): **My work** shows what waits for you as cards (short plans are
  approved right there), one In progress list (yours first, then the team's, with quiet-runner warnings), recently done
  with `PR #n`, and project chips. The **Tasks** tab has state tabs with counts, "Mine only" and a search; tasks are
  added on their own **New task** page (with "How we know it's done"). The **task page** shows a step tracker, a decision
  bar, the plan rendered, items as a checklist with their live state, a side panel, admin actions under More, and an
  Activity history (plan posts and runner reports are now audited).
- Central service, new frame (UX audit PR 2): sign-in lands on **My work**; a top bar with the number of decisions
  waiting for you and an account menu; one header and tab bar (Overview · Tasks · Runs · Blockers · Settings, with
  counts) on every project page; a project now opens on its **Overview** (needs attention, working now, recent activity);
  members, repository and machine tokens move under **Settings** (old token URL redirects); projects list as cards.
- Central service, UX foundations (from the UX audit): one status vocabulary with tone and shape, relative times,
  readable names and runners, runner reasons in words, a safe Markdown renderer for plans, copy buttons, live times and
  auto-refresh (`static/app.js`), and **a confirmation before every action that cannot be undone** (a dialog with
  JavaScript, a confirm page without). Needs `init-db` (column `account.name`).

## [0.21.0] - 2026-11-24

### Added
- Central service: **My work** (`/my`, first in the top bar): across all your projects, plans waiting for your approval,
  blockers you can answer, your tasks with build progress, every task a runner is working on now, and recently finished
  tasks with their pull requests. Admins see every project.
- The **task page shows its build live**: items done out of the task's own items, the item in progress, the latest run's
  state and tests, blocked items and every run of the task. The runs page links each run to its task. No migration.

### Changed
- The runner's `run_start` and `run_end` events name the central task the run builds (`task`).

## [0.20.0] - 2026-11-23

### Added
- **One-command setup** (decision record 0007, step 5). `/auto-runner:central` in a folder that is not a checkout signs
  you in, lists **your projects** from the central service (`autocentral.py projects`, `GET /api/v1/projects`) and clones the
  one you pick; in a checkout it also syncs (`autocentral.py sync`: fetch origin, fast-forward the base branch only when it
  is checked out, clean and has no commits of its own; anything else is reported and left alone).

## [0.19.0] - 2026-11-23

### Added
- **Central tasks** (decision record 0007, step 4; unverified against the live service). A project's **Tasks** page lists
  every task, who it is assigned to, its state and the runner working on it. People add tasks for themselves; admins for
  anyone who can run runners. `/auto-runner:auto` with no task takes your next one: a new task is planned and its plan
  **waits for approval on the task page** (assignee, manager, owner or admin: approve, request changes, reject) while the
  runner moves on; an approved one is built on its own branch and reported done with its pull request. Terminal tasks are
  recorded there too. **Claims** keep two runners off one task, expire after 2 hours without a sign of life (never while a
  plan waits for a person), and admins can release or reassign them; the runner then stops that task (`claim_lost`).
  Needs `init-db` (table `project_task`). `bin/autotasks.py`.

## [0.18.0] - 2026-11-23

### Added
- **Sign in from Claude** (decision record 0008, second part). `/auto-runner:central` signs a computer in: it shows an
  address and a short code, the person approves it on the central site (which names the computer and repository), and the
  plugin stores a **personal token** without printing it: the user variable `AUTO_RUNNER_TOKEN` on Windows,
  `~/.config/auto-runner/credentials` (mode 600) on macOS and Linux. `autocentral.py login start` / `login wait`.
- Central service: personal tokens work for every project where the account is a developer or above, checked on every
  call (`?project=<slug>`), stop after 90 days unused, and are listed and revoked on **My tokens**; admins can revoke all of
  an account's. Events sent with one are stamped with the account, and the runs page shows "signed in as". Needs `init-db`
  (tables `device_login`, `personal_token`, column `event.account_id`).

### Changed
- The plugin finds the central token in this order: `AUTO_RUNNER_TOKEN`, the credentials file, then the project token in
  `central.token_env`. Every call to the service names the project. Project tokens keep working, for machines.

### Added
- Central service: **invites** instead of shared passwords (decision record 0008, first part). Owners and admins invite
  an email with a role from a project's members page; someone new gets an account with no password and the inviter
  gets a one-time link (single use, 72 hours, only its hash stored) to send them, with which they set their own
  password. Admin password resets now make the same kind of link and stop the old password at once. Needs `init-db`
  (new table `invite`).

## [0.17.0] - 2026-11-23

### Added
- Central service: a **Runs** page per project (`/projects/<slug>/runs`, any member) showing who is running what: the
  person and Claude account, branch, state (running, finished, stopped with its reason, or silent after 2 hours without an
  event), the item in progress, items done and blocked, the last test result and the pull request. Built from the events
  the runners already send; no migration.

### Changed
- The runner's `run_start` event names who ran it (`user`, `account`), and run ids are unique across people and machines
  (`<YYYYMMDD-HHMM>-<6 hex>`), so two runs started in the same minute stay apart.

## [0.16.0] - 2026-11-23

### Changed
- One project per repository (decision record 0007). The central service fills a new project's slug and name in from its
  repository URL (the repository's name; `<owner>-<repo>` when taken) and refuses a second project for the same repository
  in any URL form; `init-db` adds and fills `project.repo_key` with a unique index, and stops before any change, listing
  them, when two projects already share a repository.
- Nobody types a slug: `/auto-runner:init` and `/auto-runner:central` read the project from the folder's `origin`, and the
  service's own slug for that repository wins once the token is set. `--central-project` is optional.
- Init, `/auto-runner:central` and the runner refuse in a linked git worktree (such as `.claude/worktrees/...`) and name
  the main checkout to use instead.

## [0.15.0] - 2026-11-23

### Added
- `/auto-runner:central`: checks whether the project is connected to the central service (the `central` section, the token
  variable without showing it, the service's answer and the repository check, the runner identity, events not sent yet,
  waiting blockers with their page links) and explains how the page is used. It then walks through what is missing,
  changing anything only after you agree: connect this checkout (init) or clone from the service (join), and set
  `AUTO_RUNNER_USER` / `AUTO_RUNNER_ACCOUNT`. The token is always stored by the person with a hidden prompt. The check
  itself (`bin/autocentral.py status`) is read-only and never pulls answers. Tested against the real central app through
  its test client; unverified against the live service.
- `docs/central-guide.md`: how admins set a project up on the central service and how teammates join it, the role table,
  answering a blocker, and troubleshooting.

## [0.14.0] - 2026-11-23

### Changed
- The runner follows the team git rules (decision record 0006; unverified beyond fakes). Every run needs
  `AUTO_RUNNER_USER` (the person) and `AUTO_RUNNER_ACCOUNT` (the Claude account in use) in the environment, and run
  branches are `<user>/<account>-claude-auto-runner/<type>/<brief>`, the type from the request's `*Kind:*` lines and the
  brief from its slug. `branch_prefix` is ignored.
- Each run fetches the base from the remote and starts from `<remote>/<base>`; a failed fetch stops the run.
- One request per branch: a new branch takes only the oldest pending request, and later requests wait until that branch
  is merged instead of joining it between iterations.
- Publishing (push, then a pull request titled `<type>: <brief> (<user> via <account>-claude-auto-runner)`) is on by
  default; `"publish": {"push": false}` or `"pull_request": false` turns it off.
- The runner starts every agent with `--disallowedTools Bash(git push:*) PowerShell(git push:*)`, so
  `.claude/settings.json` no longer needs a `git push` deny rule and `auto_init` stops adding one.

## [0.13.0] - 2026-11-19

### Added
- Journey deck: after every journey check the runner keeps `.auto/journeys/deck.html`, a slide deck of the app's user
  journeys built from the check's own screenshots (a cover slide per journey, one slide per step with expected and seen).
  A journey's slides are replaced only when it passes; a journey that fails now keeps its last passing slides under a red
  "Failing since" banner with the failed step, so old screenshots never pass for the app as it is.
- Central service: one shared stylesheet, a top bar with navigation and sign-out, status badges and cards on every page.

## [0.12.3] - 2026-11-19

### Added
- `path_prepend` in `.claude/auto.json`: project folders (such as `tools/bin` with tool shims) put first on PATH for the
  agent, the suite, the fast checks and the journey check's app, so a tool the project's permissions allow is found
  instead of a fallback they do not allow. It lives in the committed config, so every lane and restart gets it.

### Changed
- An item the agent could not finish because a tool call was refused for lack of permission now says which call and how
  to allow it, instead of a bare "blocked".

## [0.12.2] - 2026-11-18

### Fixed
- The journey check's click-through agent and the exploratory browser agent now run on `models.tester` instead of always
  the CLI's default model, so a project that sets Sonnet for its hands no longer pays Opus prices for its browser checks.

## [0.12.1] - 2026-11-18

### Fixed
- A soft cut-off at the usage limit (a result with a limit message but no error flag, and nothing committed) is treated as
  a usage limit, not as a red suite.
- While waiting for a usage limit to reset, the runner tries again after at most 25 minutes instead of trusting a late
  stated reset, and at once when another lane on the same Claude account gets an attempt through.
- An attempt that runs out of its budget hands its uncommitted work to the item's next attempt (at most twice) instead of
  losing it; the prompt now tells the agent its budget and to commit before about nine tenths of it.
- A lock left by a run that crashed or was killed is cleared automatically.

## [0.12.0] - 2026-11-18

### Changed
- A red suite no longer stops a lane at once: the item is reopened (unticked, so a red commit is never left marked done)
  and gets up to `review.max_fix_rounds` (default 2) fix rounds with the end of the failing output in its prompt. After the
  last round the runner raises a blocker, which shows on the central service, and stops.
- Before committing, the agent runs the project's fast checks (new `check_command`, for example lint and type checks, or
  else the project's own lint and type checks), the tests covering its change and the end-to-end tests of what it changed.
  This restores checks that 0.8.1's "related tests only" dropped.

### Fixed
- A handover stash (`auto-runner: <ITEM> unfinished ...`) is now restored by the runner itself before the item's next
  attempt, instead of relying on the agent to read a note; only the agent's files are restored.

## [0.11.1] - 2026-11-13

### Changed
- Reuse before building, made stricter: the engineering principles now also cover hosted APIs and services (what they
  really cost beyond a free tier, self-hosting, lock-in), extending a package that covers most of a need, not adding
  dependencies for trivial tasks, and never inventing a package, link, price or licence. `/auto-runner:auto` plans carry a
  **Reuse check** table for each significant new capability, and the spec check reports items that hand-build what a
  package already in the project, or the plan's Reuse check, provides (`reinvents`).

## [0.11.0] - 2026-11-13

### Added
- Journey check after each slice: with `acceptance.journeys` on, the runner starts the app after a slice's last item and
  an independent agent clicks through the slice's user journeys (and earlier slices') with screenshots per step. A failed
  journey becomes a fix item in the same slice and the slice is checked again; after `max_fix_rounds` a blocker is raised.
  `/auto-runner:auto` plans now include a **Journeys** section.

## [0.10.0] - 2026-11-13

### Added
- Spec check before queuing: `/auto-runner:auto` has a separate, read-only reviewer check the plan's queue items against
  the code before you see the plan (missing pieces, wrong order, untestable acceptance lines, open product decisions,
  items too big, slices without a journey), fixes what it finds and lists it in a **Spec check** section.
  `/auto-runner:spec <plan file>` runs the same check on its own.

## [0.9.0] - 2026-11-13

### Added
- Usage limits: when Claude's usage limit stops an item, the runner recognises it (instead of reporting a red suite,
  leftovers or no progress), stashes the item's unfinished changes, puts the item back in the queue and stops with exit
  code 75. With `"usage_limit": {"wait": true}` it waits for the reset (up to `max_wait_minutes`) and carries on.
- The end of a run prints the suite's totals line from the last independent test run.

### Fixed
- A reused worktree whose branch was already merged is moved up to the base branch at the start, so a run no longer
  builds on old code; a branch with unmerged work gets a warning.
- A runner started from an older copy than the installed auto-runner refuses to start and names the installed one.

## [0.8.1] - 2026-11-12

### Changed
- The agent runs only the tests that cover its change (the whole suite once if it touched shared code or setup); the
  runner's independent full-suite run after each item stays the gate. This stops the suite running twice per item.
- Iteration logs keep the agent's full `stream-json` transcript (every tool call and result) instead of only the final
  summary, so it shows which tests ran, whether subagents were used and where an item's time went.

## [0.8.0] - 2026-11-06

### Added
- A third engineering principle, first in the file: think like a senior technical manager. Start from the intent, own
  the outcome end to end (data, security, deployment, rollback, monitoring, docs), decide technical choices yourself with
  the reasons written down, and ask a person only about product, cost or anything hard to undo. Plans now open with an
  **Intent** section and a **Key decisions** section.

## [0.7.1] - 2026-11-05

### Fixed
- On Windows the runner read the agent's, the test command's and git's output in the ANSI code page, so UTF-8 text such
  as `₹` or `ā` raised `UnicodeDecodeError` and the iteration lost its cost figure. Every child's output is now read as
  UTF-8 (an invalid byte is replaced, never fatal) and Python children are asked to write UTF-8.

## [0.7.0] - 2026-11-05

### Added
- `/auto-runner:update`: checks the marketplace for a newer auto-runner, shows the changelog entries since the installed
  version and, after you agree, installs it; restart Claude to load it.

## [0.6.0] - 2026-11-03

### Added
- Engineering principles ship with the plugin (`docs/engineering-principles.md`): build in vertical slices that are each
  usable on their own, and use the project's components and established open-source libraries before building from
  scratch. Planning follows them and every iteration's prompt carries them. A project can use its own file or turn them off.
- Queue items take a `*Slice:*` line; new requests without one are refused while the principles are on.
- Decision record `docs/decisions/0005-engineering-principles.md`.

## [0.5.0] - 2026-11-02

### Added
- `publish` in `.claude/auto.json`: after a clean run the runner (never the agent) pushes its own run branch to the remote,
  without force and never the base or a `lanes.protected` branch, and with `pull_request` opens a pull request with `gh`.
  The outcome is a `publish` event, which the central service also accepts. Off by default; merging stays with a person.
- Decision record `docs/decisions/0004-runner-publishes-its-branch.md`.

## [0.4.0] - 2026-11-02

### Added
- `env_files` in `.claude/auto.json`: gitignored files such as `.env` are copied into each run's worktree, so tests and the
  app use the real configuration; their values are redacted from logs and events, and a file git would commit is refused.
  Init proposes `.env` when it is ignored.
- `"safety": {"allow_env_read": true}` (init: `--allow-env-read`) lets the agent read `.env` files: init leaves out the
  `Read(./.env)` deny rules and preflight stops demanding them. Off by default.
- Decision record `docs/decisions/0003-env-files.md`.

## [0.3.0] - 2026-11-02

### Added
- Central projects have a repository URL (`repo_url`): required when a project is created, changed by owners and admins on
  the project page and audited. Only https, ssh and `user@host:owner/repo` forms are taken, never with credentials.
  `python -m central init-db` adds the column to an existing database.
- `GET /api/v1/project` answers the token's project (slug, name, repository URL) for a token with any scope.
- `/auto-runner:init` connects a project to the central service: `--central-url` and `--central-project` write the
  `central` section with its own token variable (`AUTO_RUNNER_TOKEN_<SLUG>`), the preview says what to create on the site
  and how to store the token, and once the token is set init refuses unless this folder's `origin` is the project's repository.
- `/auto-runner:join <slug>` clones a project's repository from its central record with your own git credentials and
  shows init's preview there.
- Decision record `docs/decisions/0002-central-first-setup.md`.

### Changed
- `/auto-runner:init` needs `--central-url` and `--central-project`, or `--local` to keep answers on this machine; a
  project that already has a `central` section needs neither.

## [0.2.2] - 2026-11-02

### Added
- `central.token_env` in `.claude/auto.json` names the environment variable that holds a project's central token, so
  several projects can each keep their own token set at once (default stays `AUTO_RUNNER_CENTRAL_TOKEN`).

## [0.2.1] - 2026-11-02

### Fixed
- Event pushes to the central service send at most 20 events a request and wait up to 30 seconds, so a backlog no
  longer times out against the live service.

## [0.2.0] - 2026-11-02

### Added
- Central service (`auto-runner/central/`): a Flask app with its own accounts, project access, per-project API tokens,
  `event.v1` ingest and the delivery endpoints. Deployment is unverified.
- `HttpTransport` in `autodeliver.py`; the runner uses the central service when `central` is enabled in `.claude/auto.json`
  and `AUTO_RUNNER_CENTRAL_TOKEN` is set.
- Decision record `docs/decisions/0001-central-service.md`.
- The runner sends its event ledger to the central service when `central` is on, and `raised` blocker events carry the
  question and options; the service has blocker pages where owners, managers and admins answer them.
