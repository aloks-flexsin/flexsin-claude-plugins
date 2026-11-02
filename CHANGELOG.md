# Changelog

All notable changes to this project are written here (Keep a Changelog).

## [Unreleased]

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
