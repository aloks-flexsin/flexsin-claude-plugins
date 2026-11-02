# 0001: Central service for the auto-runner

Status: accepted (2026-10-31). Deployment is **unverified**: nothing has run on Vercel or against a real Postgres.

## Context

The runner raises blockers when an unattended run needs a person. Today they are only visible in the terminal and the
project's `.auto/` folder, and answers reach the runner through `LocalTransport` (files on the same machine). People need to
see blockers and answer them away from the terminal, and the runner needs to pick those answers up. The delivery contract
(`auto-runner/docs/delivery-contract.md`) already fixes how answers are delivered; this decision covers the service that holds
them.

## Decision

### Hosting
- A Flask app in `auto-runner/central/`, deployed as **its own Vercel project** (root `auto-runner/central`, entry
  `api/index.py`) with **its own Supabase Postgres** database.
- Nothing is shared with any other product: no shared database, Vercel project, accounts or code.
- Configuration comes only from environment variables (`SECRET_KEY`, `DATABASE_URL`, `CENTRAL_ENV`, `PUBLIC_URL`; see
  `auto-runner/central/.env.example`). In production the service refuses to start without an https `PUBLIC_URL` or with a
  default or short `SECRET_KEY`.
- Tests run on SQLite through the Flask test client, with no network. `schema.sql` is plain ANSI SQL so the same file applies
  to Postgres; this is checked by review and tests of the SQL text only.

### Accounts: the service's own, not SSO (for now)
- Accounts are created by an admin; there is no self sign-up. The first admin is created with
  `python -m central create-admin --email X`, which asks for the password and never takes it as an argument.
- Passwords: scrypt with a random salt and the parameters stored with the hash, at least 12 characters, checked against a
  bundled common-password list.
- Five failed sign-ins lock an account for 15 minutes. An unknown email gets the same answer and roughly the same timing as a
  wrong password.
- Sessions are server-side: only the sha256 of a random id is stored. The cookie is `__Host-`, Secure, HttpOnly and
  SameSite=Lax, gets a new id at every sign-in, and expires after 12 hours or 2 hours idle. A password change or admin reset
  ends every session of that person.
- Every form carries a CSRF token, and every response a strict Content-Security-Policy.

**Why not SSO now:** the users are a small team, the service must run before any identity provider is chosen, and SSO would add
a dependency on an outside service and its configuration to every deployment and test. The account table keeps email as the
key, so SSO can be added later as another way to sign in to the same accounts.

### Access model
- Global roles: `admin` (sees and manages every project and account) and `user`.
- Project roles: `owner`, `manager`, `developer`, `viewer`. Owners and managers answer blockers and manage API tokens; owners
  (and admins) manage members; developers and viewers read only.
- Someone who is not a member of a project gets **404**, never 403, so the existence of a project is not revealed.
- The last enabled admin cannot be disabled or demoted.
- The runner uses per-project API tokens (`art_` prefix), shown once and stored only as their sha256, with the scopes
  `ingest` and `pull`, revocable and rotatable. The runner reads its token only from the environment
  (`AUTO_RUNNER_CENTRAL_TOKEN`, or the variable a project names in `central.token_env`), never from `.claude/auto.json`.

### Data kept and redacted
- Kept: accounts (email, password hash, role, disabled flag, lockout counters), session hashes, projects and members, token
  hashes and prefixes, `event.v1` events, blockers, `decision.v1` decisions, delivery rows, and an audit log.
- The audit log records sign-ins (success and failure, with the email and reason, never the password) and every account,
  token, membership and decision action.
- Before an event is stored, secrets in string values of `detail` are replaced by `[redacted]`: values under secret-looking
  keys (password, token, api key, cookie, ...), bearer/basic credentials, this service's tokens, sk-/GitHub/Slack/AWS keys,
  JWTs, private key blocks, passwords in URLs and `password=...` pairs. Redaction is pattern-based and cannot catch every
  secret, so the runner should still not put secrets in events.
- Retention: nothing is deleted automatically yet. Expired sessions stay in the table until a clean-up job exists (a
  follow-up); events, decisions and the audit log are kept indefinitely.

## Consequences and known limits
- **Rate limit per instance:** ingest allows 120 requests a minute per token, counted in process memory. On Vercel each
  serverless instance counts separately, so the real limit is higher. A shared limiter (for example in Postgres) is a
  follow-up.
- **Lockout as denial of service:** anyone who knows an email can lock that account for 15 minutes by failing its password
  repeatedly. This is the usual cost of per-account lockout and is accepted for a small team.
- **Event push and blocker pages (added 2026-11-02):** the runner sends its ledger to the service when `central` is on, and
  `raised` events carry the blocker's content (question, options, recommendation, impacts) so it can be answered on the
  blocker pages. That content is agent-written text: it is stored after redaction and shown escaped, never run.
- **Unverified:** the Postgres driver path, the Vercel deployment and the production config have only been tested with fakes.
