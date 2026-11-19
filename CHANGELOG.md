# Changelog

All notable changes to this project are written here (Keep a Changelog).

## [Unreleased]

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
