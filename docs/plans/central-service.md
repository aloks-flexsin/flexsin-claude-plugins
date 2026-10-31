# Plan: auto-runner central service, Phase 4 (C11) part 1 — queued for an unattended run

## Context
Roadmap C11 needs a central service so blockers become visible and answerable away from the terminal, and so answers reach the
runner through the existing delivery contract. Today, only `LocalTransport` and the in-memory `tests/auto_runner/fake_central.py`
implement `auto-runner/docs/delivery-contract.md`. Hosting, accounts and access are decided already: its own Vercel project and
Supabase Postgres, its own accounts (no SSO), project members only, admins see all. This run builds the service and an HTTP
transport, with tests on SQLite and the Flask test client. It does no deploying and makes no live calls.

Current state: the queue (`.auto/queue.md`) has no items and there's no `.auto/pending/`, so this task uses the letter **C**.
`CLAUDE.md` rules apply: `python -m pytest -q` green before every commit, no network in tests, config from env vars only,
README updated when `auto-runner/bin` changes (doc map), no push/deploy, nothing from SIVA.

## Approach
- **Where the code lives:** the package `auto-runner/central/` (imported as `central`; tests put `auto-runner/` on `sys.path` in
  `tests/central/conftest.py`). The app factory is `create_app(config=None)`. Config comes only from `SECRET_KEY` and
  `DATABASE_URL` (plus optional tuning variables listed in `.env.example`).
- **Database:** `central/schema.sql` holds ANSI SQL with `CREATE TABLE IF NOT EXISTS` and explicit `REFERENCES`.
  `central/db.py` is a thin DB-API layer: `sqlite3` for `sqlite:` URLs (`PRAGMA foreign_keys=ON`) and `psycopg`, imported only for
  `postgres:` URLs, with `?`→`%s` placeholders. Tests never import psycopg.
- **Event validation, reused:** the ingest API holds its own copy of the `event.v1` field, type and evidence lists, so the
  deployed service doesn't depend on `bin/`. A parity test feeds real `autoevents.Ledger` output and asserts the lists equal
  `autoevents.FIELDS`/`TYPES`/`EVIDENCE`.
- **Sessions:** our own session table (only the hash of the random id is stored) instead of Flask's cookie session. The cookie is
  `Secure; HttpOnly; SameSite=Lax`. Tests use `base_url='https://localhost'` so the Secure cookie is sent back.
- **Third contract world:** `world` gets a new `'http'` param. `HttpWorld.transport(project)` is an
  `autodeliver.HttpTransport(url, token, send=...)` whose `send` routes through the Flask test client, with no socket.
  `answer()` ingests a `blocker raised` event and records a decision through the service's store. Every existing scenario in
  `test_delivery_contract.py` then runs unchanged against the real app.
- **Runner wiring:** the `central` config section already exists (`enabled`, `url`, `project`, "never a token"). The token comes
  from the env var `AUTO_RUNNER_CENTRAL_TOKEN`.

## Assumptions (open questions, decided this way)
- **Roles:** owner and manager may answer blockers and manage tokens. Owner (and admin) manages members. Developer and viewer
  can read only; this run has no developer-only actions.
- **Passwords:** at least 12 characters, checked against a small bundled common-password list.
- **Lockout:** 5 failures lock the account for 15 minutes. An unknown email gets the same response and timing as a wrong password.
- **Sessions:** expire after 12 hours or after 2 hours idle.
- **Ingest limits:** bodies over 256 KB get 413. The rate limit is 120 requests per minute per token, kept in process memory.
  It isn't shared across serverless instances; the decision record says so, and a shared limiter is a follow-up.
- **Duplicate events:** the same event id with the same body is accepted as a duplicate. The same id with a different body gets 409.
- **Redaction:** the ingest API redacts bearer/API-key/password/private-key/GitHub/AWS patterns in string values of `detail` before storing.
- **Out of scope:** the blocker-answering pages and other UI pages (later prompt), Phase 7 issues/scribe/alerts, and pushing
  events from the runner (only the HTTP pull/ack transport is wired).

## Risks
- `auto-runner/central/` sits inside the plugin folder, so it ships with plugin installs (small, harmless). Moving it out later is easy.
- Postgres behaviour is proven only by schema review and placeholder tests, never against a real Postgres. It's marked unverified.
- Each item is capped at $5. A big item may stop early, and the runner then leaves it unticked for review.

## Verification
- After every item: `python -m pytest -q` green (the runner's own test step).
- The end: `pytest tests/auto_runner/test_delivery_contract.py -q` shows each scenario for `local`, `central` and `http`.
- After the run: I review the commits and diff on the run branch and report what's proven and what's unverified.

## Queue items
- [ ] **C1 Central service skeleton, config and schema.** Create the Flask package `auto-runner/central/` (`__init__.py` with `create_app(config=None)`, `config.py` reading only env vars `SECRET_KEY`/`DATABASE_URL` and refusing to start without `SECRET_KEY` unless testing, `db.py` DB-API layer: sqlite3 for `sqlite:` with foreign keys on, psycopg imported lazily for `postgres:`, `?`→`%s` placeholders, `schema.sql` with ANSI `CREATE TABLE IF NOT EXISTS` and explicit `REFERENCES` for account, session, project, member, token, event, task, plan, blocker, decision, delivery, issue, audit, and `init_db()`), security headers on every response (strict CSP `default-src 'self'; frame-ancestors 'none'; ...`, nosniff, no-referrer), `GET /healthz`, `auto-runner/central/.env.example` (names only, no values), `auto-runner/central/requirements.txt` (flask, psycopg[binary]); add `flask` to `requirements-dev.txt`; `tests/central/conftest.py` puts `auto-runner/` on sys.path and gives an app fixture on a temp SQLite file. Do not change anything in `auto-runner/bin/`.
  *Acceptance:* `python -m pytest -q tests/central/test_skeleton.py` passes: schema applies twice on SQLite and creates all 13 tables, a row with a missing FK is refused, `schema.sql` has no SQLite-only syntax (no AUTOINCREMENT, no PRAGMA), placeholder conversion is tested, the app refuses to start without SECRET_KEY, and `/healthz` carries the CSP header; full `python -m pytest -q` green.
- [ ] **C2 Passwords, accounts and the first-admin CLI.** In `auto-runner/central/`: `passwords.py` (stdlib `hashlib.scrypt`, random 16-byte salt, parameters stored with the hash, constant-time verify, minimum 12 characters, bundled `common-passwords.txt` check), `accounts.py` store (create, find by email, case-insensitive unique email, roles `admin`/`user`, disabled flag), and `python -m central create-admin --email X` which asks for the password twice with `getpass` and has no password argument. No self sign-up route exists.
  *Acceptance:* `python -m pytest -q tests/central/test_accounts.py` passes: hash differs per salt and verifies, short and common passwords are refused, the CLI creates an admin from mocked `getpass` and argparse rejects `--password`, and no route matches `/signup` or `/register`; full suite green.
- [ ] **C3 Sign-in, sessions, lockout and CSRF.** Sign-in/sign-out pages with server-side sessions (only the sha256 of a random id stored; cookie `Secure; HttpOnly; SameSite=Lax`; a new id at every sign-in; 12-hour absolute and 2-hour idle expiry), lockout after 5 failures for 15 minutes with an injectable clock, the same answer for an unknown email, a CSRF token checked on every POST form (`400` without it), and audit rows for sign-in success and failure (email and reason, never the password).
  *Acceptance:* `python -m pytest -q tests/central/test_signin.py` passes: cookie flags, id rotation, expiry, lockout and time-based unlock, CSRF refusal, the audit rows exist and no audit or log text contains the password; full suite green.
- [ ] **C4 Account administration and own password change.** Admin pages and handlers: add an account (admin sets a first password), disable/enable, change role, reset password; everyone can change their own password (current password required). A password change or reset deletes all of that person's sessions; the last enabled admin cannot be disabled or demoted; each action writes an audit row. All forms carry CSRF.
  *Acceptance:* `python -m pytest -q tests/central/test_account_admin.py` passes: non-admins get 404 on admin pages, the last-admin guard holds, a change or reset signs the person out of every session, a disabled account cannot sign in, and audit rows exist; full suite green.
- [ ] **C5 Projects, membership and the access model.** Projects (slug, name), members with roles owner/manager/developer/viewer, and a `project_access(min_role)` guard: admins see every project, members see theirs, anyone else gets 404 (never 403). Owners and admins manage members; membership changes are audited. Minimal project list and members page only.
  *Acceptance:* `python -m pytest -q tests/central/test_access.py` passes: the role matrix for each role, a stranger gets 404 on another project's pages, an admin sees all, and membership audit rows exist; full suite green.
- [ ] **C6 Per-project API tokens.** Owners, managers and admins create tokens with scopes `ingest` and/or `pull`; the token (`art_` + 32 random bytes urlsafe) is shown once and stored only as its sha256 with a prefix for display; revoke; rotate (a new token, the old one revoked); a `bearer_token(scope)` helper for API routes (401 for a missing/bad/revoked token, 403 for a wrong scope); every action audited.
  *Acceptance:* `python -m pytest -q tests/central/test_tokens.py` passes: the database never holds the raw token, the token is shown once, revoke and rotate work, the scope check works, audit rows exist; full suite green.
- [ ] **C7 Event ingest API.** `POST /api/v1/events` (ingest scope) takes one `event.v1` object or a list. The service keeps its own copy of the field/type/evidence lists and validation; the event's project must equal the token's project slug; it is idempotent by event id (same body = counted as duplicate, different body = 409); unknown schema versions get 400; bodies over 256 KB get 413; 120 requests per minute per token get 429 (in-memory, injectable clock); secrets in string values of `detail` are redacted before storing. A `blocker` event with action `raised` creates or updates the blocker row (project, task = item, blocker id).
  *Acceptance:* `python -m pytest -q tests/central/test_ingest.py` passes, including a parity test that posts events written by the real `auto-runner/bin/autoevents.py` `Ledger` unchanged and asserts the service's lists equal `autoevents.FIELDS`, `TYPES` and `EVIDENCE`, and tests for duplicate, 409, 400, 413, 429, wrong project and redaction; full suite green.
- [ ] **C8 Decisions and delivery endpoints.** A service function `record_decision(project, blocker_id, account, option, text, context)` (owner/manager/admin only) writes `decision.v1` (the same shape as `autoblock.new_decision`), the next version, and a delivery row `pending` with `delivery_id = <blocker_id>.v<version>`, audited. `GET /api/v1/deliveries/pending` (pull scope) returns the token project's unacknowledged rows, oldest id first, moving pending/failed → delivered and counting attempts; `POST /api/v1/deliveries/<id>/ack` → acknowledged (a second ack changes nothing); an unknown id or another project's id gets 404; acking a row never delivered gets 409; a reply that fails to serialise marks the row `failed` with `last_error`. Implement exactly `auto-runner/docs/delivery-contract.md`.
  *Acceptance:* `python -m pytest -q tests/central/test_delivery_api.py` passes: state transitions, attempts, no cross-project rows, 404/409 cases, idempotent ack, and an audit row per decision; full suite green.
- [ ] **C9 HTTP transport in the plugin, proven as a third contract world.** Add `HttpTransport(base_url, token, send=None, timeout=10)` to `auto-runner/bin/autodeliver.py` (stdlib `urllib` by default; connection errors, timeouts and 5xx raise `TransportUnavailable` having changed nothing; 404 on ack raises `KeyError`, 409 raises `ValueError`; the token goes only in the `Authorization` header). In `tests/auto_runner/test_delivery_contract.py`, add `'http'` to the existing `world` fixture params with an `HttpWorld` whose `send` routes through the Flask test client of the real app on SQLite (no socket); do not copy or rewrite the existing scenarios. Update `auto-runner/README.md` and `auto-runner/docs/delivery-contract.md` (three implementations now).
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_delivery_contract.py` runs every `world` scenario for `local`, `central` and `http` and passes; `tests/auto_runner/test_http_transport.py` covers the error mapping with a fake `send`; full suite green.
- [ ] **C10 Runner uses the central service when configured.** When `.claude/auto.json` has `central.enabled` with `url` and `project`, `auto_run.py` uses `HttpTransport` with the token from env var `AUTO_RUNNER_CENTRAL_TOKEN` (never from the config file); enabled without the token or url makes the preflight refuse with a clear reason; otherwise behaviour is unchanged (`LocalTransport`). Update `auto-runner/README.md`.
  *Acceptance:* new tests in `tests/auto_runner/test_central_config.py` (transport choice, preflight refusal, the token is not read from config and is not written to any log or event); full suite green.
- [ ] **C11 Vercel entry point and production config check.** Add `auto-runner/central/api/index.py` (exposes `app = create_app()`), `auto-runner/central/vercel.json` (all routes to it), a `python -m central init-db` command that applies `schema.sql` to `DATABASE_URL`, and a startup check that refuses a non-HTTPS deployment flag or a default/short `SECRET_KEY` in production. No deployment.
  *Acceptance:* `python -m pytest -q tests/central/test_entry.py` imports the entry point with env vars set via monkeypatch, checks the `vercel.json` routes, and runs `init-db` against a temp SQLite URL; full suite green.
- [ ] **C12 Decision record, service README and deployment steps (unverified).** Write `docs/decisions/0001-central-service.md` (hosting on its own Vercel project and Supabase Postgres; own accounts and why not SSO now; access model; what data is kept, what is redacted and for how long; the in-memory rate-limit limitation) and `auto-runner/central/README.md` (what it is, local run, tests, env vars, API, and the deployment steps a person runs: create the Supabase database and apply the schema, create the Vercel project with root `auto-runner/central` and env vars, create the first admin, create a project token, set `central` in a project's `auto.json` and `AUTO_RUNNER_CENTRAL_TOKEN`), all marked **unverified**. Add a doc-map entry `auto-runner/central/**` → `auto-runner/central/README.md`, and a CHANGELOG `[Unreleased]` entry.
  *Acceptance:* `python -m pytest -q` green, including `test_docs_check.py` with the new doc-map entry; the README and decision record exist and contain the word "unverified" in the deployment section.

## Needs a person
- Create the Supabase database and apply `schema.sql`.
- Create the Vercel project (root `auto-runner/central`) and set `SECRET_KEY` and `DATABASE_URL`.
- Create the first admin on the live database (`python -m central create-admin`).
- Review the run branch and merge it.
- Publish the plugin update (version bump, marketplace).
