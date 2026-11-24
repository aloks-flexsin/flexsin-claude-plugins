# Task 6 follow-up: the surface, and the runner confirming its Claude account

## Intent
A person can be signed in to different Claude accounts on the CLI, Claude desktop and Claude in the browser at the same
time, so one person is not tied to one Claude account. Task 6 (I1–I6) records (person, username, Claude account) as
the runner reports it. This follow-up makes two changes:
1. Every recorded identity also says **where** it was used (the surface). Today that is always `auto-runner`;
   `cli`, `desktop` and `browser` can be added later without changing the table again.
2. The runner **checks the CLI's real sign-in** (`claude auth status --json`) before a run and says when it does not
   match the Claude account the person declared. The central page then shows whether a run's Claude account was confirmed.

Success: the Account and Members pages show the surface next to each pair, and a run whose CLI sign-in did not match is
marked "Claude account not confirmed" on the Runs page. Out of scope: reporting from desktop/browser sessions (they do
not talk to the service), and sending the Claude account's email to the service.

## Key decisions
1. **Surface storage.** Add `surface` to `claude_identity` and make it part of the unique key. A column can be added
   with `ALTER TABLE`, but an inline unique key cannot be changed that way, so `init_db` rebuilds a table made from
   I1's schema (copy rows with surface `auto-runner`, swap tables). An upgraded database therefore ends up with the
   same key as a fresh one.
2. **How the declared account is compared.** `claude auth status --json` gives the signed-in email, not the short name
   in `AUTO_RUNNER_ACCOUNT`. Options: (a) a new optional per-machine variable `AUTO_RUNNER_ACCOUNT_EMAIL` the email is
   compared with; (b) guess from the email's local part. **Choice (a)**: it doesn't guess, and it stays optional.
3. **Privacy.** The email never leaves the computer. The run_start event gets only `account_check`: `match`,
   `mismatch`, `not_set` (no `AUTO_RUNNER_ACCOUNT_EMAIL`) or `unknown` (CLI missing, not signed in, bad output).
4. **Warn, not refuse.** A mismatch prints a warning and is reported; it never stops a run (a person may switch
   accounts on purpose). Making it a refusal is a later choice (Needs a person).

## End to end
Data: one column plus the unique-key change (see decision 1). Security: no email sent; values validated. Errors: a
failing `claude auth status` gives `unknown`, never a crash; it has a 20-second timeout. Docs: `auto-runner/README.md`,
`auto-runner/central/README.md`, `auto-runner/docs/central-guide.md`. Deployment: a person.

## Slices
- **Slice 4 Surface on each identity.** Account and Members show `auto-runner` next to each pair.
- **Slice 5 The runner confirms its Claude account.** The run warns on a mismatch, and the Runs page marks runs whose
  Claude account was not confirmed.

## Journeys
- **J6 Surface shown.** Slice 4. Roles: developer.
  1. As alok, run the auto-runner once with Claude account `bhawish`.
  2. Open Account.
  *Expect:* "Your Claude accounts" shows `alok` · `bhawish` · `auto-runner`.
- **J7 Wrong CLI sign-in is flagged.** Slice 5. Roles: developer.
  1. Set `AUTO_RUNNER_ACCOUNT_EMAIL` to the email of Claude account `bhawish`, sign the CLI in to a different account, start a run.
  2. Open the project's Runs page.
  *Expect:* the run printed a warning when it started, and its row shows "Claude account not confirmed". With the matching sign-in the row has no mark.

## Libraries
Nothing new: the existing `claude` CLI (`claude auth status --json`), `subprocess`, Flask/Jinja.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Read the CLI's signed-in account | `claude auth status --json` (installed Claude Code CLI) | free | Anthropic terms | reuse | the runner already depends on the CLI |

## Queue items

- [ ] **I7 Record and show the surface of each Claude identity.** In `auto-runner/central/schema.sql` define `claude_identity` with `surface VARCHAR(32) NOT NULL` and the unique key (account_id, username, claude_account, surface). In `auto-runner/central/db.py` `init_db`, upgrade a `claude_identity` table that lacks `surface` (made from I1's schema) by rebuilding it: create the new table under a temporary name, copy every row with surface `auto-runner`, drop the old table, rename the new one (portable on SQLite and Postgres, in one transaction); a table that already has `surface` is left alone, so `init_db` stays idempotent. Do not use `COLUMNS_ADDED` for this column. `identities.record()` takes `surface` (default `auto-runner`, one of `auto-runner`, `cli`, `desktop`, `browser`; anything else records nothing) and upserts on all four columns; every caller from I1–I6 passes `auto-runner`. Show the surface as a column on the Account page's "Your Claude accounts" and next to each Claude account on the Members page. The shared marker and the duplicate-username warning keep working across surfaces. Do not change `auto-runner/bin/`.
  *Slice:* 4 Surface on each identity
  *Acceptance:* `python -m pytest -q tests/central/test_identities.py tests/central/test_identities_pages.py tests/central/test_skeleton.py` passes: on a database built with I1's old `claude_identity` table holding one row, `init_db` keeps that row (surface `auto-runner`), a second `init_db` changes nothing, and recording `auto-runner` and `cli` for the same person, username and Claude account then gives two rows; an unknown surface records nothing; `GET /account` and the members page show `auto-runner` next to the pair; full `python -m pytest -q` green.
- [ ] **I8 Runner checks the CLI's signed-in Claude account.** In `auto-runner/bin/autosafe.py` add `claude_signed_in(run=subprocess.run)`: it runs `claude auth status --json` with a 20-second timeout and returns the email when `loggedIn` is true, else None (any error, timeout or bad JSON → None). Add `account_check(env=None, run=...)` → `match` / `mismatch` / `not_set` / `unknown`, comparing case-insensitively with the optional `AUTO_RUNNER_ACCOUNT_EMAIL`. In `auto-runner/bin/auto_run.py`, before the `run_start` event, compute it once, print a warning line on `mismatch` (naming the variable, never the emails), and add `account_check` to the `run_start` detail. A mismatch never stops the run. `/auto-runner:central`'s status (`bin/autocentral.py`) shows the same check in its "Runner identity" line. Update `auto-runner/README.md` (the new variable, what is checked, that the email is never sent).
  *Slice:* 5 The runner confirms its Claude account
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_team_rules.py tests/auto_runner/test_central_status.py` passes with new tests that use a fake `run`: a matching email gives `match`, a different one `mismatch` and the warning, no variable gives `not_set`, a non-zero exit, timeout or bad JSON gives `unknown`, the `run_start` detail carries `account_check` and no email anywhere in the event or output; the docs check passes; full suite green.
- [ ] **I9 Runs page marks runs whose Claude account was not confirmed.** In `auto-runner/central/runs.py` `summarise()` read `account_check` from `run_start` (only the four known values; anything else is ignored), and in `templates/runs.html` show "Claude account not confirmed" next to the Claude account for `mismatch`, and nothing for `match`, `not_set`, `unknown` or older runners. Update `auto-runner/central/README.md` and `auto-runner/docs/central-guide.md` (surfaces, and what the confirmation means; docs marked unverified).
  *Slice:* 5 The runner confirms its Claude account
  *Acceptance:* `python -m pytest -q tests/central/test_runs.py tests/central/test_identities_pages.py` passes: a `run_start` posted through `POST /api/v1/events` with `account_check: "mismatch"` shows "Claude account not confirmed" on `GET /projects/<slug>/runs`, `match` and a missing field show no mark, an unknown value shows no mark and no error; the docs check passes; full suite green.

## Needs a person
- Deploy task 6 and this follow-up together if possible. `init_db` upgrades an older `claude_identity` table either way.
- Whether a mismatch should ever stop a run. Recommendation: keep it a warning.
- Set `AUTO_RUNNER_ACCOUNT_EMAIL` on each computer that should be checked (per person; never in a repository file).

## Spec check
Round 1: I7 could not change the unique key of a table made from I1's schema (`init_db` only adds columns), and the
test did not cover that upgrade. Changed: `init_db` rebuilds the old table and copies its rows. `record()` upserts on all
four columns. The acceptance test now runs on an old database. Round 2: no findings.
