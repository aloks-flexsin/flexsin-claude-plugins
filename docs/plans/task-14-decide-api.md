# Plan: decide a plan through the API (central task #14)

Built under the owner's standing instruction to keep work moving while they are away (central has no way yet to
approve a plan except the signed-in page, which is what this task adds).

## Intent
A Claude session or a Supervisor signed in with a personal token can approve a plan, send it back with a comment, or
reject it, with exactly the rules the task page uses, so work does not stop while the owner is away. Every such
decision is recorded as made through the API and by whom. Success: `autotasks.py decide 4 approve` moves a waiting
task to approved, and its Activity reads "Alok approved the plan for this task (through the API)".
Out of scope: restricting API decisions to Supervisor accounts (decision record 0010 may add that later) and deciding
blockers through the API (they already have their own flow).

## Context (what exists to build on)
- `tasks.decide(db, project, row, account, decision, comment, moment)` already enforces: plan waiting (409), assignee or
  manager/owner/admin (403), comment required for changes (400), unknown decision (400); it resolves notifications.
- The page route `task_decide` records `audit` action `task.plan.<decision>`; `shell._sentence` turns it into words.
- API routes use `tokens.bearer_token(...)`, `_api_account()` (personal tokens only), `_api()` (TaskError → JSON with
  its status), `g.token_project`.
- Plugin: `autotasks.py` sub-commands `next|plan|start|record|propose|list` with `_call(transport, method, path, body)`;
  tests use `tests/auto_runner/fake_central.py`.

## Key decisions
- **One shared rule set:** the API calls `tasks.decide`, so the page and the API can never disagree.
- **Audit:** the same action names (`task.plan.approve|changes|reject`) with `"via": "api"` in the detail, so existing
  history keeps working; `shell._sentence` adds " (through the API)" when `via` is `api`.
- **Scope:** `ingest` scope on the bearer token (a decision is a write), personal tokens only, like the other task writes.

## Slices
1. **Decide through the API.** A personal-token call approves, sends back or rejects a waiting plan, and history says so.
2. **Decide from the terminal.** `autotasks.py decide N approve|changes|reject [--comment TEXT]`.

## Journeys
- **J1 Approve from the terminal.** Slice 2. Roles: developer.
  1. As developer, with a plan of yours waiting, run `autotasks.py decide <N> approve`.
  *Expect:* it prints that the task is approved; the task page shows Approved and the Activity says "(through the API)".

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Decision rules | `tasks.decide` | free | n/a | reuse | one rule set for page and API |

## End to end
- No new tables or columns. Personal tokens only; same permission checks as the page; audited.
- Docs: `auto-runner/central/README.md`, `auto-runner/README.md`, `auto-runner/docs/central-guide.md`, root `CHANGELOG.md`.

## Queue items
- [ ] **P1 Decide a plan through the API.** In `auto-runner/central/tasks.py` add `POST /api/v1/tasks/<N>/decide` (`tokens.bearer_token("ingest")`, `_api_account()`, `_api`): body `{"decision": "approve"|"changes"|"reject", "comment": "..."}`, calls `tasks.decide` with the token's account, records `audit` `task.plan.<decision>` with detail `{"number": N, "via": "api"}`, returns `{"task": public(row)}`; 404 for an unknown task. In `shell._sentence`, append " (through the API)" when the detail has `"via": "api"`. Update `auto-runner/central/README.md`.
  *Acceptance:* `tests/central/test_tasks_decide_api.py`: the assignee's personal token approves a waiting plan (status approved, audit row with `via: api`, Activity text ends "(through the API)"); a viewer's personal token gets 403; a task not waiting gets 409; "changes" without a comment gets 400; reject ends the claim; a project token (not personal) is refused; an unknown task gets 404.
  *Slice:* 1 Decide through the API
  *Kind:* feature
- [ ] **P2 `autotasks.py decide`.** Add `decide N approve|changes|reject [--comment TEXT]` to `auto-runner/bin/autotasks.py` (POST to the new route; prints "Task N: approved" / "changes requested" / "rejected", or "Not done: <reason>"), and the fake route in `tests/auto_runner/fake_central.py`. Update `auto-runner/README.md`, `auto-runner/docs/central-guide.md` (tasks section) and the root `CHANGELOG.md`, marked unverified until used live.
  *Acceptance:* tests in `tests/auto_runner/test_central_tasks.py` with `fake_central.py`: `decide N approve` prints approved and the fake task is approved; `changes` without `--comment` prints the refusal; an unknown decision word is refused by the argument parser; `python -m pytest -q` passes.
  *Slice:* 2 Decide from the terminal
  *Kind:* feature

## Spec check
One run: no findings.

## Needs a person
- None to deploy beyond the usual redeploy (no migration). Decide later whether only Supervisor accounts may decide
  through the API (decision record 0010).
