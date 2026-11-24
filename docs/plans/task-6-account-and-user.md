# Task 6: Account and User — the central service knows which Claude account each person runs with

## Intent
Today the central service identifies a runner only by the **person** (the central account behind the personal token).
The **Claude account** a person runs with (`AUTO_RUNNER_ACCOUNT`, entered during `/auto-runner:central` setup) and the
**username** (`AUTO_RUNNER_USER`) only exist in each computer's environment and in the free text of run events. Nobody
on the central page can answer "which Claude accounts does Alok use?" or "who is using the `bhawish` Claude account?".

Outcome: owners, admins and each person can see, on the central page, every (person, username, Claude account)
combination their runners use, including both real cases:
- one person/username with **several Claude accounts** (alok via `bhawish` and via `alok-pers`);
- one Claude account **shared by several people** (`bhawish` used by alok and by priya).

Success shows as: the Account page lists "Your Claude accounts"; the Runs page shows person, username and Claude
account as separate facts and can be filtered by Claude account; the members page shows each member's Claude accounts
and marks a shared one; the device sign-in page and My tokens show which username/Claude account a computer signed in for.

Out of scope: verifying that a Claude account really belongs to someone (Claude accounts are not visible to the service;
the pair is self-reported by the runner and attributed to the verified central account), billing per Claude account,
and the non-runner surfaces (`browser`, `desktop`, `cli`) which do not talk to the service.

## Context (what exists)
- `auto-runner/bin/autosafe.py` `runner_identity()` / `runner_name()` → `alok/bhawish-claude-auto-runner`; `RUNNER_NAME` regex.
- Run events: `run_start` detail carries `user` and `account` (plugin 0.17.0+); `central/ingest.py` stores `event.account_id`
  from the personal token; `central/runs.py` `summarise()` builds `who` = "alok via bhawish-claude-auto-runner".
- Tasks: `central/tasks.py` stores `claim_runner` (the free-text runner label from `bin/autotasks.py` `runner_label()`).
- Sign-in from Claude: `bin/autocentral.py` `login_start()` posts `client_name` and `repo`; `central/personal.py` `start()`
  stores them in `device_login`; `create_token()` names the personal token after `client_name`.
- Schema: `central/schema.sql` + `db.COLUMNS_ADDED` for columns added later (migration path for an existing database).

## Key decisions
1. **Where the identity lives.** Options: (a) columns on `account`; (b) a new table of observed pairs; (c) keep parsing
   event bodies at page time. **Choice: (b)** `claude_identity(id, account_id, username, claude_account, first_seen,
   last_seen, UNIQUE(account_id, username, claude_account))`. It is many-to-many by nature (both cases in the request),
   needs no setup screen, and is cheap to query; (a) cannot hold several, (c) is capped by MAX_EVENTS and slow.
2. **Trust.** The central account is verified (personal token); username and Claude account are self-reported. Pairs are
   only recorded for personal-token requests (never for project tokens, whose account is unknown), and shown as "reported
   by the runner". Values must match the runner's own rule (lower-case, `[a-z0-9][a-z0-9._-]{0,63}`); the service keeps
   its own copy of the pattern with a parity test against `autosafe.RUNNER_NAME`, as ingest does for event fields.
3. **When pairs are recorded.** On ingest of a `run_start` event, on device sign-in (new `user`/`claude_account`
   fields), and on task API calls by parsing the `runner` label they already send (one shared parser,
   `identities.split_runner`, also used by `ui.runner_label`). Older runners that send neither keep working; nothing is required.
4. **Display.** "Who" becomes person name (central) · `user` · Claude account, with the old string kept as a fallback for
   events with no signed-in account. A Claude account used by more than one central account in the project is marked
   **shared**.
5. **Same username claimed by two central accounts** is shown as a warning on the members page, not refused (see Needs a person).

## End to end
- Data/migration: one new table (idempotent `CREATE TABLE IF NOT EXISTS`); `device_login.runner_user`,
  `device_login.claude_account`, `personal_token.runner_user`, `personal_token.claude_account` via `db.COLUMNS_ADDED`.
  Existing rows stay NULL and display as "not reported". Rollback: the columns/table are additive; old code ignores them.
- Security: all pages behind the existing `project_access` / sign-in guards; members see Claude accounts only of their
  projects' members; values escaped by Jinja; no new secrets. Audit row when a new pair is first seen.
- Errors: invalid username/Claude account in a request → ignored for recording (request still succeeds), never a 500.
- Deployment: a person deploys; `init-db` adds the columns/table. Docs: `auto-runner/central/README.md`,
  `auto-runner/docs/central-guide.md`, `auto-runner/README.md`.

## Slices
- **Slice 1 Your Claude accounts.** Runs signed in with a personal token record (person, username, Claude account); the
  Account page lists the person's pairs and the Runs page shows person, username and Claude account separately.
- **Slice 2 The team's Claude accounts.** The members page shows each member's Claude accounts, marks shared ones and
  duplicate usernames; the Runs page filters by Claude account.
- **Slice 3 Sign-in and tasks carry the identity.** The runner sends username and Claude account when signing in and on
  task calls; the device approval page, My tokens and the task page show them.

## Journeys
- **J1 Two Claude accounts, one person.** Slice 1. Roles: developer.
  1. As alok, run the auto-runner once with Claude account `bhawish` and once with `alok-pers`, both signed in.
  2. Open Account.
  *Expect:* "Your Claude accounts" lists `alok` · `bhawish` and `alok` · `alok-pers`, each with a last-seen time.
- **J2 Runs show the Claude account.** Slice 1. Roles: developer.
  1. Open the project's Runs page after J1.
  *Expect:* each run shows Alok's name, username `alok` and its own Claude account.
- **J3 Shared Claude account.** Slice 2. Roles: owner, two developers.
  1. As alok and as priya, each run once with Claude account `bhawish`.
  2. As owner, open the project's Members page.
  *Expect:* both rows list `bhawish`, marked shared; filtering Runs by `bhawish` shows both people's runs and no others.
- **J4 Sign-in names the Claude account.** Slice 3. Roles: developer.
  1. With `AUTO_RUNNER_USER=alok` and `AUTO_RUNNER_ACCOUNT=bhawish`, run `/auto-runner:central` and start sign-in.
  2. Open the shown address and code.
  *Expect:* the approval page says the computer signs in for `alok` with Claude account `bhawish`; after approval My
  tokens and Account → "Your Claude accounts" show the same.
- **J5 Taking a task records the Claude account.** Slice 3. Roles: developer, owner.
  1. As alok with Claude account `alok-pers` (never used before), run `/auto-runner:auto` with no task so it claims one.
  2. As owner, open the project's Members page.
  *Expect:* alok's row now lists `alok-pers`, and the task page shows the claim as Alok (alok-pers account).

## Libraries
Nothing new: Flask, Jinja templates and the existing `ui.html` macros and `db.py` layer. No new packages.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Store/show person ↔ Claude account pairs | existing `db.py` + Jinja pages | free | repo's own | extend | small relational table; no external capability needed |

## Queue items

- [ ] **I1 Record Claude identities from run events.** Add table `claude_identity` (id, account_id → account ON DELETE CASCADE, username, claude_account, first_seen, last_seen, UNIQUE(account_id, username, claude_account)) to `auto-runner/central/schema.sql`, and a module `auto-runner/central/identities.py` with `NAME` (its own copy of the runner name pattern), `record(db, account_id, username, claude_account, moment)` (upsert; invalid or empty values are ignored and return None; an audit row `identity_seen` only the first time) and `of_account(db, account_id)` / `of_project(db, project_id)`. In `central/ingest.py`, after storing a `run_start` event that came with a personal token (a known `account_id`), call `record` with its detail `user` and `account`. Project-token events record nothing. Update the table count/list in `tests/central/test_skeleton.py`. Do not change any page or `auto-runner/bin/`.
  *Slice:* 1 Your Claude accounts
  *Acceptance:* `python -m pytest -q tests/central/test_identities.py tests/central/test_skeleton.py` passes: one person with two Claude accounts gives two rows, two people with one Claude account give two rows, a repeat updates `last_seen` only, a project-token event and an invalid name record nothing, the audit row is written once, the schema applies twice, and `identities.NAME.pattern == autosafe.RUNNER_NAME.pattern`; full `python -m pytest -q` green.
- [ ] **I2 Show Claude accounts on Account and Runs.** Add a "Your Claude accounts" section to `central/templates/account.html` (username, Claude account, first and last seen, or "None reported yet: your runners report it when they start"), fed from `identities.of_account`. In `central/runs.py` `summarise()` keep `user` and `account` as separate fields plus the signed-in name, and change the `who` macro in `templates/runs.html` to show person name · username · "Claude account X", keeping the old text for events without a signed-in account. Do not change how runs are grouped or their states.
  *Slice:* 1 Your Claude accounts
  *Acceptance:* `python -m pytest -q tests/central/test_identities_pages.py tests/central/test_runs.py` passes: after posting `run_start` events through `POST /api/v1/events` with a personal token for Claude accounts `bhawish` and `alok-pers`, `GET /account` lists both pairs and `GET /projects/<slug>/runs` shows the person's name, `alok` and each Claude account on its run; an event sent with a project token still shows the old "who" text; full suite green.
- [ ] **I3 Team Claude accounts on Members, shared marker, Runs filter.** On `central/templates/project_members.html` show each member's Claude accounts in this project (from `identities.of_project`), mark a Claude account reported by more than one member as "shared", and show a warning when one username is reported by more than one member. On the Runs page add a `?claude=<name>` filter as a second row of link chips under the existing outcome chips ("All Claude accounts" plus one chip per Claude account of the project); it keeps only runs with that Claude account. `claude` and `outcome` combine: the outcome chip links and their counts keep the current `claude` value, and the Claude chips keep the current `outcome`. Unknown values show no runs and no error. Viewers see the same lists; nothing is editable.
  *Slice:* 2 The team's Claude accounts
  *Acceptance:* `python -m pytest -q tests/central/test_identities_pages.py` passes: with alok and priya both reporting `bhawish`, the members page lists `bhawish` on both rows marked shared, a username reported by two members shows the warning, `GET /projects/<slug>/runs?claude=bhawish` shows both people's runs and not a third person's `other` run, `?claude=bhawish&outcome=finished` shows only the finished `bhawish` runs and its outcome chip links keep `claude=bhawish`, and a stranger still gets 404; full suite green.
- [ ] **I4 Runner sends its username and Claude account when it signs in.** In `auto-runner/bin/autocentral.py` `login_start()` add `user` and `claude_account` (from `autosafe.runner_identity()`, only when both are valid per `autosafe.runner_name()`) to the `/api/v1/device/start` body, next to `client_name` and `repo`. Task calls are not changed: they already send the identity as `runner` (`autotasks.runner_label()`). An unset identity sends neither field. Update `auto-runner/README.md` for the new fields.
  *Slice:* 3 Sign-in and tasks carry the identity
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_sign_in.py` passes with new tests that capture the `/api/v1/device/start` body through the fake transport: both fields present with a valid identity, both absent when `AUTO_RUNNER_ACCOUNT` is unset, `client_name` and `repo` unchanged; the docs check passes; full suite green.
- [ ] **I5 Device sign-in and My tokens show the username and Claude account.** Add columns `runner_user` and `claude_account` to `device_login` and `personal_token` (in `schema.sql` and `db.COLUMNS_ADDED`); `central/personal.py` `start()` stores them (validated with `identities.NAME`, invalid values dropped); `create_token()` gets explicit `runner_user` and `claude_account` parameters, and where `poll()` creates the token on delivery (after approval) it passes the row's values and calls `identities.record` for the approving account. Show them on `templates/device.html` ("signs in for alok with Claude account bhawish") and on `templates/my_tokens.html` per token ("not reported" when NULL). Do not change the task API or task pages.
  *Slice:* 3 Sign-in and tasks carry the identity
  *Acceptance:* `python -m pytest -q tests/central/test_personal.py` passes: a device start with `user=alok`, `claude_account=bhawish` shows both on `/device` after the code is entered, and after approval and a `POST /api/v1/device/poll` that delivers the token, on `/account/tokens` and in the Account page's "Your Claude accounts"; a start without them shows "not reported"; `init_db` on a database created before this change adds the four columns; full suite green.
- [ ] **I6 Task claims record the Claude identity.** Move the `<user>/<account>-claude-auto-runner` pattern from `central/ui.py` `runner_label()` into `identities.split_runner(label) -> (user, claude_account) or None` and have `runner_label()` use it (its output unchanged). In `central/tasks.py`, when `next`, `record` or `plan` comes with a personal token and a `runner` that `split_runner` accepts, call `identities.record` for the signed-in account. The task page keeps showing the claim through the existing `ui.runner` macro. Update `auto-runner/central/README.md` and `auto-runner/docs/central-guide.md` (who is identified how: person verified by the token, username and Claude account reported by the runner; docs marked unverified).
  *Slice:* 3 Sign-in and tasks carry the identity
  *Acceptance:* `python -m pytest -q tests/central/test_tasks.py tests/central/test_ui.py tests/central/test_identities_pages.py` passes: `POST /api/v1/tasks/next` with `runner=alok/bhawish-claude-auto-runner` and a personal token adds a `claude_identity` row that then appears on `/account` and on the project's members page; a `runner` that does not match records nothing; existing `runner_label` tests pass unchanged; the docs check passes; full suite green.

## Needs a person
- **Should a username belong to one central account?** Recommendation: not now: show a warning when two members report
  the same username (I3) and decide later whether to bind `AUTO_RUNNER_USER` to the central account and refuse others.
- Deploy the service and run `init-db` on the live database (a person; never the runner).
- After deploy, walk J1–J4 with real runners on two computers / two Claude accounts.
- Assumed: Claude account names are as entered in `AUTO_RUNNER_ACCOUNT`; the service cannot verify them.

## Verification
`python -m pytest -q` (all green) per item; the named tests above go through the real endpoints/pages of the Flask app on
a temp SQLite database and the runner's fake central transport. Live behaviour (J1–J4) is unverified until a person deploys.

## Spec check
Two rounds (the configured maximum).
- Round 1: I5 was too big and re-sent the identity task calls already carry as `runner`. Changed: I4 now only adds
  fields to device sign-in; I5 split into I5 (sign-in, My tokens) and I6 (task claims parse the existing `runner`
  label through one shared `identities.split_runner`, also used by `ui.runner_label`).
- Round 2: I5 assumed the token is made on approval; it is made on delivery in `poll()`. Fixed the item and its test to
  approve, then poll. I3's filter shape was open; now chips that combine with the outcome filter, with a combined test case.
- Still open: none from the reviewer. The round-2 fixes were not re-checked, because only two rounds are allowed.
