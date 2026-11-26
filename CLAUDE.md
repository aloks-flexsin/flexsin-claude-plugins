# flexsin-claude-plugins

Flexsin's Claude Code plugin marketplace. Today it holds one plugin, `auto-runner`: an unattended runner
(queue items, isolated git worktree, tests, event ledger, blockers, delivery). The central service and its
dashboard will live in `auto-runner/central/`.

## Layout
- `auto-runner/bin/` – the runner's Python modules (standard library only)
- `auto-runner/commands/` – slash commands; `auto-runner/docs/` – contracts (e.g. delivery-contract.md)
- `tests/auto_runner/` – pytest suite; `docs/doc-map.json` – which docs must change with which code

## Rules
- Before every commit, run only the tests the change can affect (for example `python -m pytest -q tests/central/test_tasks.py`)
  and keep them green; a change to docs or a config value needs at most the tests that read it. The full suite runs on
  every pull request (the `tests` check) and must pass before merging; run it locally only for a broad change (shared
  helpers, the schema, the runner's core). New behaviour gets tests.
- Tests never reach the network or live services; use fakes (see `tests/auto_runner/fake_central.py`).
- Config comes from environment variables only; never commit secrets, `.env` files or real customer data.
- When you change anything under `auto-runner/bin`, `commands` or `config.schema.json`, update `auto-runner/README.md` (the doc-map check enforces this).
- Every change to the plugin (anything under `auto-runner/` except its README and docs) adds one line under
  `## [Unreleased]` in `CHANGELOG.md` (the `version` check enforces this; label the PR `no-changelog` when nothing a
  user notices changed). A release is its own PR made with `python auto-runner/bin/autochange.py release --apply`, which
  moves Unreleased under the new version and bumps `plugin.json` and `marketplace.json` together; the `release`
  workflow tags `auto-runner-v<version>` when that PR merges.
- Deploys run only through the Supervisor's deploy step after a merge (decision record 0012); never deploy by hand
  from a session, and never touch Supabase data or run a destructive migration (a person does that).
- This repo is separate from SaleEnable/SIVA: never copy its code, database, queue or docs here.

## Team git rules
- Every Claude session has an identity made of three parts: the **real person** working (for example `alok`), the
  **Claude account** they are using (for example `bhawish`; one person may use several accounts) and the **surface**
  (`browser`, `desktop`, `cli`, or `auto-runner` for unattended runs). Use it in branch names and as the commit author,
  never just "Claude". Take it from the environment (`AUTO_RUNNER_USER`, `AUTO_RUNNER_ACCOUNT`) and the surface you
  run in; ask only when a part is missing.
- Branch names: `<user>/<account>-claude-<surface>/<type>/<brief>`, type one of `feat`, `fix`, `hot-fix`, `chore`, `docs`,
  `refactor`, `test`. Examples: `alok/bhawish-claude-browser/fix/login`, `alok/alok-claude-cli/feat/csv-export`; the
  auto-runner makes `alok/bhawish-claude-auto-runner/<type>/<brief>` from `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT`.
- Every new request (feature, fix, anything) gets a new branch from the latest base: run `git fetch origin main` first,
  then `git switch -c <branch> origin/main` (no upstream to `main`).
- Commit, push and open a pull request from that branch. The PR target is always the base branch (`main`).
- Never push to `main`, never force-push. Autonomy is the default (decision record 0012): a pull request is merged by the
  Supervisor, or by the session that opened it, once its `tests` check is green. A project or task in manual mode
  (`"autonomy": "manual"`, or the switch on the central service) waits for a person instead.
