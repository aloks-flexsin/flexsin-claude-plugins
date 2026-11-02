# auto-runner central service

A small Flask service where an unattended run's blockers can be seen and answered away from the terminal, and where the
runner picks up the answers. It implements `auto-runner/docs/delivery-contract.md` over HTTP; the runner's side is
`HttpTransport` in `auto-runner/bin/autodeliver.py`. Why it is built this way: `docs/decisions/0001-central-service.md`.

**Status: unverified.** Everything below is proven only by tests on SQLite through the Flask test client. Nothing has run on
Vercel, against a real Postgres, or with a real runner talking to a deployed service.

The runner sends its events here when `central` is on in its project's `.claude/auto.json` (see the plugin README,
"Delivery contract"); a blocker it raises then shows on the project's **Blockers** page with its question and options,
and an owner, manager or admin answers it there.

## Layout

| File | What it does |
| --- | --- |
| `__init__.py` | `create_app(config=None)`, security headers (strict CSP, nosniff, no-referrer), `GET /healthz` |
| `config.py` | Reads the environment variables below; the production check |
| `db.py`, `schema.sql` | DB-API layer (sqlite3 for `sqlite:` URLs, psycopg for `postgres:` URLs) and the ANSI SQL schema |
| `passwords.py`, `accounts.py` | scrypt hashing, password policy, account store |
| `auth.py`, `sessions.py` | Sign-in and sign-out, lockout, server-side sessions, CSRF |
| `admin.py` | Account administration and own password change |
| `access.py`, `projects.py` | Projects, members, the access guard, token pages |
| `tokens.py` | Per-project API tokens and the `bearer_token(scope)` guard |
| `ingest.py` | `POST /api/v1/events` |
| `blockers.py` | Blocker list, blocker page and the answer form |
| `deliveries.py` | Decisions and the delivery endpoints |
| `audit.py` | The audit log |
| `cli.py` | `python -m central init-db` and `python -m central create-admin --email X` |
| `api/index.py`, `vercel.json` | Vercel entry point; every route is rewritten to it |

## Environment variables

Listed in `.env.example`; no value is ever kept in the repo.

| Name | Meaning |
| --- | --- |
| `SECRET_KEY` | Random key, at least 32 characters in production. The service refuses to start without it. |
| `DATABASE_URL` | `sqlite:///path/to/central.db` locally, the Supabase `postgres://...` connection string in production. |
| `CENTRAL_ENV` | `production` turns on the production check (so does Vercel's `VERCEL_ENV=production`). |
| `PUBLIC_URL` | The service's `https://` address; required in production. |

## Local run and tests

From the repository root:

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests/central tests/auto_runner/test_delivery_contract.py
```

To run it locally (from `auto-runner/`, with `SECRET_KEY` and `DATABASE_URL=sqlite:///central.db` set):

```bash
python -m central init-db
python -m central create-admin --email you@example.com
flask --app "central:create_app()" run
```

The session cookie is `__Host-` and Secure; browsers that do not treat `http://localhost` as secure will drop it, so sign-in
may not stick over plain http. The API endpoints do not use the cookie.

## Sign-in and access

- Accounts are created by an admin (no self sign-up). Five failed sign-ins lock an account for 15 minutes.
- Sessions expire after 12 hours, or 2 hours idle. A password change or reset signs the person out everywhere.
- Global roles `admin` and `user`; project roles `owner`, `manager`, `developer`, `viewer`. Non-members get 404.
- Every sign-in (success and failure, never the password) and every account, token, membership and decision action is audited.
- A project has a repository URL (`repo_url`), required when an admin creates it and changed by owners and admins on the project
  page (audited as `project.repo_url` with the old and new value; a refusal as `project.repo_url.refused`, without the value).
  Only `https://host/owner/repo`, `ssh://[user@]host[:port]/owner/repo` and `user@host:owner/repo` are taken; a password, any
  user in an https URL, queries, fragments, other schemes and local paths are refused. The service never holds git credentials.

## Blockers

- `/projects/<slug>/blockers` (any member): the project's blockers, the ones waiting for an answer first.
- `/projects/<slug>/blockers/<blocker_id>` (any member): the question, why it is needed, the options with their
  trade-offs, the recommendation, assumptions and evidence, as the runner sent them (redacted at ingest), and the
  answers so far with their delivery state. A blocker raised by a runner older than this version shows only its id.
- Owners, managers and admins answer a waiting (OPEN or ESCALATED) blocker with one of its options and/or text. The
  answer is stored as a `decision.v1`, queued as a delivery and audited; the runner picks it up at its next pull, and
  the blocker shows RESUMED once the runner reports that it took the item up again. A blocker cannot be answered twice
  from the page.

## API

Send the project token as `Authorization: Bearer art_...`. A missing, wrong or revoked token gets 401; a token without the
needed scope gets 403.

| Endpoint | Scope | What it does |
| --- | --- | --- |
| `POST /api/v1/events` | `ingest` | One `event.v1` object or a list. Replies `accepted`, `duplicates`, `ids`, `duplicate_ids`. The same id with a different body gets 409, an unknown schema or bad event 400, a body over 256 KB 413, more than 120 requests a minute per token 429 (counted per process). The event's `project` must be the token's project. Run-level events (`run_start`, `run_end`, `publish`) have no item. Secrets in `detail` are redacted before storing. |
| `GET /api/v1/project` | any | `{"slug", "name", "repo_url"}` of the token's project (`repo_url` is null for a project made before the column existed). The plugin's init and join use it to check they are pointed at the right repository. |
| `GET /api/v1/deliveries/pending` | `pull` | `{"deliveries": [...]}`: the project's answers not yet acknowledged, oldest first; each is marked delivered. |
| `POST /api/v1/deliveries/<delivery_id>/ack` | `pull` | Marks a delivery acknowledged (a second ack changes nothing). Unknown or another project's id: 404; never delivered: 409. |

## Deployment (unverified; a person runs these steps)

None of these steps has been tried. Check each result before going on.

1. **Database.** Create a new Supabase project for this service only. Copy its Postgres connection string, then apply the
   schema from `auto-runner/`:
   ```bash
   DATABASE_URL="postgres://..." python -m central init-db
   ```
   (`pip install -r central/requirements.txt` first, for psycopg.) It is safe to run again. On a database made by an older
   version it also adds the columns listed in `db.COLUMNS_ADDED` (for now `project.repo_url`) and prints each one; the same
   change by hand is `ALTER TABLE project ADD COLUMN IF NOT EXISTS repo_url VARCHAR(500);`.
2. **Vercel project.** Create a new Vercel project from this repository with the root directory `auto-runner/central`.
   Set the environment variables for Production: `SECRET_KEY` (from
   `python -c "import secrets; print(secrets.token_hex(32))"`), `DATABASE_URL`, `CENTRAL_ENV=production` and `PUBLIC_URL`
   (the project's https address). Deploy, then open `PUBLIC_URL/healthz`.
3. **First admin.** From `auto-runner/` with `DATABASE_URL` pointing at the live database:
   ```bash
   python -m central create-admin --email you@example.com
   ```
   Sign in at `PUBLIC_URL/signin`.
4. **Project and token.** Create the project with its repository URL (its slug must equal the `project` the runner uses;
   set the repository URL on the page of any project made before it existed), add members, and create a token with the
   `pull` scope (and `ingest`, for when the runner sends events). Copy it; it is shown only once.
5. **Runner.** `/auto-runner:init` with `--central-url` and `--central-project` writes this and checks the repository; by hand,
   in the project's `.claude/auto.json`:
   ```json
   "central": {"enabled": true, "url": "https://<PUBLIC_URL host>", "project": "<slug>"}
   ```
   and set `AUTO_RUNNER_CENTRAL_TOKEN` in the environment the runner starts from. With more than one project, give each its
   own variable: add `"token_env": "AUTO_RUNNER_TOKEN_<NAME>"` to its `central` section and set that variable instead.
   Never put the token in `auto.json`.
6. **Check.** Start a run; its preflight refuses with a reason if the url, project or token is missing.

Known limits: see "Consequences and known limits" in `docs/decisions/0001-central-service.md`.
