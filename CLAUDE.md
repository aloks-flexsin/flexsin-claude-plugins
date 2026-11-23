# flexsin-claude-plugins

Flexsin's Claude Code plugin marketplace. Today it holds one plugin, `auto-runner`: an unattended runner
(queue items, isolated git worktree, tests, event ledger, blockers, delivery). The central service and its
dashboard will live in `auto-runner/central/`.

## Layout
- `auto-runner/bin/` – the runner's Python modules (standard library only)
- `auto-runner/commands/` – slash commands; `auto-runner/docs/` – contracts (e.g. delivery-contract.md)
- `tests/auto_runner/` – pytest suite; `docs/doc-map.json` – which docs must change with which code

## Rules
- Run `python -m pytest -q` and keep it green before every commit. New behaviour gets tests.
- Tests never reach the network or live services; use fakes (see `tests/auto_runner/fake_central.py`).
- Config comes from environment variables only; never commit secrets, `.env` files or real customer data.
- When you change anything under `auto-runner/bin`, `commands` or `config.schema.json`, update `auto-runner/README.md` (the doc-map check enforces this).
- Never deploy or touch Vercel/Supabase; a person does that.
- This repo is separate from SaleEnable/SIVA: never copy its code, database, queue or docs here.

## Team git rules
- Every Claude member has its own name (for example `alok-claude`, `bhawish-claude`). Use it in branch names and as the
  author identity, never just "Claude". If you are unsure of your name, ask before creating a branch.
- Branch names: `<your-name>/<type>/<brief>`, type one of `feat`, `fix`, `hot-fix`, `chore`, `docs`, `refactor`, `test`.
  Example: `alok-claude-bhawish/fix/setup-test-windows-paths`.
- Every new request (feature, fix, anything) gets a new branch from the latest base: run `git fetch origin main` first,
  then `git switch -c <branch> origin/main` (no upstream to `main`).
- Commit, push and open a pull request from that branch. The PR target is always the base branch (`main`).
- Never push to `main`, never force-push, never merge your own pull request.
