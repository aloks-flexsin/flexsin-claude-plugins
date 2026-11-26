# auto-runner

A Claude Code plugin. You plan a task with `/auto`, the plan becomes queue items, and a runner works through them unattended in a separate git worktree:
one item per iteration, an independent test run after each, one commit per green item. After a clean run the runner pushes its own
branch and opens a pull request (the team git rules below); nothing is merged or deployed.

Status: **built and tested with a fake `claude` command; never run on a real second project or live.** Treat it as unverified until it has.
The foundations added later (event ledger, blockers, delivery, staleness, evaluation harness; ADR 0029) are likewise **unverified**: they are
proven only with fakes (a fake `claude`, a fake runner, an in-memory fake central service), never with a live model, a real central service or a real project.
Setup and doctor, browser acceptance (Layer 1 and Layer 2), throwaway accounts, the changelog and the docs gate (ADR 0030) are also **unverified**:
fake browser runner, fake installer, fake model; no real browser was started, nothing was downloaded, no live model ran.
**SIVA itself does not use any of this:** its unattended runs still go through `scripts/auto_run.ps1` (PowerShell), which has none of these features.

## Install
From the Flexsin marketplace (this repository):

```
claude plugin marketplace add aloks-flexsin/flexsin-claude-plugins
claude plugin install auto-runner@flexsin-claude-plugins
```

Or load it for one session without installing, from a clone: `claude --plugin-dir ./auto-runner`.
Check the manifest with `claude plugin validate ./auto-runner` (needs the `claude` CLI on PATH).

## Use
1. `/auto-runner:init` shows a preview of what the project needs and asks before writing anything (it runs `bin/auto_init.py`; `--apply` writes).
   It connects the project to the central service unless you choose `--local` (see "Central-first setup").
   `/auto-runner:join <slug>` clones a project registered on the central service and then runs init there.
2. `/auto-runner:auto <task>` plans first. Before it shows you the plan, a separate read-only reviewer (`bin/autospec.py check`,
   only Read/Grep/Glob, model `models.plan_reviewer` or `review.model`, cap `review.max_budget_usd`, default $1) checks the queue
   items against the code: something an item needs that neither exists nor is built earlier, an item that needs a later one, an
   untestable acceptance line, an open product decision, an item too big for one sitting, a slice without a journey, an item that hand-builds what a package already in the project
   or the plan's **Reuse check** provides. For each significant new capability the plan carries a **Reuse check** table
   (requirement, options with real links, cost and free-tier limits, licence, recommendation, why; unchecked facts marked
   unverified), and the engineering principles cover hosted services (real cost, lock-in), needless dependencies and never
   inventing packages, links, prices or licences. Findings are
   fixed in the plan (up to `review.max_plan_revisions` rounds) and listed in its **Spec check** section; a reply that is not valid
   `spec.v1` fails the check rather than passing it. `/auto-runner:spec <plan file>` runs the same check on a plan written by hand.
   Unverified beyond a fake reviewer. When you accept the plan it writes the request to `.auto/pending/<slug>/items.md` and starts the runner.
   **Watch mode** (`/auto-runner:auto watch`): `bin/autowatch.py` keeps taking your central tasks, planning new ones and
   building approved ones, within a daily spend cap (`watch.daily_budget_usd`), waiting out usage limits, one watcher per
   checkout; stop it with `.auto/STOP`. A task request left waiting in `.auto/pending/` (an earlier branch not merged
   yet) is built again by the watcher before it claims anything new, lowest task number first. Unverified against the
   live CLI and service.
   **No task given** (`/auto-runner:auto` alone): the runner takes your next task from the central service
   (`bin/autotasks.py`, 0.19.0, decision record 0007; needs the project connected and this computer signed in). A new task is
   planned the same way and its plan **waits for approval on the central page** while the runner moves on; an approved task
   becomes a request (`.auto/pending/task-<N>-<brief>/`) and runs on its own branch. A task typed in the terminal is also
   recorded there (`autotasks.py record`), or, when its plan should be approved on the central page instead, proposed
   (`autotasks.py propose --title T --plan FILE --items FILE`: stored as `plan_review` and claimed by this runner; once
   approved, `next` hands it back to build; `list` shows it as "waiting for approval"). A waiting plan can also be decided
   from the terminal: `autotasks.py decide N approve|changes|reject [--comment TEXT]` (`POST /api/v1/tasks/<N>/decide`,
   the task page's rules; `changes` needs `--comment`; prints "Task N: approved" / "changes requested" / "rejected" or
   "Not done: <reason>"; unverified against the live service). While a run works on a task it beats the claim before every iteration, stops
   (reason `claim_lost`, exit 1) if an admin released or reassigned it, and reports it done (clean run, queue empty) or
   stopped at the end. Its `run_start` and `run_end` events name the task (`task`, 0.21.0), so the central task page and
   **My work** show the build's progress live. `autotasks.py start` marks the task **Queued** (a heartbeat with
   `"state": "queued"`) while its request waits; when a run takes the request on a new branch it sends `"state": "building"`
   before the first iteration, and a 409 there prints "claim lost" and the run carries on without a central task. A request the run leaves waiting ("waits: <branch> is not merged into origin/main yet") gets a heartbeat with `"state": "queued"` and that sentence as `"note"` (at most 300 characters) for its own task (its `task.json`), so the task page says why it waits; the note goes when the task becomes `building`, and an unreachable service never fails the run. If a build fails before its run begins, the watcher puts the task back to `building` so `next` offers it again. Tested against the real central app through its test client.
   `/auto-runner:update` checks the marketplace for a newer auto-runner, shows the changelog entries in between and, after you
   agree, installs it (`bin/autoupdate.py check` / `apply`, both through the `claude plugin` command line); restart Claude to load it.
   `/auto-runner:central` checks whether the project is connected to the central service (config, token variable,
   the service's answer to `GET /api/v1/project` and the repository check, runner identity, events not sent yet, waiting
   blockers with their page links) and walks you through what is missing, changing anything only after you agree
   (`bin/autocentral.py status` is the read-only check; tested with the real central app through Flask's test client,
   never against the live service). How teammates and admins use the page: `docs/central-guide.md`. Once connected,
   its optional last step offers watch mode (`/auto-runner:auto watch`), started only after a clear yes; the guide's
   "Hands-free work" section explains it for people. Unverified on the live service.
   With `"enforce_runner": true`, edits in the main checkout are blocked (see the config table);
   `/auto-runner:allow-edits` lets the current session edit there for 12 hours.
3. `/auto-runner:status` and `/auto-runner:log` are read-only reports (counts by state, the running item, blocked questions, recorded spend; the last iterations and log tails; `--full N` prints one whole log).

The runner can also be started by hand: `python bin/auto_run.py [--project DIR] [--max-iterations 8] [--max-budget-usd 5] [--enqueue SLUG] [--dry-run] [--allow-dirty]`.

### Team git rules (0.14.0; tested with local bare remotes, a fake `gh` and a fake `claude`; never with two real runners)
- **Whose run:** every run needs `AUTO_RUNNER_USER` (the real person working, for example `alok`) and `AUTO_RUNNER_ACCOUNT`
  (the Claude account that person runs it with, for example `bhawish`), set per person or machine and never in
  `.claude/auto.json`. Without either, or with anything but lower-case letters, digits, `.`, `_` and `-`, the preflight
  refuses. `/auto-runner:init` says how to set them (PowerShell:
  `[Environment]::SetEnvironmentVariable('AUTO_RUNNER_USER', 'alok', 'User')`, the same for `AUTO_RUNNER_ACCOUNT`).
- **Claude account check (optional):** set `AUTO_RUNNER_ACCOUNT_EMAIL` to the email of the Claude account the CLI should be signed in
  with. Before the `run_start` event the runner asks `claude auth status --json` (20-second timeout) and compares the signed-in email,
  ignoring case. The result is `match`, `mismatch`, `not_set` (no variable) or `unknown` (the CLI failed, timed out, is signed out or
  answered badly); it goes into the `run_start` detail as `account_check`, and `/auto-runner:central`'s "Runner identity" lines show it.
  A `mismatch` prints a warning naming the variable and never stops the run. The email is compared on your machine only: it is never
  printed, logged, put in an event or sent to the central service.
- **Branch name:** the team convention `<user>/<account>-claude-<surface>/<type>/<brief>` with the surface `auto-runner`:
  `<user>/<account>-claude-auto-runner/<type>/<brief>`, for example `alok/bhawish-claude-auto-runner/fix/login`. `<brief>` is the
  request's slug (`queue-<stamp>` when the queue has open items but no request); `<type>` comes from the request's `*Kind:*`
  lines: any `feature`/`breaking` -> `feat`, else any `fix` -> `fix`, else only `internal` -> `chore`; none -> `feat`.
  A name already taken locally or on the remote gets `-2`, `-3`, ….
- **Latest base:** each run first runs `git fetch <remote> <base>` (`publish.remote`, default `origin`) and starts from
  `<remote>/<base>`. A failed fetch stops the run. No such remote, or a remote without the base branch yet, means the local
  base, with a notice.
- **One request per branch:** a new branch takes only the oldest pending request (`.auto/pending/<slug>`). While the worktree's
  branch has commits that are not in `<remote>/<base>` (its pull request is not merged), the runner only continues that
  branch's items and every other request waits, listed as `request '<slug>' waits: <branch> is not merged ...` at the start and
  end of the run. Once it is merged, the next run starts a new branch in the same worktree. Requests are no longer added
  between iterations. "Merged" is judged by ancestry, so after a **squash** merge remove the worktree
  (`git worktree remove <project>-auto`) before the next run.
- **Resuming a request:** `--enqueue <slug>` for a request whose items are all in the queue already, each with the same
  text (ignoring the tick and the runner's status line), resumes it instead of refusing it: the runner prints
  `request '<slug>' is already queued on <branch>: resuming it`, adopts the request's `task.json` as the current task
  (so a central task stopped part-way and taken again by watch mode beats and reports with its new claim), removes
  `.auto/pending/<slug>/` and carries on as a run without `--enqueue`. While another run holds the lock it only says the
  request is checked and waiting, and touches neither the folder nor `.auto/run/current-task.json`. A request with only
  some of its IDs queued, or with changed item text, is refused as before.
- **A run on a central task works only that task's items:** the queue is shared, so it can hold another request's open
  items (a task still being built on another branch). When a request from a central task is added or resumed, its item
  IDs are kept in `.auto/run/current-task.json` as `items` (`autotasks.adopt(..., items)`; `current()` keeps them when
  it takes a newer claim). The run then picks only ready items among them, ends with reason `queue_empty` once none is
  open (printing `the task's items are done; N open item(s) of other requests left untouched` when others are open),
  and reports the task done on that basis. Other items are never worked, ticked or given a status line by that run.
  With no current task, or one without `items`, every ready item is worked in file order as before.
- **Push and pull request:** see "Publishing a run"; both are on unless turned off.

Create `.auto/STOP` to stop a run between iterations. `.auto/lock` keeps a second run out; delete it by hand only if a run died.
Each iteration works the first *ready* item and names it in the prompt: an item whose status is `blocked` is skipped (delete or rewrite its
status line once the question is answered), and an item with a `  *Depends on:* A1, A2` line waits until each of those boxes is ticked.
When no ready item is left the run ends cleanly and lists what is blocked or waiting.

## What `init` does
Detects the test command (pytest, npm test, dotnet test, go test, cargo test) and proposes it; appends deny rules to `.claude/settings.json` (never removes or overwrites,
skips duplicates); creates `.claude/auto.json`, `.auto/queue.md` and `.gitignore` entries; reports an existing project `auto` command or scripts without touching them;
with `--with-rules` writes a short `AUTO_RULES.md` when there is no `CLAUDE.md`. Idempotent. `--remove` previews, and with `--apply` removes only what init added.
It also writes the `central` section (next section) or, with `--local`, none. When a gitignored `.env` exists it proposes
`"env_files": [".env"]`; `--allow-env-read` writes `"safety": {"allow_env_read": true}` into a new auto.json and leaves out the `.env` read rules.
When the central service is enabled (a new or an existing `central` section), init also appends a short "Work goes through the
auto-runner" section to `CLAUDE.md` (or to `AUTO_RULES.md` when there is no `CLAUDE.md`, including the one `--with-rules` creates)
telling interactive sessions to plan with `/auto-runner:auto` and send the plan with `autotasks.py propose`. It sits between
`<!-- auto-runner:routing:start -->` and `<!-- auto-runner:routing:end -->` markers, is shown in the preview, recorded in the
manifest (`routing_rule`, with the exact text added), and `--remove` takes out exactly that text, leaving the rest of the file
byte-for-byte as it was (an edited section is kept and reported).

## Central-first setup (tested with the real central app through Flask's test client; never against the live service)
The central service is the starting point for a project: an admin creates it there with its repository URL, and init connects
the checkout to it. Step by step for admins and teammates, with roles and troubleshooting: `docs/central-guide.md`;
`/auto-runner:central` checks a checkout and guides the rest.

- **One project per repository; nobody types a slug** (0.16.0, decision record 0007). `auto_init.py --central-url URL
  [--central-project SLUG] [--token-env NAME]` reads the project from this folder's `origin` (`autocentral.slug_from_repo`:
  the repository's name, lower case, other characters as `-`, the same rule the service uses) unless `--central-project`
  is given. Once the token is set, the service's own slug for this repository wins (it is `<owner>-<repo>` when the plain
  name was taken), and the token must still be that repository's project. No `origin` remote: init refuses rather than guess.
- It adds `"central": {"enabled": true, "url", "project", "token_env"}` to a new `.claude/auto.json`, or to an existing one
  with no `central` key (recorded, so `--remove` takes it out again unless it was edited). `token_env` defaults to
  `AUTO_RUNNER_TOKEN_<SLUG>` (upper case, `-` to `_`).
- **Main checkout only:** in a linked git worktree (for example one Claude Code made under `.claude/worktrees/`) init,
  `/auto-runner:central` and the runner's preflight refuse and name the main checkout (`autosafe.worktree_main`). The
  project and its connection are the same there; only the local `.auto/` state would differ, and it goes away with the worktree.
- Without `--central-url` and without `--local`, init refuses: a project can no longer slip into local mode unnoticed. An existing
  `central` section is never changed and needs no options.
- While the token variable is empty, the preview lists what to create on the site (the project with this folder's `origin` as its
  repository URL, a token with `ingest` and `pull`) and the PowerShell line that stores the token without echoing it:
  `$t = Read-Host 'Token for SLUG' -AsSecureString; [Environment]::SetEnvironmentVariable('AUTO_RUNNER_TOKEN_SLUG', [System.Net.NetworkCredential]::new('', $t).Password, 'User')`.
- Once it is set, init calls `GET /api/v1/project` and refuses, writing nothing, unless the token is that project's, the project has
  a repository URL, and `git remote get-url origin` is that repository. `autocentral.repo_key()` compares `https://host/o/r(.git)`,
  `ssh://[user@]host[:port]/o/r` and `user@host:o/r` as `host/o/r` (lower case); credentials in an https remote are never shown.
  A refused token (401/403) or an unreachable service also stops init. The same check runs for an existing enabled section.
- `auto_join.py SLUG --central-url URL [--dest FOLDER] [--token-env NAME] [--apply]` (`/auto-runner:join`) reads the project's
  repository URL from the service, refuses a folder that exists and any URL that is not https://, ssh:// or `user@host:o/r`,
  runs `git clone -- <url> <folder>` with your own git credentials (only with `--apply`), then prints init's preview there and
  the `auto_init.py ... --apply` line that writes it. The central service never holds git credentials.

## Config: `.claude/auto.json`
Every key is optional; unknown keys and wrong types are errors that name the key.

| Key | Default | Meaning |
|---|---|---|
| `test_command` | `""` | Command that proves the suite is green. Run independently after every iteration. |
| `check_command` | `""` | Fast checks (for example `npm run lint && npm run typecheck`) the agent must run and pass before it commits; allowed in the agent's tools. Without it the agent is asked for the project's lint and type checks if it has them. |
| `item_test_command` | `""` | Command that tests only one item's own test files, for example `python -m pytest -q {tests}`. `{tests}` is replaced by the quoted paths (they are appended when it has no `{tests}`). The paths are the existing files named on the item's `*Acceptance:*` line plus the test files its commits changed (`test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*`, or under a `tests/` folder). Empty = no per-item run. Only the helpers exist so far (`auto_run.item_tests`, `auto_run.item_test_cmd`); nothing uses them yet. |
| `path_prepend` | `[]` | Project folders (relative, committed, for example `["tools/bin"]`) put first on PATH for everything the runner starts: the agent, `test_command`, `check_command` and the journey check's app. For tool shims, so a command the project's permissions allow (`pnpm`) is found instead of a fallback they do not allow (`corepack pnpm`). The preflight refuses an entry outside the project or missing. |
| `check_only` | `false` | `true` = the project has no tests; every status and log then says "unverified: no tests". |
| `queue_file` | `.auto/queue.md` | The one file a person reads; each item gets a status line (queued, running, done + commit, blocked + question, rejected + reasons). |
| `rules_files` | `[]` | Files the agent must read first. |
| `share_dirs` | `[]` | Folders linked into the worktree instead of copied (a venv, `node_modules`). Junction on Windows, symlink elsewhere. |
| `env_files` | `[]` | Gitignored files (such as `.env`) copied into the worktree at the start of a run, so the suite and the app use the real configuration. Preflight refuses an entry outside the project, missing, not ignored by git or tracked. Values of 8 characters or more are redacted from iteration logs and events. A copy, not a link: a change in the worktree never reaches the project's file. |
| `worktree_suffix` | `-auto` | Worktree folder = project folder name + suffix, next to the project. |
| `base_branch` | `""` | Branch the worktree starts from. Empty = detect: `origin/HEAD`, else the current branch if it is main/master/trunk/develop, else one answer is needed. |
| `branch_prefix` | `auto/` | **Deprecated, ignored** since 0.14.0 (still accepted so old files load): run branches are `<user>/<account>-claude-auto-runner/<type>/<brief>`. |
| `commit_trailer` | `""` | Line added to each commit message. |
| `autonomy` | `"auto"` | `"auto"` or `"manual"`, read by the Supervisor from the base branch. **auto**: it decides instead of escalating: it approves plans whose "Needs a person" is not empty (the open points are recorded as decided in the decision's reasons), merges pull requests the merging identity opened (`merge.allow_self_merge` in the policy, default true here), and uses a default policy (only secrets restricted) when the project has no policy file. Green CI and restricted paths still refuse. **manual**: every person-in-the-loop step stays as before (`bin/autogates.py`). |
| `enforce_runner` | `false` | Opt-in guard: the PreToolUse hook (`autohook.py guard`) blocks Edit, Write, MultiEdit and NotebookEdit on paths in the main checkout (exit 2, the message names `/auto-runner:auto`). Paths under `.auto/`, the runner's worktree, paths outside the project and every path in a linked git worktree stay allowed. Typing `/auto-runner:allow-edits` lets that one session edit for 12 hours (`.auto/run/allow-<session_id>`; delete it to end sooner). |

Schema v2 sections (objects, every nested key optional, unknown nested keys and wrong types fail naming `section.key`). They are **parsed and validated only**, with one exception: `models.implementer` is passed to `claude -p` as `--model <value>` (no flag when it is unset or `inherit`). The preflight refuses any `models.<role>` value that is not `opus`, `sonnet`, `haiku`, `inherit` or a name starting with `claude-`, naming the key. A section appears in the loaded config only when the file sets it, so a config with only the keys above behaves exactly as before. `config.schema.json` describes every key.

| Section | Keys |
|---|---|
| `models` | `implementer`, `planner`, `plan_reviewer`, `work_reviewer`, `tester`, `scribe` (model alias or name). In use: `implementer` (every item and fix round), `plan_reviewer` (the spec check), `tester` (the journey check's click-through and the exploratory browser agent); unset = the CLI's default model. For example Sonnet for `implementer` and `tester`, Opus for `plan_reviewer`. |
| `lanes` | `protected`, `sandbox` (lists of branch patterns), `deploy_command` |
| `review` | `model`, `max_plan_revisions`, `max_fix_rounds`, `max_budget_usd` (the spec check uses `model`, `max_plan_revisions` and `max_budget_usd`) |
| `budgets` | `iteration_usd`, `run_usd`, `max_iterations` |
| `docs` | `adr_dir`, `required` (list of files), `map` (doc map path, default `docs/doc-map.json`), `check` (docs gate, default on) |
| `central` | `enabled`, `url`, `project`, `token_env` (the name of the variable holding the token; never a token) |
| `publish` | `push`, `pull_request` (flags, default **on**; `false` turns each off), `remote` (default `origin`): see "Publishing a run" |
| `principles` | `enabled` (default on), `file` (default the plugin's `docs/engineering-principles.md`), `require_slices` (default on): see "Engineering principles" |
| `sandbox` | `enabled` (default on), `required` (default off), `allowed_domains` (default `github.com`, `*.github.com`, `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`); read with `autocfg.sandbox()`. See "Sandbox" |
| `deploy` | no command | Deploy after a merge (decision 0012, Phase 5; read by the Supervisor from the base branch): `command` (run with the shell in a detached worktree `<repo>-deploy` at the base head), `smoke_url` (any 2xx counts; `smoke_tries`, default 3, 10 s apart), `rollback_command` (run when the command or the smoke check fails), `timeout_s` (default 600 per command). Auto mode only (repository and central project); no command means nothing is deployed. Secrets the command needs come from the environment, never from this file. See `bin/autodeploy.py`. |
| `safety` | `allow_env_read` (`true`: the agent may read `.env` files; init leaves out the `Read(./.env)` rules and preflight stops demanding them. Default off) |
| `locks` | `path`, `stale_after_minutes` |
| `usage_limit` | `wait` (default false), `max_wait_minutes` (default 360): wait for Claude's usage limit to reset and continue, instead of stopping; `stop_at_percent` (1 to 100, default 90): stop between items when a plan-usage window reaches it (100 = never) |
| `acceptance` | `browser`: `enabled`, `base_url`, `command`, `headless`, `exploratory`, `accounts`: `method` (`seed_command` or `signup`), `seed_command` |
| `changelog` | `enabled` (default off), `file` (default `CHANGELOG.md`), `versioning` (only `semver`) |
| `watch` | `poll_seconds` (default 300), `plan_budget_usd` (default 2), `daily_budget_usd` (default 20); read with `autocfg.watch()`. Settings for watch mode (`/auto-runner:auto watch`). `plan_budget_usd` caps one headless plan (`bin/autoplan.py`: planner and spec check together); `poll_seconds` is how long the watch loop (`bin/autowatch.py`) waits when idle; `daily_budget_usd` caps what all of the watcher's `claude -p` calls (plans, spec checks, builds) report per local day (`.auto/run/spend-<date>.json`); at the cap the watcher pauses until local midnight. The headless planner's instructions are in `docs/planner-prompt.md` (the plan sections of `/auto-runner:auto` step 1) |

Besides `models.implementer`, three sections act: `acceptance.browser` (the browser stage below), `changelog` (the changelog step) and
`docs.map`/`docs.check` (the docs gate). Each does nothing when the file does not turn it on.

## Engineering principles (tested with a fake agent; no real model has been given them yet)
`docs/engineering-principles.md` ships with the plugin and applies to every project unless it says otherwise:
**think like a senior technical manager** (start from the intent, own the outcome end to end, decide technical choices
yourself with the reasons written down, ask a person only about product, cost or anything hard to undo; plans open with
**Intent** and **Key decisions** sections), **build in vertical slices** (each slice runs end to end and ends usable: it could be pushed, deployed and used) and
**use what exists before building** (the project's own components and UI library, such as shadcn/ui, then maintained,
permissively licensed open-source packages; building it yourself needs a reason in the commit).

- `/auto-runner:auto` reads the file while planning, lists the slices first and gives every queue item a `*Slice:*` line
  (`  *Slice:* 1 Sign up with email`).
- The runner refuses a request whose items have no `*Slice:*` line, or whose slices go backwards (`queue.validate(...,
  require_slice=True)`). Items already in a queue are not checked again.
- Every iteration's prompt carries the file's text in a `<principles>` block (`auto_run.principles_text`, at most 12,000
  characters). The item and the project's rules files win where they differ.
- `"principles": {"file": "docs/our-principles.md"}` uses the project's own file instead (preflight refuses one that is not
  a file inside the project). `"require_slices": false` keeps the text but drops the `*Slice:*` check, and
  `"enabled": false` turns both off: the prompt is then exactly as before.

## Publishing a run (tested with a local bare remote and a fake `gh`; never against GitHub)
## Sandbox
Where Claude Code's sandbox works (macOS; Linux and WSL2 with `bubblewrap` and `socat`; `autosafe.sandbox_available`),
each item's agent runs in it: the runner writes `.auto/run/sandbox-settings.json` (`auto_run.sandbox_settings`) and starts
the agent with `--settings <that file>`. Every shell command then runs inside the sandbox without a prompt and without the
shell allowlist (`autoAllowBashIfSandboxed`), never outside it (`allowUnsandboxedCommands: false`), and the agent refuses
to start rather than run unsandboxed (`failIfUnavailable: true`). It may write in the worktree, the repository's git folder
(a linked worktree's commits land there) and the `share_dirs` targets, and reach only `sandbox.allowed_domains`.
`git push` stays denied to the agent either way. Where the sandbox cannot run (native Windows, a Linux host without the
two tools) the agent keeps the shell allowlist below and the run says so in its first lines; `"sandbox": {"required":
true}` makes preflight refuse to start instead (group `sandbox`), and `"enabled": false` turns it off. Not yet tried
against a live sandbox (unverified): the setting names follow the Claude Code docs (sandboxing, settings reference).

The agent can never push: the runner starts every agent with `--disallowedTools Bash(git push:*) PowerShell(git push:*)`,
whatever the project's settings say. Publishing is **on by default** (since 0.14.0): the runner itself, a plain Python step with
your own git credentials, pushes the run branch **as it goes**: before each new item starts, whenever the branch has new
commits (`push_branch`, no pull request yet), and at the end of every run, clean or not, so the work is never only on one
computer.
`"publish": {"push": false}` turns it off; `"pull_request": false` pushes without opening a pull request. Left at the default,
a repository with no remote simply is not published; `"push": true` written out with no remote is refused.

- `git push --porcelain <remote> refs/heads/<run branch>:refs/heads/<run branch>`: only the run's own branch (it must start
  with `<AUTO_RUNNER_USER>/<AUTO_RUNNER_ACCOUNT>-claude-auto-runner/` and not match `lanes.protected`) or the branch of the
  central task it took over (below), never the base branch, never with force, with `GIT_TERMINAL_PROMPT=0`
  so it fails rather than waits for a password. A run with no commits pushes nothing. A failed in-run push is reported in
  the run's output and retried before the next item.
- Then `gh pr create --head <branch> --base <base>` with the title `<type>: <brief> (<user> via <account>-claude-auto-runner)` and the commit
  list as the body. When the task's items are still open at the end (stopped by `max_iterations`, STOP, a problem, a lost
  claim, a limit or a crash) it is opened with `--draft` (`draft: true` in the event), so no one merges half-done work.
  When `gh` reports that a pull request for the branch already exists (an earlier, stopped run of the same task), its URL
  is reused (`pr_existing: true`) and, once the work is finished, `gh pr ready <url>` marks it ready for review.
- When `gh pr create` fails and its output mentions GraphQL (blocked by a proxy, as in some cloud sessions), the runner opens
  the pull request with the REST API instead: `gh api repos/<owner>/<repo>/pulls -X POST -f title=... -f head=... -f base=...
  -f body=... --jq .html_url` through the same `pr_runner`, with owner and repository from `autocentral.repo_key` on
  `git remote get-url <remote>` (https and ssh forms; `--hostname <host>` for a host other than github.com). Its URL is
  recorded as `pr_url`; when it fails too (or the remote names no owner/repository), `pr_error` holds both errors. Any
  other `gh pr create` failure makes no REST call.
- The outcome is a `publish` event (`pushed`, `commits`, `pr_url` or `pr_error`, or `error`) in `.auto/events.jsonl` and,
  with central on, on the central service. A failed push or pull request is reported and never fails the run.
- A red run, or one stopped on a problem, still pushes what it committed and opens (or keeps) a draft pull request, from
  the run's `finally` step; a failure there is reported and never hides why the run ended.
- **Done items are marked on the branch:** when an item is done the runner adds a `Auto-Runner-Done: <ITEM>` trailer to its
  last commit (amended before it is pushed; `mark_item_done`), because the queue is often untracked.
- **Taking over a task's branch:** `autotasks.start` writes the task's recorded `branch` (and `pr_url`) into the request's
  `task.json`. When the run takes that request it fetches the branch and puts the worktree on it (`take_over_branch`;
  a worktree with uncommitted changes or other unmerged work is left alone and a new branch starts instead), adds the
  task's items, ticks those the branch's trailers mark done (`items_done_on_branch`), and continues with the rest. Any
  runner can do this, on any computer: a run that stopped part-way loses nothing.

## Safety preflight
The runner refuses to start unless:
- `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT` hold valid names (see "Team git rules");
- `.claude/settings.json` denies `git remote`, `git reset --hard`, `git clean`, checking out or switching to the base branch, and reading `.env` files
  (`git push` is no longer demanded there: the runner blocks it for every agent itself; an existing rule is left alone)
  (not demanded with `"safety": {"allow_env_read": true}`; the suite and the app can load `.env` either way, since the rule only stops the agent reading it, and `env_files` puts it in the worktree);
- every `env_files` entry is inside the project, exists, is ignored by git and is not tracked;
- with publishing on: `"push": true` written out needs the remote, run branches are neither the base branch nor match `lanes.protected`, and `"pull_request": true` written out needs `push` on;
- a `test_command` exists, or `check_only` is true;
- the repository has at least one commit;
- when `central.enabled` is true: `central.url` and `central.project` are set and there is a token (`autosafe.central_token`,
  in this order: a personal token in `AUTO_RUNNER_TOKEN`, the personal token in the credentials file, a project token in
  the variable named by `central.token_env`, default `AUTO_RUNNER_CENTRAL_TOKEN`; the message names what is missing; a token
  is never read from `.claude/auto.json`, where a `central.token` key is refused as unknown, and a `token_env` that holds a
  token is refused).

The agent is asked to run only the tests that cover its change (the whole suite once, if it changed shared code or setup):
the runner's own run of `test_command` after the iteration is the gate, so the suite no longer runs twice per item.
When `item_test_command` is set, that gate runs only the item's own tests instead: the test files named on its `*Acceptance:*`
line plus the test files its commits changed (`item_test_command` with `{tests}` replaced by them). An item with no such
file still gets the full `test_command`. The log line and the `tests` event say which ran (`scope`: `item` or `full`), and a
red per-item run's fix note names the command that failed.
Each iteration's log (`.auto/logs/<run>-iter<N>.log`) holds the agent's full `stream-json` transcript, every tool call and its
result, redacted like the rest of the log, followed by the runner's test and stage output.
Claude's usage limit is recognised from the agent's final result (or, when the CLI left none, its last lines), never from the
work's own output; a result that is not marked as an error but carries a limit message (a soft cut-off) counts too when the
attempt committed nothing. The item's uncommitted changes become a handover commit (below; the queue and `.auto/` are
left alone), the item goes back to queued, no suite runs, and the run stops with exit code 75 and reason `usage_limit`
instead of being reported as red, leftovers or no progress. With `"usage_limit": {"wait": true}` it waits and tries again:
at the stated reset or after 25 minutes, whichever comes first (a stated reset can be later than the real one), and at once
when another run on the same Claude account gets an attempt through (each attempt that is not stopped by the limit writes
its time to `auto-runner/usage.json` in Claude's config folder, which every lane reads). It checks the STOP file every 30
seconds; the interrupted try does not use up an iteration, and the run stops as above once the waiting would pass
`max_wait_minutes`. Plan usage: the CLI's `rate_limit_event` line in each iteration's output gives the utilization (0 to 1) and reset time of each
window (`five_hour` shown as "5-hour", `seven_day` as "7-day"; any other key by its name). It is stored as `plan_usage` on the
iteration's `.auto/runs.jsonl` entry and `auto_report.py status` prints "Plan usage (last iteration): 5-hour 16% (resets 20:00), ..."
in the computer's local time, or "Plan usage: not reported". Plan-usage threshold: `"usage_limit": {"stop_at_percent": N}` (whole number 1 to 100, default 90) stops the run
between items. After an item's iteration is recorded, and only when open items remain and another iteration would start,
the window with the highest utilization is compared with N; at or over it the run says "plan usage at N% of the <window>
limit (resets HH:MM): stopping before the next item" and ends with exit code 75, reason `usage_threshold` and
`{window, utilization, resets_at}` as `usage` in the `run_end` detail. With `usage_limit.wait` on it instead waits until that
window's reset plus 60 seconds (STOP ends the wait; the wait uses no iteration) and carries on, unless the reset is further
away than the waiting left under `max_wait_minutes` (the 7-day window can be days away): then it stops as when `wait` is off.
`"stop_at_percent": 100` turns it off. Output without usage data never stops the run (a log note says so), and an item that
finishes the queue ends the run normally whatever the usage. It is checked only between items, never inside one.
An attempt that ends because its `--max-budget-usd` ran out is a handover, not a failure: its
uncommitted changes become a handover commit and the same item runs again from them (at most twice; then the usual stop), and every
prompt tells the agent its budget and to commit before about nine tenths of it. A lock whose process is no longer running
(a run that crashed or was killed) is cleared with a message; a live run's lock, or one that cannot be read, still refuses.
A reused worktree whose branch is already merged into the base branch is fast-forwarded to it at the start, so new work
never starts from old code; a branch with unmerged work of its own is left as it is, with a warning naming how far the base
is ahead. A runner older than the installed auto-runner (Claude's `installed_plugins.json`) refuses to start and names the
installed copy's `auto_run.py`. The end of a run prints the suite's own totals line from its last independent run.
Before it commits, the agent runs the project's fast checks (`check_command`, else its lint and type checks), the tests
that cover its change and the end-to-end tests of every service or screen it changed; the runner's own `test_command` stays
the gate. A red suite reopens the item (its box is unticked, with a queue commit when the queue is tracked, so a red commit
is never left marked done), keeps any uncommitted changes as a handover, and gives it up to `review.max_fix_rounds` (default
2) fix rounds: the next iteration works the same item with the end of the failing output in its prompt, and does not use up
an iteration. After the last round the runner raises a blocker with that output (it shows on the central service when that
is on) and stops with reason `red`. A handover is a commit `wip(<ITEM>): unfinished <why> (<stamp>)` on the run branch
(`set_aside`), pushed with it, so whichever runner works the item next, here or on another computer, continues from it.
Content that holds a generated test credential is never committed: it goes to a local stash labelled
`auto-runner: <ITEM> unfinished ...` instead. Before the item's next attempt the runner undoes a `wip(<ITEM>)` commit at
the branch's tip (`git reset --soft`), or restores such a stash (also one a person made), and the prompt tells the agent to
continue from it; only the agent's files come back (never the queue, `.auto/` or shared folders), tracked changes as one
patch and new files one by one, and a stash that does not apply cleanly is kept for a person.
When the agent stops because a tool call was refused for lack of permission (read from the result's `permission_denials`,
or from refused tool results in the transcript), the item's status names the call, for example "needs permission for Bash:
corepack pnpm install: allow it in .claude/settings.json, or put the tool on PATH with path_prepend", and the log lists them. That holds only when the agent exited 0: when it exited non-zero (it was cut
off early, so the refusals may not be why it stopped) and nothing else explains the stop, the item goes back to queued with
"agent exited with <code>; retried next iteration" and the next iteration runs it again; two such exits in a row still
stop the run by the no-progress rule.
It also stops on a browser-acceptance failure, a STOP file, uncommitted leftovers, or two iterations without progress. Cost per iteration is recorded in `.auto/runs.jsonl` only when the CLI reports it; otherwise it is `null`.
Every run also appends to `.auto/events.jsonl` (schema `event.v1`, `bin/autoevents.py`), under a run id that is unique across people
and machines (`<YYYYMMDD-HHMM>-<6 hex>`; 0.17.0): run start (with branch, base, and who ran it: `user` and `account` from
`AUTO_RUNNER_USER` / `AUTO_RUNNER_ACCOUNT`, which the central service's runs page shows) and end (with exit code and reason), each item state change (from, to) and each independent test result, one line per event, never rewritten. `runs.jsonl` keeps its old shape.

Event types: `run_start`, `run_end`, `publish` (run-level, `item` null), `item_state`, `tests`, `blocker`, `browser`,
`findings`, `changelog`, `docs` (each about one item) and `friction` (decision record 0011), whose `item` is null for a
run-level friction (such as a refused start) or an item ID. A friction detail is exactly `{"kind", "subject"}`
(`autoevents.friction_detail(kind, subject='')`): `kind` one of `FRICTION_KINDS` (`permission_denied`, `gate_stop`,
`fix_round`, `retry`, `usage_stop`, `refused_start`, `central_error`, `plan_changes`), `subject` a fixed-vocabulary
string matching `^[A-Za-z0-9_.+:/-]{0,80}$` (for example `exit_1` or `http_409:/api/v1/tasks/N/heartbeat`; never
project content). `autoevents.signature(detail)` gives `kind` or `kind:subject`. The central service checks the same
rules.

The runner records friction during a run through one helper in `auto_run.run`, which emits a `friction` event at most once
per signature per item per run:

| Kind | Subject | When |
|---|---|---|
| `permission_denied` | `autoevents.friction_subject(call)`: the tool name (`Write`), or `Bash:<executable base name>` with env assignments and `cd … &&` steps skipped (`FOO=1 cd /a/b && pnpm i` → `Bash:pnpm`), else `Bash:other` (`"$(x)"`) | each tool call refused for lack of permission; never its arguments |
| `gate_stop` | `docs`, `changelog`, `browser`, `journey`, `suite`, `stale` | the docs gate, the changelog step, browser acceptance, a journey check or the suite (after its last fix round) stops the run, or an item is held for a stale artifact |
| `fix_round` | `suite`, `journey` | a red suite reopens the item, or a failed journey queues a fix item |
| `retry` | `exit_<code>` | the agent exited early after a refusal and the item is retried |
| `usage_stop` | `limit`, `budget`, `threshold` | a usage limit, the attempt's `--max-budget-usd`, or the plan-usage threshold stops the item or the run |
| `refused_start` | `newer_installed`, or the first failed preflight group | the runner refuses to start (also with `--dry-run`); `item` is null and the event is written to `.auto/events.jsonl` before the error, so the next push sends it. The refusal message is unchanged |
| `central_error` | `http_<status>:<route>` (e.g. `http_409:/api/v1/tasks/N/heartbeat`, `http_400:/api/v1/events`), or `unreachable` | a call to the central service failed: a task heartbeat (a 409 means the claim was lost: released, reassigned or expired), the end-of-run report, a wait note, or an event push that stopped (`error_status`) or skipped an event (`skipped_status`). `item` is null; the route is the request path with task numbers as `N` and no query string (`autotasks.error_route`). A failed end-of-run report is pushed at once. Since 0.23.0 |

The preflight groups come from `autosafe.preflight_groups(project, cfg, base)`, which returns `[(group, message)]` with one
fixed code per check: `worktree`, `runner_name`, `model`, `base_url`, `journey`, `central`, `env_file`, `path_prepend`,
`publish`, `principles`, `capability`, `no_commit`, `no_tests`, `settings`. `autosafe.preflight()` returns the same messages
without the groups.

`autotasks.beat`, `finish` and `wait_note` take an optional `on_error(status, route)` (status the HTTP code, or None when
the service could not be reached), called on every failed call; their return values are unchanged. `autodeliver.push`
returns `{sent, skipped, error, skipped_status, error_status}`: the HTTP status of the first event skipped in that call, and
of the failure that stopped it (None when nothing answered). `plan_changes` is counted by the central service, not the
runner.

## Modules (all stdlib, all unverified beyond fakes)
Every child process (the agent, the test command, git) is read as UTF-8, with an undecodable byte replaced rather than
fatal, and Python children are asked to write UTF-8 (`PYTHONIOENCODING`, unless the project sets it), so non-English text
such as `₹` never breaks a run on Windows.

| File | What it does |
|---|---|
| `bin/auto_run.py` | The runner (worktree, one ready item per iteration, independent test run, lock, STOP, events, blockers, delivery, `push_branch` before each item and `publish` of its own branch or a taken-over task branch at the end, draft when items stay open; `set_aside`/`restore_handover` wip commits; `mark_item_done`/`items_done_on_branch` trailers; `take_over_branch`). |
| `bin/queue.py`, `bin/autocfg.py`, `bin/autosafe.py` | Queue grammar and readiness (`*Depends on:*`, `*Slice:*`), config loader (v1 keys + v2 sections, `principles()`), safety preflight; `autosafe.utf8_console()` makes stdout/stderr write UTF-8 (undecodable characters replaced, streams without `reconfigure` skipped, never raises). Every command in `bin/` calls it first in its `__main__` block (`queue.py` does the same inline, as it must not import from its own folder), so every command writes UTF-8 to its console. |
| `bin/autoevents.py` | Append-only `.auto/events.jsonl`, schema `event.v1` (`RUN_TYPES` such as `publish` carry no item; `friction` may or may not; `friction_detail`, `friction_subject`, `signature`). |
| `bin/autoblock.py` | `blocker.v1` / `decision.v1`, lifecycle state machine, local store; `list [--state S]`, `answer ID --option/--text`, `view`. |
| `bin/autodeliver.py` | `Transport` interface, `LocalTransport`, `HttpTransport` (central service; `project_info()` reads `GET /api/v1/project`), event push to central, inbox writer, resume packets; `resume ID` rebuilds a packet. |
| `bin/autostale.py` | Artifact front-matter (`version`, `hash`, `built_from`, `status`), `scan`, `status`, `revalidate`; stale artifacts refuse a resume. |
| `bin/autoeval.py` | Evaluation harness: `run --benchmark ID --runner fake`, `validate`, `validate-results FILE`. |
| `bin/auto_init.py`, `bin/auto_report.py` | Init (preview/apply/remove, the `central` section and its repository check) and the status/log reports. **CI workflow** (decision 0012, Phase 5): when the project has no GitHub Actions workflow (`.github/workflows/*.yml` or `*.yaml`) and a test command was detected, init proposes and writes `.github/workflows/tests.yml` that runs it on every pull request (`ci_workflow(cmd)`: setup for npm, pytest, dotnet, go and cargo; another command gets a note instead), because the Supervisor merges only when checks pass. It is recorded in the manifest, so `--remove` deletes it while unedited. |
| `bin/autotasks.py` | Central tasks: `attachments N` (the same "Screenshots" block as `next` for task N, without claiming it; the screenshots live in the project's `.auto/run/tasks/<N>/attachments/`, not in the plugin; an unknown task prints `Not done: ...`, exit 1), `next` (claim my next task; plan or build; then its screenshots, listed by `GET /api/v1/tasks/<N>`, are downloaded with `GET /api/v1/tasks/<N>/attachments/<id>` (`HttpTransport.fetch`) to `.auto/run/tasks/<N>/attachments/<id>-<basename>`, the basename with anything outside letters, digits, `.`, `-` and `_` replaced by `_` so a name never leaves the folder or collides; a file already there is not fetched again; the paths are printed under "Screenshots" and `commands/auto.md` has the session open them; a failed download is printed as "Not downloaded" and never stops `next`; watch mode does not fetch them; unverified against the live service), `plan N` (post its plan), `start N` (an approved task → a request folder with `task.json`, which also carries the task's recorded `branch` and `pr_url` so the run continues that branch), `record` (a terminal task), `propose` (a terminal plan sent for approval on the page: `"approval": "page"`, stored as `plan_review`, claim kept in `.auto/run/tasks/<N>.json`), `list` (`plan_review` shown as "waiting for approval"), `decide N approve|changes|reject [--comment TEXT]` (`POST /api/v1/tasks/<N>/decide` as the signed-in person; prints "Task N: approved" / "changes requested" / "rejected", or "Not done: <the service's reason>" such as a missing comment or no plan waiting; another decision word is refused by the argument parser; unverified against the live service); and for the runner `adopt`, `current` (a resumed run keeps the task's newest claim: when `next` has re-claimed it, the claim in `.auto/run/tasks/<N>.json` wins and is written back to `current-task.json`; no `current-task.json` means no current task), `beat` (`lost` on 409), `finish` (report done/stopped); and for the watcher `check_claim(project, N, claim)` (heartbeat with state `queued`, never `building`, so the task stays Queued: `ok`, `lost` on 409, `TaskError` on any other refusal, `TransportUnavailable` when unreachable). Personal tokens only. Its console output is UTF-8 (`autosafe.utf8_console()` first in `__main__`), so a title or plan with `→` prints on a Windows cp1252 console; nothing sent to the service changes. |
| `bin/autocentral.py` | What init and join share: `default_token_env`, `repo_key` / `clean_url`, `remote_url`, `lookup` (`GET /api/v1/project`), `check`, `token_steps`; `status` (`python autocentral.py status`, `/auto-runner:central`): the read-only connection check with next steps, exit 0 when connected; and **sign in from Claude** (0.18.0, decision record 0008): `login start` asks the service for a code (sending `client_name`, `repo` and, when `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT` are both set and valid, `user` and `claude_account`; an unset identity sends neither) and shows it with the address, keeping the device code in a private file; `login wait` polls until the person approves or denies in the browser (10 minutes at most) and stores the personal token without printing it, on Windows in the user variable `AUTO_RUNNER_TOKEN` (set by PowerShell from the child's environment, never on a command line), elsewhere in `~/.config/auto-runner/credentials` (mode 600). **One-command setup** (0.20.0): `projects` lists the signed-in person's projects (`GET /api/v1/projects`), `sync` fetches origin and fast-forwards the base branch only when it is checked out, clean and has no commits of its own (anything else is reported, never changed), and `status` in a folder that is not a checkout says how to sign in, list and clone. The page links it prints point to My work and the project's Overview (0.22: the project page opens on its Overview; members, repository and machine tokens are under Settings). Tested against the real central app through its test client. |
| `bin/auto_join.py` | Join: repository URL from the central service, `git clone` into a folder you confirm, then init's preview. |
| `bin/autojourney.py` | Journey check: parses journeys, starts and stops the app, runs the click-through agent, validates `journey.v1`, writes fix items. |
| `bin/autodeck.py` | Journey deck: after each journey check, the passing journeys' screenshots replace their slides in `.auto/journeys/deck.html` (`deck.v1` in `deck.json`). |
| `bin/autoplan.py` | Headless planner (watch mode): `plan N` plans a central task this computer claimed. Prompt = `docs/planner-prompt.md` + principles + the task's screenshots + the task text (and the sent-back comment); the planner downloads the task's screenshots first (`autotasks.fetch_attachments`; a failed download is named in the prompt and never stops planning) and is told to open each path with Read, the section is left out when there are none; `claude -p` read-only with `models.planner`; a reply without Queue items is refused; spec check with one revision at most, findings still open go under **Needs a person**; then `autotasks.post_plan`. Planner and spec-check costs count against `watch.plan_budget_usd`: at the cap nothing is posted, the local claim file is dropped (the claim lapses after 2 hours) and the reason goes to `.auto/run/watch.json`. Tested with a fake claude and `fake_central.FakeTasks`; never against the live CLI (unverified). |
| `bin/autowatch.py` | Watch loop (watch mode, core): claims this person's next central task (`autotasks.take_next`), plans it (`autoplan.plan`) or builds an approved one (`autotasks.start`, then `auto_run.run` with `enqueue`), and reports each step on stdout and under `last` in `.auto/run/watch.json`. Idle: writes the heartbeat `.auto/run/watch.json` (`state`, `at`) and waits `watch.poll_seconds`. Stops on `.auto/STOP` (checked before every claim and while waiting; the file is left in place). A failed step (refused plan, crashed or stopped build, unreachable service) is reported, the loop waits one poll and carries on. A build that exited 0 but left its request folder `.auto/pending/<slug>/` in place only waited for an earlier branch to be merged: its outcome is `ok` and `waiting` (detail `waits for an earlier branch to be merged`), the line says "waiting" (not "done"), the heartbeat is `waiting_for_merge` and the loop waits one poll; a build that exited non-zero, hit the usage limit or raised keeps its failed outcome and pause even with the request still pending. Before claiming, the step looks at the first entry of `auto_run.pending_requests` only (`task-<N>-...` folders by number, so `task-9` before `task-10`, then the others by name; the runner uses the same order): when it is a task folder whose `task.json` claim equals the one in `.auto/run/tasks/<N>.json`, `autotasks.check_claim` beats it `queued` and the request is built again (`on_start(N, 'build')`, the build's `claude` through the meter, title from the first heading of its `plan.md`) without `autotasks.start`, `take_next` or `reopen_build`, and with the same waiting rule; a lost claim (409) deletes the claim file and the folder and the step claims as usual; any other refusal or an unreachable service fails the step (the loop says "could not claim a task" and waits one poll; the claim file and folder stay). Any other first entry is not resumed. When the resumed build only waits, the step claims the next task (`take_next`) at once, so planning goes on while a request waits: a `plan` answer is planned as usual and its outcome, when the plan is posted, is marked `waiting` (heartbeat `waiting_for_merge`, one poll); a failed plan keeps its failed outcome (`waiting_after_failure`, one poll); a `build` answer is only started with `autotasks.start` (its own pending folder, the task stays Queued; no `building #N` beat, no build) and later steps resume it once it is first in line, and if starting it fails the task is reopened; with no task, a build answer, or a claim the service refuses or cannot answer, the resumed build's waiting outcome is returned. A resumed build that ends done or failed claims nothing in that step. Guardrails: every `claude -p` it starts (planner, spec check, builds) adds its `total_cost_usd` to `.auto/run/spend-<local date>.json`, and before each claim a day at `watch.daily_budget_usd` pauses the loop until local midnight (a step already started finishes, bounded by its own plan or attempt cap); a usage limit in any output (`auto_run.usage_limit`) or a build exit 75 waits for the reset plus a minute, at most `auto_run.LIMIT_POLL_MINUTES`; the checkout lock `.auto/run/watch.lock` (`auto_run.Lock`, stale ones cleared) lets one watcher, so one build, run per checkout and a second one refuses to start (exit 2). Started by `/auto-runner:auto watch`. Presence: it sends `POST /api/v1/runners/beat` (personal token, label `<user>/<account>-claude-auto-runner`, the day's spend) with `watching` or `paused` before each claim, `planning #N` / `building #N` once a task is claimed, and `paused: daily budget reached` each poll during the daily-budget pause; between steps it obeys the reply's `control`: `pause` waits a poll at a time without claiming until resumed, `stop` ends the loop (exit 0, heartbeat `stopped`); `wake` (set by the Supervisor when a task is ready) means poll now. While idle the loop beats `watching` every `IDLE_BEAT` (30 s) inside the poll wait, so a `wake` (or a pause or stop) is seen within 30 s and the loop claims at once instead of after the whole `poll_seconds`; a wake is said once by the service and logged as `woken from the service`; a step already started always finishes; a failed beat is reported once and the loop carries on. Tested against the real central app (test client), `fake_central.FakeRunners` and the real runner with a fake claude and a fake clock; never against the live CLI or service (unverified). |
| `bin/autogates.py` | Supervisor gates (decision 0010; called by `autosupervisor.py`). **Policy file** `.claude/supervisor-policy.json`, keys `base` (target branch, non-empty string) and `restricted_paths` (list of patterns: `dir/` or `dir/**` for everything below, `*` and `?` inside a name); unknown keys or wrong types make it invalid. It is **read from the base ref, never the working tree**, through an injected reader `read(ref, path) -> (text, blob_sha)`; `load_policy` returns the policy and its blob SHA (for the audit rows). A missing, unreadable or invalid file gives no policy, and the plan is left for a person. `plan_gate(task, policy)` is pure and returns `approve`, `changes` (the planner can fix it) or `person`, with reasons. Five checks: the task is in `plan_review` (else person); the plan has queue items (else changes); "Needs a person" says `None.` (else person); every item has an *Acceptance:* line (else changes); the plan names no restricted path (else changes). **Merge policy** (optional key `merge`): `review` is `required` (default) or `none` (CI-only merging; a project opts in, and a person should decide that), `method` is `squash` (default), `merge` or `rebase`. `merge_gate(task, pr, checks, reviews, policy, merger, files)` is pure and returns `merge` or `refuse` with the head SHA, the method, the evidence and **every** failed reason. Six conditions: the PR is open, not a draft, not merged, and the task has no `merged_at`; it targets the policy `base`; CI has at least one check and all are `success`; an `APPROVED` review by someone other than the author on the current head SHA, with no later `CHANGES_REQUESTED` from the same reviewer (skipped when `merge.review` is `none`); no changed file is restricted; the author differs from the merger. No policy always refuses. **Queue order:** `order(tasks)` is pure and returns a list of `number`, `rank`, `reason` entries, best first, leaving out `done` and `rejected` tasks. Sort keys, in this order: the person's `priority` (lower first, none last, so the order never crosses a priority); how many other open tasks list the task in `depends_on` (more first); age (oldest `created_at` first); number. Tested in `tests/auto_runner/test_supervisor_gates.py`. **Autonomy:** `load_autonomy(read, base_ref)` reads `autonomy` from the base's `.claude/auto.json` (default `auto`; unreadable or unknown → `manual`); `gate_inputs` returns policy, sha, error and autonomy for a pass, with `default_policy` (base branch, only `.env*`, `*.pem`, `*.key` restricted) when an auto-mode project has no policy file; `plan_gate(task, policy, autonomy)` turns an open "Needs a person" into the reason `decided by the supervisor in auto mode: ...` (`needs_lines`) instead of `person`; `merge_gate(..., autonomy)` skips the identity condition when `merge.allow_self_merge` (default: true in auto) allows it and records `self_merge` in the evidence. |
| `bin/autoforge.py` | Forge adapter for the Supervisor (decision 0010). The `Forge` interface has `pr`, `changed_files`, `checks`, `reviews` (read-only) and `merge(pr, sha, method)` (squash, merge or rebase, only if the PR head is still `sha`). It has **no push, commit or branch-write method**, by design. `GithubForge(repo)` uses `urllib` against the GitHub REST API (pages of 100, up to 10 pages); the token comes from `AUTO_SUPERVISOR_GITHUB_TOKEN` and goes only into the Authorization header, never into a log line, error or `repr`. A failed call raises `ForgeError` (with the HTTP status). The opener is injectable; `FakeForge` is in `tests/auto_runner/fake_forge.py`. Tested in `tests/auto_runner/test_autoforge.py` with a recorded fake opener (no network). |
| `bin/autodeploy.py` | The Supervisor's **deploy step** (decision 0012, Phase 5), run by `autosupervisor.run_pass` after the merge step. In auto mode (the repository's `autonomy` and the central project's both auto) it reads the `deploy` section from `.claude/auto.json` on the base; with a `command`, and when the base head differs from the state's `last_deploy` (or that deploy only `started`), it records `started` (`POST /api/v1/supervisor/deploys`), runs the command in `ShellDeployer`'s detached worktree `<repo_dir>-deploy` at that SHA (the person's checkout is never touched), and checks `smoke_url`. Green: `done`. Red (non-zero exit, timeout, no 2xx): a fix task for the pool (`Fix the failed deploy of <sha>`, from `failed deploy of <sha> (supervisor)`, with the output's last lines), `failed` with its number, then `rollback_command` when set and `rolled_back`. Output sent anywhere is cut to its last 1500 characters, with the value of every TOKEN/SECRET/KEY/PASSWORD environment variable replaced by `***`. A bad `deploy` section is an error and deploys nothing. `run_pass` result key `deploys`; summary lines `deploy: <sha> deployed|failed|error`; a failed deploy makes the pass exit 1. Tested in `tests/auto_runner/test_autodeploy.py` and `tests/central/test_supervisor_e2e.py`. **Watch** (`autosupervisor.py watch [--every SECONDS]`, default 900): runs a pass for every project and again after the wait, until `.auto/STOP` exists in the folder it was started from (checked between projects and every 30 s). Projects come from `AUTO_SUPERVISOR_PROJECTS` (`slug=clone dir,slug=clone dir`: each project's policy, autonomy and deploy settings are read from its own clone and its GitHub `owner/repo` from that clone's `origin`), else the single `AUTO_SUPERVISOR_PROJECT` with `AUTO_SUPERVISOR_REPO` and `AUTO_SUPERVISOR_REPO_DIR`. One project's error is printed (`[slug] error: ...`) and the watch goes on; every summary line is prefixed with `[slug]`. Exit 0 when stopped, 2 without settings, 3 when the token is not a supervisor's. Tested in `tests/auto_runner/test_supervisor_watch.py`. |
| `bin/autosupervisor.py` | The Supervisor's pass (decision 0010); the **plan step** and the **merge step**; `run_pass(project, http, read, base_ref, forge, merger)` runs both. **Merge step:** `merge_step(project, http, read, base_ref, forge, merger)` looks only at `done` tasks that have a pull request URL and no `merged_at` (so a merged task is skipped), reads the PR, its files, checks and reviews through the `Forge`, runs `merge_gate`, and records through `POST /api/v1/supervisor/tasks/<n>/merge`: `refused` with every reason (a repeat of the same refusal is not written twice), or `decided` before the forge call and `done` or `failed` after it. The forge merges at the evaluated head SHA only, so a head that moved is refused by the forge and recorded as `failed`; a missing or invalid policy merges nothing. `task_autonomy(loaded, state, task)` gives the mode a task is decided in: `auto` only when the repository's `autonomy`, the central project's (`state["autonomy"]`) and the task (`manual` unticked) all say auto; plan_step sends it as `autonomy` with each decision and merge_step passes it to `merge_gate`. `plan_step(project, http, read, base_ref)` reads `GET /api/v1/supervisor/state`, loads the policy from the base, runs `plan_gate` on each `plan_review` task that has no supervisor decision on its current plan post, and posts `approve`, `changes` or `escalate` (the gate's `person`) with the reasons and the policy SHA to `POST /api/v1/supervisor/tasks/<n>/plan-decision`. **Round cap:** the service turns a `changes` into `escalate` once the task has two `supervisor.plan.changes` rows, so a plan is sent back at most twice. Re-running a pass never duplicates a decision. `http(method, path, body) -> (status, json)` is injected (no network in tests); a failed call is reported in the result and the pass goes on. **Command and entry point (`/auto-runner:supervisor`, `python bin/autosupervisor.py`):** runs one pass and prints a summary. It calls `GET /api/v1/supervisor/whoami` first and refuses to run, with a message and exit code 3, unless the role is `supervisor` (nothing else is called). Settings come from the environment only: `AUTO_SUPERVISOR_URL`, `AUTO_SUPERVISOR_TOKEN` (the supervisor account's token), `AUTO_SUPERVISOR_PROJECT` (slug), `AUTO_SUPERVISOR_REPO` (`owner/repo`) and `AUTO_SUPERVISOR_MERGER` (the GitHub login the merge runs as) are required; `AUTO_SUPERVISOR_GITHUB_TOKEN` is needed to merge; `AUTO_SUPERVISOR_BASE` (default `origin/main`) and `AUTO_SUPERVISOR_REPO_DIR` (default the current directory) say where the policy is read from (`git show` on the base ref). Tokens are never printed. **Exit codes:** 0 the pass ran clean, 1 it ran but a step reported an error, 2 a required setting is missing (no call made), 3 refused (not a supervisor token, or the service did not answer). **Order step:** `order_step(project, http)` (the third step of `run_pass`, result key `orders`) ranks the open tasks with `autogates.order` and sends only the ranks that differ from the state to `PUT /api/v1/supervisor/order` with a reason for each; the service writes `rank` and one `supervisor.order` audit row per change, so a pass that finds the order already right writes nothing. **Control step:** `control_step(project, http, read, base_ref, forge)` (the fourth step of `run_pass`, result key `controls`) reads CI on the base head through `forge.base_checks(branch)` (the policy `base`, else `AUTO_SUPERVISOR_BASE` without `origin/`) and calls `POST /api/v1/supervisor/runners/<id>/<pause|resume|wake>` (audit rows `supervisor.runner.<action>`). CI **red** (any check failed): it pauses every live runner that has no pause or stop pending. CI **green** (at least one check, all success): it resumes the runners the supervisor paused (`paused_by_supervisor` in the state), never a person's pause. No checks yet or some still running: nothing is paused or resumed. Not red: it wakes a runner whose state is `watching`, with no control pending, when its person has a task `take_next` could hand out (unclaimed, claimable status, every `depends_on` done), so that watcher polls at once (see `bin/autowatch.py`). A **silent** runner (no beat for two hours) is listed in the summary (`control: runner X is silent`) and never touched. A forge read that fails is reported and no runner is changed. `Forge.base_checks` is read-only like the other calls. Tested in `tests/central/test_supervisor_e2e.py` and `tests/auto_runner/test_supervisor_pass.py`. **Add a task** (0010, 6a): `autosupervisor.py add --title T --from TEXT --for EMAIL [--description-file F] [--plan F --items F] [--project SLUG]` posts `POST /api/v1/supervisor/tasks`; `--from` (what the task traces to) is required, the assignee's runner takes it, and a plan sent with it waits for a person (the supervisor never decides on a task it added). Needs only `AUTO_SUPERVISOR_URL`, `AUTO_SUPERVISOR_TOKEN` and a project; exit 0 added, 1 refused with the service's reason, 2 bad input or settings, 3 no answer or not a supervisor token. |
| `bin/autospec.py` | Spec check: a read-only reviewer `claude -p` checks a plan's queue items against the code and answers `spec.v1` findings. |
| `bin/autoupdate.py` | Update: `check` refreshes the marketplace and lists the changelog between the installed and the available version; `apply` runs `claude plugin update`. |
| `bin/autosetup.py` | Capability registry and `doctor` (read-only), `plan` / `apply` / `remove` for setup. |
| `bin/autobrowser.py` | Host policy (`check_base_url`), throwaway accounts, `redact` / `scan_diff`, `browser.v1` results and the Layer 1 stage, `finding.v1` and the Layer 2 explorer. |
| `bin/automail.py` | Stdlib SMTP sink bound to 127.0.0.1; messages stored as JSON under `.auto/run/mail/`. |
| `bin/autochange.py` | Changelog sentence rules, Keep a Changelog entries, the runner's changelog commit, `release` (SemVer proposal). |
| `bin/autohook.py` | Claude Code hooks, registered in `hooks/hooks.json`. `prompt` (UserPromptSubmit): in a project whose `.claude/auto.json` has `central.enabled`, prints one line reminding the session to plan new work with `/auto-runner:auto` and send it for approval with `autotasks.py propose`; nothing for other projects, for prompts starting with `/`, or when the config or the hook input cannot be read; always exits 0. A prompt of `/auto-runner:allow-edits` (command `commands/allow-edits.md`) instead calls `allow`. `guard` (PreToolUse, matcher `Edit\|Write\|MultiEdit\|NotebookEdit`): only with `"enforce_runner": true`, exits 2 with a message naming `/auto-runner:auto` for a path in the main checkout; allows `.auto/`, the runner's worktree, paths outside the project, any path when the project is a linked worktree, and a session with a live allow file; allows when the config or input cannot be read. `allow` writes `.auto/run/allow-<session_id>` (the time), valid for that session only, for 12 hours or until deleted. Tested with temp projects and a subprocess run; never in a live Claude Code session (unverified). |
| `bin/autodocs.py` | Doc map, `check` (mapped docs changed, relative links resolve, new symbols documented) and the stale-doc scan (`gate`). |

## Setup and doctor (unverified: fake installer only)
`/auto-runner:doctor` (`python bin/autosetup.py doctor --project DIR`) is read-only and lists each capability as ok, missing or optional-admin
with a reason and the repair: browser (an installed Edge or Chrome, else Playwright Chromium), the Playwright package, Linux browser
libraries (admin only, never a failure), the mail sink, the `.gitignore` line for `.auto/run/`, `CHANGELOG.md`, and the doc map.
`/auto-runner:setup` shows one plan (a diff per file, any download with its source and a size marked "estimate"), asks once, then `apply`
runs exactly those steps, checks each with the doctor and records them in `.auto/init-manifest.json` under `setup`; `remove` takes away only
those. A download cannot be undone. Setup never reads or writes secrets and only appends deny rules. When `acceptance.browser.enabled` is true and
the doctor reports a missing capability, the preflight refuses with "run /auto-runner:setup".

## Browser acceptance (unverified: fake browser runner only, no real browser)
**Host policy.** `acceptance.browser.base_url` must be local or a test host: `localhost`, `127.0.0.1`, `[::1]`, `*.localhost`, `*.test`,
or a host with a `staging`/`dev`/`test`/`qa`/`sandbox`/`preview` label before the top-level domain. Everything else, URLs with a user name and
non-http schemes are refused; the preflight names the key.

**Accounts.** For the roles on an item's `*Browser:*` line the runner makes one throwaway account per role (an address at `auto-runner.test`, a
random password) in `.auto/run/accounts.json`, which must be git-ignored (otherwise nothing is written), and passes them only to the test
process as `AUTO_TEST_<ROLE>_EMAIL` / `AUTO_TEST_<ROLE>_PASSWORD`. With `accounts.method: seed_command` that command creates them; with
`signup` the journeys sign up through the app (the sink catches mail). Generated values are redacted from every log and event, and an item
whose commits contain one stops with `secret_leak`. No third-party service is used.

**Layer 1 decides.** An item opts in with a line such as `  *Browser:* journeys: sign-in, checkout; roles: admin, buyer`. After a green
independent test run the runner runs `acceptance.browser.command`, which must write `.auto/evidence/<item>/browser/results.json`
(`browser.v1`: per criterion id, pass/fail/skip, duration, screenshot/trace/console/network artifacts). An invalid or missing file, a failed
criterion, a non-zero exit or all-skipped is red and stops the run; a pass adds an evidence class to the item (E1 local, E3 staging-like host).
No `*Browser:*` line or no section = no stage.

**Journey check after each slice** (unverified: fake app and fake click-through agent only). With
`"acceptance": {"journeys": {"enabled": true, "start_command": "..."}}` and a local or test `acceptance.browser.base_url`
(the preflight refuses either missing), a request may carry `journeys.md` next to `items.md` (written by `/auto-runner:auto`
from the plan's **Journeys** section): `- **J1 Name.** Slice 2. Roles: a, b.`, numbered steps, and an `*Expect:*` line. They
are checked when the request is added (one item letter per request, every journey naming one of its slices) and stored in
`.auto/journeys/<LETTER>.json`. When an item finishes the last open item of its slice, the runner starts the app with
`start_command` in the worktree, waits until `base_url` answers (`ready_timeout_s`, default 120), makes the journeys' role
accounts as in layer 1, and runs a separate `claude -p` that may only use Playwright MCP on that origin and write to
`.auto/evidence/<LETTER>-slice-<n>/journeys/`. It clicks through that slice's journeys and every earlier slice's and writes
`journey.v1` results: per journey and step, what was expected, what was seen, pass or fail, and a screenshot. The app is
always stopped afterwards. Results that are missing, invalid, without a screenshot per step or inconsistent stop the run
(reason `journey`), as does an app that never answers. A failed journey becomes a fix item in the same slice (the step,
expected vs seen, the screenshot) that runs next with an extra iteration; the slice is checked again after it. After
`max_fix_rounds` (default 2) failed rounds a blocker with the evidence is raised and the run stops. Events: type `browser`
with `layer: "journey"`. The click-through costs up to `max_budget_usd` per check (default $3).

**Journey deck** (unverified: fake click-through only, never opened on a real app's screenshots). After every journey check with
valid results, each journey that passed gets its slides replaced in `.auto/journeys/deck.html`: one self-contained page (no
network) with a title slide listing the journeys, then per journey a cover slide (name, slice, the *Expect:* line) and one slide
per step (the screenshot, the planned step, expected and seen). A journey that failed keeps the slides of its last pass, marked
with a red "Failing since <time>" banner (the failed step, expected and seen, when it was last checked) on its cover and step
slides and "failing" in the contents, until it passes again; one that never passed shows a cover with the banner and no
screenshots. `deck.json` records per journey `last_result`, `last_checked` and, while it fails, `failing_since`. Open it in a browser; arrow keys or Page Up/Down move between slides, and it prints one
slide per page. Screenshots are copied to `.auto/journeys/deck/<LETTER>/<Jn>/`, the state is `deck.json` (`deck.v1`); all of it
is under `.auto/`, which is not committed. Text is redacted like the journey log; screenshots cannot be, so journeys run on test
data only. A deck that cannot be written is reported and never stops the run.

**Layer 2 only advises.** With `exploratory: true`, after a passing Layer 1 a second `claude -p` explores through Playwright MCP limited to
the base URL's origin and may only write `finding.v1` files to `.auto/findings/new/`. Valid findings are stored with a proposed regression
item (never queued automatically); invalid ones go to `rejected/` with reasons. A finding never changes pass or fail.

## Changelog and release (unverified: no real agent has written a sentence)
An item may carry `  *Kind:* feature|fix|breaking|internal` (no line = internal; when `changelog.enabled` the line is required). With the
changelog on, the agent writes one user-facing sentence to `.auto/changes/<ITEM>.txt`; after a green item the runner checks it (one sentence,
10 to 200 characters, no item ids, links or generated secrets), appends it under `## [Unreleased]` (Breaking / Added / Fixed / Internal; an item
is never added twice) and makes one small changelog commit on the auto branch. A missing or invalid sentence stops with reason `changelog`.
`python bin/autochange.py release` proposes the SemVer bump and writes `.auto/release/<version>.md` with the notes and the commands a person
runs; it never tags, pushes or edits the changelog, and the deny rules then include `git tag`.

## Doc map and the docs gate
`docs/doc-map.json` (or `docs.map`) lists which docs describe which code:
`{"entries": [{"name": "...", "paths": ["glob"], "docs": ["file"], "pattern": "sql_table|route|regex", "stamp": false}]}`.
`python bin/autodocs.py check [--base REF] [--head REF]` fails when a mapped file changed without its docs, a changed Markdown file has a dead
relative link, or a newly added symbol (from `pattern`) is in none of the docs. For `stamp: true` entries a changed watched file makes the
doc stale until `python bin/autostale.py revalidate DOC --from a.py,b.py` is committed in the same range. After a green item, before the
changelog step, the runner runs this gate on the item's commits; a failure stops with reason `docs`. No map or `docs.check: false` = no gate.
This repository's own `docs/doc-map.json` is the working example: it maps the plugin's code to this README.

## Continuous integration (unverified until a pull request shows the check)
`.github/workflows/tests.yml` (workflow `tests`, job `tests`) runs `python -m pytest -q -n auto --durations=15` (in parallel, one
worker per CPU, with `pytest-xdist`; the 15 slowest tests are listed) on Ubuntu with Python 3.12 for every pull request to `main`;
not again on the push of the merge, since the pull request's run already passed. A pull request that changes only files under
`docs/plans/` or `docs/designs/` (which no test reads) passes the check without running the suite; any other change, other docs
included, runs all of it. It installs `requirements-dev.txt` and `auto-runner/central/requirements.txt`, has read-only
permissions, uses no secrets and cancels an older run of the same ref. No test launches a real browser, so it installs none.
`tests/auto_runner/test_ci_workflow.py` checks the file's text; whether GitHub runs it green has not been checked.

## Blocker file contract (unverified with a real agent)
Instead of guessing, the agent writes one JSON object per question to `.auto/blockers/new/<name>.json` with the `blocker.v1` content fields:
`question`, `why_needed`, `prevents`, `impact_if_delayed`, `impact_if_wrong`, `owner_role`, `class` (B1-B5), `options` (`[{label, tradeoffs}]`),
`recommendation`, and optionally `evidence`, `assumptions`, `downstream`, `due`, `blocking_artifact`, `parent_blocker`. The runner owns id, project,
task, run, state, history and timestamps. A valid file is stored OPEN in `.auto/blockers/<id>.json`, the item becomes `blocked (<id>)`, a blocker
event is written and the run moves to the next ready item. An invalid file goes to `.auto/blockers/rejected/` with a `.reasons.txt`; the item is
blocked with that message. `.auto/QUESTIONS.md` is a generated view; never edit it. Answer with
`python bin/autoblock.py answer ID --option LABEL [--text TEXT]`: the decision is saved as data (never run as a command) and waits for delivery.
Only RESUMED and CLOSED count as unblocked; "answered" is not "resumed". With no blocker file present the runner behaves as before.

## Delivery contract
Pull-based: the runner pulls answered decisions at start and between iterations, writes `.auto/inbox/<blocker>.v<N>.json` once per decision
version, and acknowledges only when it selects the item again; the next prompt then carries `.auto/resume/<id>.md` (blocker, decision, artifact
hashes, last evidence, failed attempts, ledger excerpt). Outages change nothing and are retried. The request/response shapes and state rules are in
`docs/delivery-contract.md`. Three implementations pass the same contract tests: `LocalTransport`, the in-memory fake in
`tests/auto_runner/fake_central.py`, and `HttpTransport(base_url, token, send=None, timeout=10)`, which talks to the central service
(`central/`) with stdlib `urllib`: no reply, a timeout, a 5xx or an unreadable reply raise `TransportUnavailable`; on ack 404 is `KeyError` and 409
`ValueError`; any other 4xx (bad token, wrong scope) raises `TransportRefused`. The token goes only in the `Authorization` header. The tests drive
it through the real app's Flask test client; it has not run over a real network.

With `"central": {"enabled": true, "url": "...", "project": "<slug>"}` in `.claude/auto.json` and a token (a personal token
from signing in, which works for every project you belong to, or a project token in `AUTO_RUNNER_CENTRAL_TOKEN` or the
variable named by `"token_env"`), the runner pulls and acknowledges answers through `HttpTransport` instead of `LocalTransport`, and events, blockers and pulls carry
`central.project` instead of the folder name; every call sends `?project=<slug>`, which a personal token needs and a project token must match. The token is removed from the environment of the
agent and the test command (every variable whose value is a token, under any name), added to the values redacted from events, and never written to a log, an event or a file. Without the
section, or with `enabled` false, nothing changes: answers stay local. This is proven with a fake `send` only (no real service).

With central on, the runner also sends its ledger there: before each pull and once more after `run_end`, `autodeliver.push()` posts the
lines of `.auto/events.jsonl` it has not sent yet (only events of `central.project`, up to 20 a request, waiting up to 30 seconds for each), and records how far it got in
`.auto/central-push.json`. A `raised` blocker event carries the blocker's content (`autoblock.content()`: question, options, recommendation,
impacts, assumptions, evidence), so a person can answer it on the service's blocker pages. An outage or a refused token stops the push with a
message and the next push starts again from the same line (the service drops duplicates by event id); an event the service will never take
(400, 409, 413) is skipped and its id kept in the cursor file. A push never stops a run. While central is on, answer blockers on the service:
`autoblock.py answer` saves a local decision the runner no longer reads. Proven through the real app's Flask test client (no socket), not over
a real network.

## Evaluation harness (no real-model run yet)
`eval/` holds a rubric, a smoke benchmark and seven benchmark briefs with held-out test descriptions kept out of the agent's view. A criterion passes
only on executable held-out checks; the runner's own approval never counts; a score with nothing to measure is null. The fake runner is evidence class
E0: it proves the wiring, not delivery quality. Real baselines are manual (`eval/BASELINE.md`, results format `baseline.v1`), record model versions
and variance, and no target scores are set before a baseline exists. The briefs have no executable checks yet, and two need a seed codebase the
harness cannot copy.

## What the plugin cannot enforce
- The deny rules are Claude Code permission settings. The plugin only checks they are written; it cannot stop a person or another tool from changing them, or a command written in a form the rules do not match.
- Without Claude Code's sandbox (see "Sandbox": native Windows, or `sandbox.enabled` false) it does not sandbox the machine: the agent runs with your user account's file access inside the worktree and anything your permission mode allows.
- A green suite shows the tests pass, not that the work is right. Read the diff (`git log <base>..<branch>`) before merging. Merging and pushing are always yours.
- Cost and time limits are per run flags, enforced by the `claude` CLI, not by the plugin.

## Uninstall
`claude plugin uninstall auto-runner`, then `claude plugin marketplace remove flexsin-claude-plugins`. In a project: `python bin/auto_init.py --remove` previews what init added; add `--apply` to remove it.
Remove a finished worktree with `git worktree remove <folder>` (unlink any shared folder junction first so the real one is never touched).

## Central sidebar (task 22)
The central service's pages now sit in a left sidebar (My work with its waiting count, Projects, Admin for admins,
theme switch and Settings); the top bar keeps the brand, the bell and the account menu. Project pages keep their
header and tabs. My work is two columns (waiting and done; progress and runners). A project's Tasks board has Recent activity and Runners below it. The Projects list shows those needing you first, with a marker. Every page section is a panel (task, blockers, runs, settings and admin pages share it). Settings (Account, My tokens) and Admin (Accounts, Notification log, Runner frictions) have section tabs. The Task page keeps its main and side columns, project settings keep their Members, Repository and Machine tokens tabs, and the signed-out pages keep their cards. Every page in `docs/designs` is covered.
