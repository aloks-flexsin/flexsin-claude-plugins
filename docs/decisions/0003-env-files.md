# 0003: Real configuration in unattended runs

Status: accepted (2026-11-02). Tested with a fake agent and scratch git repositories only.

## Context

Init writes `Read(./.env)` and `Read(./.env.*)` deny rules and preflight demands them. Unattended runs could still not test
against real configuration, but the rule was not the main cause: the rule stops only the agent's own reads, and a test
suite loads `.env` as an ordinary process. The cause was that a run works in a separate git worktree, and a gitignored
`.env` is not in it (`share_dirs` links folders, not files).

## Decision

- **`env_files`** (top-level list, default empty): at the start of a run each file is copied into the worktree. It is a
  copy, so nothing done in the worktree changes the project's own file. Preflight refuses an entry that is outside the
  project, missing, not ignored by git, or tracked, so a copied file can never be committed. Every value of 8 characters or
  more is redacted from iteration logs and events, which can reach the central service. Shorter values (ports, flags) are
  not secrets and would garble every log. Init proposes `.env` when it exists and is ignored.
- **The read rules stay by default.** Tests and the app get the values; the agent does not copy them into its transcript.
- **`safety.allow_env_read`** (default false; init `--allow-env-read`) is the owner's explicit opt-out. Init leaves out the
  read rules and preflight stops demanding them. The git rules (push, remote, reset --hard, clean, base checkout) are not
  affected.

## Consequences and known limits

- The agent runs with whatever the file grants. A `.env` holding production credentials lets an unattended run write to
  production data. Point `env_files` at a file with test settings where one exists.
- Redaction matches exact values only. A value the agent transforms (encodes, splits) is not caught.
- The copy stays in the worktree folder after the run (it is ignored there too).
