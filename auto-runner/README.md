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
   checkout; stop it with `.auto/STOP`. Unverified against the live CLI and service.
   **No task given** (`/auto-runner:auto` alone): the runner takes your next task from the central service
   (`bin/autotasks.py`, 0.19.0, decision record 0007; needs the project connected and this computer signed in). A new task is
   planned the same way and its plan **waits for approval on the central page** while the runner moves on; an approved task
   becomes a request (`.auto/pending/task-<N>-<brief>/`) and runs on its own branch. A task typed in the terminal is also
   recorded there (`autotasks.py record`), or, when its plan should be approved on the central page instead, proposed
   (`autotasks.py propose --title T --plan FILE --items FILE`: stored as `plan_review` and claimed by this runner; once
   approved, `next` hands it back to build; `list` shows it as "waiting for approval"). While a run works on a task it beats the claim before every iteration, stops
   (reason `claim_lost`, exit 1) if an admin released or reassigned it, and reports it done (clean run, queue empty) or
   stopped at the end. Its `run_start` and `run_end` events name the task (`task`, 0.21.0), so the central task page and
   **My work** show the build's progress live. Tested against the real central app through its test client.
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
The agent can never push: the runner starts every agent with `--disallowedTools Bash(git push:*) PowerShell(git push:*)`,
whatever the project's settings say. Publishing is **on by default** (since 0.14.0): the runner itself, a plain Python step with
your own git credentials, pushes after a run that ends cleanly (exit 0, every commit passed the runner's own test run).
`"publish": {"push": false}` turns it off; `"pull_request": false` pushes without opening a pull request. Left at the default,
a repository with no remote simply is not published; `"push": true` written out with no remote is refused.

- `git push --porcelain <remote> refs/heads/<run branch>:refs/heads/<run branch>`: only the run's own branch (it must start
  with `<AUTO_RUNNER_USER>/<AUTO_RUNNER_ACCOUNT>-claude-auto-runner/` and not match `lanes.protected`), never the base branch, never with force, with `GIT_TERMINAL_PROMPT=0`
  so it fails rather than waits for a password. A run with no commits pushes nothing.
- Then `gh pr create --head <branch> --base <base>` with the title `<type>: <brief> (<user> via <account>-claude-auto-runner)` and the commit
  list as the body. A second clean run on the same unmerged branch pushes again; `gh` then reports that the pull request
  exists, which is recorded as `pr_error` and is harmless.
- When `gh pr create` fails and its output mentions GraphQL (blocked by a proxy, as in some cloud sessions), the runner opens
  the pull request with the REST API instead: `gh api repos/<owner>/<repo>/pulls -X POST -f title=... -f head=... -f base=...
  -f body=... --jq .html_url` through the same `pr_runner`, with owner and repository from `autocentral.repo_key` on
  `git remote get-url <remote>` (https and ssh forms; `--hostname <host>` for a host other than github.com). Its URL is
  recorded as `pr_url`; when it fails too (or the remote names no owner/repository), `pr_error` holds both errors. Any
  other `gh pr create` failure makes no REST call.
- The outcome is a `publish` event (`pushed`, `commits`, `pr_url` or `pr_error`, or `error`) in `.auto/events.jsonl` and,
  with central on, on the central service. A failed push or pull request is reported and never fails the run.
- A red run, or one stopped on a problem, publishes nothing. Merging, tagging and deploying stay with a person.

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
Each iteration's log (`.auto/logs/<run>-iter<N>.log`) holds the agent's full `stream-json` transcript, every tool call and its
result, redacted like the rest of the log, followed by the runner's test and stage output.
Claude's usage limit is recognised from the agent's final result (or, when the CLI left none, its last lines), never from the
work's own output; a result that is not marked as an error but carries a limit message (a soft cut-off) counts too when the
attempt committed nothing. The item's uncommitted changes are stashed (`git stash list` in the worktree; the queue and `.auto/` are
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
uncommitted changes are stashed and the same item runs again from them (at most twice; then the usual stop), and every
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
is on) and stops with reason `red`. A handover (a stash labelled `auto-runner: <ITEM> unfinished ...`, from a usage limit, a
red suite or a person) is restored by the runner itself before the item's next attempt, and the prompt tells the agent to
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

## Modules (all stdlib, all unverified beyond fakes)
Every child process (the agent, the test command, git) is read as UTF-8, with an undecodable byte replaced rather than
fatal, and Python children are asked to write UTF-8 (`PYTHONIOENCODING`, unless the project sets it), so non-English text
such as `₹` never breaks a run on Windows.

| File | What it does |
|---|---|
| `bin/auto_run.py` | The runner (worktree, one ready item per iteration, independent test run, lock, STOP, events, blockers, delivery, `publish` of its own branch). |
| `bin/queue.py`, `bin/autocfg.py`, `bin/autosafe.py` | Queue grammar and readiness (`*Depends on:*`, `*Slice:*`), config loader (v1 keys + v2 sections, `principles()`), safety preflight; `autosafe.utf8_console()` makes stdout/stderr write UTF-8 (undecodable characters replaced, streams without `reconfigure` skipped, never raises). Every command in `bin/` calls it first in its `__main__` block (`queue.py` does the same inline, as it must not import from its own folder), so every command writes UTF-8 to its console. |
| `bin/autoevents.py` | Append-only `.auto/events.jsonl`, schema `event.v1` (`RUN_TYPES` such as `publish` carry no item). |
| `bin/autoblock.py` | `blocker.v1` / `decision.v1`, lifecycle state machine, local store; `list [--state S]`, `answer ID --option/--text`, `view`. |
| `bin/autodeliver.py` | `Transport` interface, `LocalTransport`, `HttpTransport` (central service; `project_info()` reads `GET /api/v1/project`), event push to central, inbox writer, resume packets; `resume ID` rebuilds a packet. |
| `bin/autostale.py` | Artifact front-matter (`version`, `hash`, `built_from`, `status`), `scan`, `status`, `revalidate`; stale artifacts refuse a resume. |
| `bin/autoeval.py` | Evaluation harness: `run --benchmark ID --runner fake`, `validate`, `validate-results FILE`. |
| `bin/auto_init.py`, `bin/auto_report.py` | Init (preview/apply/remove, the `central` section and its repository check) and the status/log reports. |
| `bin/autotasks.py` | Central tasks: `next` (claim my next task; plan or build; then its screenshots, listed by `GET /api/v1/tasks/<N>`, are downloaded with `GET /api/v1/tasks/<N>/attachments/<id>` (`HttpTransport.fetch`) to `.auto/run/tasks/<N>/attachments/<id>-<basename>`, the basename with anything outside letters, digits, `.`, `-` and `_` replaced by `_` so a name never leaves the folder or collides; a file already there is not fetched again; the paths are printed under "Screenshots" and `commands/auto.md` has the session open them; a failed download is printed as "Not downloaded" and never stops `next`; watch mode does not fetch them; unverified against the live service), `plan N` (post its plan), `start N` (an approved task → a request folder with `task.json`), `record` (a terminal task), `propose` (a terminal plan sent for approval on the page: `"approval": "page"`, stored as `plan_review`, claim kept in `.auto/run/tasks/<N>.json`), `list` (`plan_review` shown as "waiting for approval"); and for the runner `adopt`, `current` (a resumed run keeps the task's newest claim: when `next` has re-claimed it, the claim in `.auto/run/tasks/<N>.json` wins and is written back to `current-task.json`; no `current-task.json` means no current task), `beat` (`lost` on 409), `finish` (report done/stopped). Personal tokens only. Its console output is UTF-8 (`autosafe.utf8_console()` first in `__main__`), so a title or plan with `→` prints on a Windows cp1252 console; nothing sent to the service changes. |
| `bin/autocentral.py` | What init and join share: `default_token_env`, `repo_key` / `clean_url`, `remote_url`, `lookup` (`GET /api/v1/project`), `check`, `token_steps`; `status` (`python autocentral.py status`, `/auto-runner:central`): the read-only connection check with next steps, exit 0 when connected; and **sign in from Claude** (0.18.0, decision record 0008): `login start` asks the service for a code (sending `client_name`, `repo` and, when `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT` are both set and valid, `user` and `claude_account`; an unset identity sends neither) and shows it with the address, keeping the device code in a private file; `login wait` polls until the person approves or denies in the browser (10 minutes at most) and stores the personal token without printing it, on Windows in the user variable `AUTO_RUNNER_TOKEN` (set by PowerShell from the child's environment, never on a command line), elsewhere in `~/.config/auto-runner/credentials` (mode 600). **One-command setup** (0.20.0): `projects` lists the signed-in person's projects (`GET /api/v1/projects`), `sync` fetches origin and fast-forwards the base branch only when it is checked out, clean and has no commits of its own (anything else is reported, never changed), and `status` in a folder that is not a checkout says how to sign in, list and clone. The page links it prints point to My work and the project's Overview (0.22: the project page opens on its Overview; members, repository and machine tokens are under Settings). Tested against the real central app through its test client. |
| `bin/auto_join.py` | Join: repository URL from the central service, `git clone` into a folder you confirm, then init's preview. |
| `bin/autojourney.py` | Journey check: parses journeys, starts and stops the app, runs the click-through agent, validates `journey.v1`, writes fix items. |
| `bin/autodeck.py` | Journey deck: after each journey check, the passing journeys' screenshots replace their slides in `.auto/journeys/deck.html` (`deck.v1` in `deck.json`). |
| `bin/autoplan.py` | Headless planner (watch mode): `plan N` plans a central task this computer claimed. Prompt = `docs/planner-prompt.md` + principles + the task text (and the sent-back comment); `claude -p` read-only with `models.planner`; a reply without Queue items is refused; spec check with one revision at most, findings still open go under **Needs a person**; then `autotasks.post_plan`. Planner and spec-check costs count against `watch.plan_budget_usd`: at the cap nothing is posted, the local claim file is dropped (the claim lapses after 2 hours) and the reason goes to `.auto/run/watch.json`. Tested with a fake claude and `fake_central.FakeTasks`; never against the live CLI (unverified). |
| `bin/autowatch.py` | Watch loop (watch mode, core): claims this person's next central task (`autotasks.take_next`), plans it (`autoplan.plan`) or builds an approved one (`autotasks.start`, then `auto_run.run` with `enqueue`), and reports each step on stdout and under `last` in `.auto/run/watch.json`. Idle: writes the heartbeat `.auto/run/watch.json` (`state`, `at`) and waits `watch.poll_seconds`. Stops on `.auto/STOP` (checked before every claim and while waiting; the file is left in place). A failed step (refused plan, crashed or stopped build, unreachable service) is reported, the loop waits one poll and carries on. Guardrails: every `claude -p` it starts (planner, spec check, builds) adds its `total_cost_usd` to `.auto/run/spend-<local date>.json`, and before each claim a day at `watch.daily_budget_usd` pauses the loop until local midnight (a step already started finishes, bounded by its own plan or attempt cap); a usage limit in any output (`auto_run.usage_limit`) or a build exit 75 waits for the reset plus a minute, at most `auto_run.LIMIT_POLL_MINUTES`; the checkout lock `.auto/run/watch.lock` (`auto_run.Lock`, stale ones cleared) lets one watcher, so one build, run per checkout and a second one refuses to start (exit 2). Started by `/auto-runner:auto watch`. Presence: it sends `POST /api/v1/runners/beat` (personal token, label `<user>/<account>-claude-auto-runner`, the day's spend) with `watching` or `paused` before each claim, `planning #N` / `building #N` once a task is claimed, and `paused: daily budget reached` each poll during the daily-budget pause; between steps it obeys the reply's `control`: `pause` waits a poll at a time without claiming until resumed, `stop` ends the loop (exit 0, heartbeat `stopped`); a step already started always finishes; a failed beat is reported once and the loop carries on. Tested against the real central app (test client), `fake_central.FakeRunners` and the real runner with a fake claude and a fake clock; never against the live CLI or service (unverified). |
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
`.github/workflows/tests.yml` (workflow `tests`, job `tests`) runs `python -m pytest -q` on Ubuntu with Python 3.12 for every pull request
to `main` and every push to `main`. It installs `requirements-dev.txt` and `auto-runner/central/requirements.txt`, has read-only
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
- It does not sandbox the machine: the agent runs with your user account's file access inside the worktree and anything your permission mode allows.
- A green suite shows the tests pass, not that the work is right. Read the diff (`git log <base>..<branch>`) before merging. Merging and pushing are always yours.
- Cost and time limits are per run flags, enforced by the `claude` CLI, not by the plugin.

## Uninstall
`claude plugin uninstall auto-runner`, then `claude plugin marketplace remove flexsin-claude-plugins`. In a project: `python bin/auto_init.py --remove` previews what init added; add `--apply` to remove it.
Remove a finished worktree with `git worktree remove <folder>` (unlink any shared folder junction first so the real one is never touched).
