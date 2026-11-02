# 0002: Central-first project setup

Status: accepted (2026-11-02). Tested with the real central app through Flask's test client and with a fake `git clone`;
nothing has run against the live service or its database.

## Context

The runner uses the central service only when `.claude/auto.json` has an enabled `central` section, and `/auto-runner:init`
never mentioned it, so a new project ran in local mode without anyone deciding that. Nothing tied a central project to its
repository, so a token for one project could be set up in another project's checkout, and a newcomer had no way to go from the
central service to a working checkout.

## Decision

- **A project names its repository.** `project.repo_url` is required when a project is created and changed by owners and
  admins, with an audit row. Only `https://host/owner/repo`, `ssh://[user@]host[:port]/owner/repo` and `user@host:owner/repo`
  are accepted. Anything carrying a credential (`user:pass@`, any user in an https URL) is refused, and the refused value is
  neither echoed nor audited, since it may hold a pasted token. **The service never holds git credentials**: cloning uses the
  person's own git setup.
- **Schema change without a migration tool.** `schema.sql` stays `CREATE TABLE IF NOT EXISTS`. Columns added later are listed
  in `db.COLUMNS_ADDED`, and `init-db` adds each one a database lacks (`ALTER TABLE ... ADD COLUMN`, nullable). The check reads
  the columns from an empty `SELECT`, so it works the same on SQLite and Postgres. Projects that existed before show "not set"
  until someone fills in the URL.
- **A read API.** `GET /api/v1/project` answers slug, name and repo_url for a token with any scope. Whoever holds the token can
  already act for that project, and these three fields reveal nothing more.
- **Central is required by default; `--local` opts out.** Init without `--central-url` and `--central-project` and without
  `--local` refuses. A project that already has a `central` section, on or off, needs neither: init never changes that section.
  The alternative (central opt-in) was rejected because it keeps the silent local mode this decision is meant to end.
- **One token variable per project.** Init writes `token_env` = `AUTO_RUNNER_TOKEN_<SLUG>` (overridable), so several projects
  can be set up on one machine. The token is stored with PowerShell `Read-Host -AsSecureString`, so it is not echoed or kept in
  shell history, and it is never written to a file or passed on a command line.
- **The repository check.** When the token is set, init refuses (and writes nothing) unless the token is the named project's,
  the project has a repo_url, and `origin` is the same repository. https, ssh and scp forms compare as `host/owner/repo`, lower
  case and without `.git`. A refused token or an unreachable service also stops init: the check is meant to fail closed. While
  the token is not set yet, init writes the section and says the check runs next time.
- **Join.** `auto_join.py` (`/auto-runner:join`) reads repo_url, clones it into a folder the person confirms, then previews init
  there. It clones only https, ssh and scp URLs, after `--`, so a stored value can never become a git option or an `ext::`
  transport.

## Consequences and known limits

- **The live database needs the column** before the new code is deployed (a person runs it; see the central README):
  `ALTER TABLE project ADD COLUMN IF NOT EXISTS repo_url VARCHAR(500);`, or `python -m central init-db` against it.
- **Re-running init on a local project** now needs `--local` (or the central options).
- **Case-insensitive comparison:** paths are compared in lower case (right for GitHub). On a host where two repositories differ
  only in case, init would treat them as the same.
- **Mirrors and forks:** a checkout whose `origin` is a fork or a mirror is refused. Point `origin` at the project's repository,
  or change the project's repo_url.
- **Unverified:** no live run of init or join against the deployed service, and no real `git clone`.
