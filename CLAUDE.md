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
- Never push, deploy, or touch Vercel/Supabase; a person does that.
- This repo is separate from SaleEnable/SIVA: never copy its code, database, queue or docs here.
