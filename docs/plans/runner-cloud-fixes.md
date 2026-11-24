# Plan: three runner fixes found while building central tasks #1–#3 in a cloud session

Recorded from the terminal under the owner's standing instruction to keep work moving while they are away
(approved in chat, `autotasks.py record`); the PR is reviewed and merged the same way.

## Intent
The auto-runner keeps going in a Claude Code cloud session without a person repairing it by hand. Today three things
stop it: a resumed task holds an old claim and stops at once; an item whose agent exited early is wrongly marked
"needs permission" and skipped until a person clears it; and the pull request cannot be opened because `gh pr create`
needs GitHub's GraphQL API, which cloud sessions block. Success: a resumed run keeps its task, an interrupted item is
simply retried, and the run ends with a pull request link in cloud sessions too.
Out of scope: changing how claims, blockers or publishing work otherwise.

## Context (what exists to build on)
- `autotasks.py`: `take_next` saves each claim to `.auto/run/tasks/<N>.json`; the run's task is
  `.auto/run/current-task.json` (`adopt`, `current`, `beat`, `finish`); a 409 on a heartbeat deletes it ("lost").
- `auto_run.py`: after each iteration the item's state is set; `denied` (tool calls refused for lack of permission)
  leads to `blocked: needs permission ...` when nothing else explains the result, even when `claude` exited non-zero
  (for example at the usage limit); `code` is the agent's exit code.
- `auto_run.publish` opens the PR with `pr_runner(['gh', 'pr', 'create', ...])`; tests pass a fake `pr_runner`.
- Seen in this session: `HTTP 403: GitHub GraphQL is not available from Claude Code sessions; use the REST API`.

## Key decisions
- **Claim:** `autotasks.current()` prefers the claim in `.auto/run/tasks/<N>.json` when it differs from the one in
  `current-task.json` (the newer `next` wins) and rewrites `current-task.json` with it.
- **Early exits are retried, not blocked:** when `claude` exited non-zero, refused tool calls alone never mark the item
  blocked; the item goes back to queued with the detail "agent exited with <code>; retried next iteration". "Needs
  permission" stays for an agent that exited normally but could not finish because of refusals.
- **PR fallback:** when `gh pr create` fails and its output mentions GraphQL, open the PR with the REST API:
  `gh api repos/<owner>/<repo>/pulls -X POST -f title=... -f head=... -f base=... -f body=... --jq .html_url`, with
  owner and repository read from the push remote's URL with the existing `autocentral.repo_key`.

## Slices
1. **A resumed run keeps its task.**
2. **An interrupted item is retried.**
3. **The run opens its pull request in cloud sessions.**

## Journeys
- **J1 Resume after next.** Slice 1. Roles: developer.
  1. As developer, stop a run on a central task, run `autotasks.py next`, then start the run again.
  *Expect:* the run continues the task and does not stop with "released, reassigned or its claim expired".
- **J1b Early exit is retried.** Slice 2. Roles: developer.
  1. As developer, start a run where the agent exits early after a refused tool call.
  *Expect:* the item shows as queued with "retried next iteration" and the next iteration works on it.
- **J2 Cloud PR.** Slice 3. Roles: developer.
  1. As developer, in a cloud session, let a run finish an item.
  *Expect:* the run prints a pull request link.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Opening a PR without GraphQL | `gh api` (REST) with the same `gh` the runner already calls | free | MIT (gh) | reuse | same tool and login; only the endpoint changes |

## End to end
- No data or service changes; plugin only. Tests use fakes (`fake_central.py`, a fake `pr_runner`, a fake claude).
- Docs: `auto-runner/README.md` and the root `CHANGELOG.md`.

## Queue items
- [ ] **F1 A resumed run keeps its task's newest claim.** In `auto-runner/bin/autotasks.py`, `current(auto_dir)` returns the claim from `.auto/run/tasks/<N>.json` when it differs from the one in `current-task.json` and rewrites `current-task.json` with it; when `current-task.json` is missing it stays missing. Update `auto-runner/README.md`.
  *Acceptance:* tests in `tests/auto_runner/test_central_tasks.py` with `fake_central.py`: after `next` gives a new claim, `beat` uses the new claim and gets `ok`; with matching claims nothing changes; with no `current-task.json`, `current` returns None.
  *Slice:* 1 A resumed run keeps its task
  *Kind:* fix
- [ ] **F2 An item whose agent exited early is retried, not blocked.** In `auto-runner/bin/auto_run.py`, when the agent exited non-zero and the only explanation would be refused tool calls, set the item back to queued with the detail `agent exited with <code>; retried next iteration` instead of `blocked: needs permission ...`; keep "needs permission" for an agent that exited 0. The no-progress rule (stop when nothing changed twice) still applies. Update `auto-runner/README.md`.
  *Acceptance:* tests in `tests/auto_runner/test_runner.py` with a fake claude: exit 1 with refusals leaves the item queued with that detail and the next iteration runs it; exit 0 with refusals still blocks with "needs permission"; two such early exits in a row stop the run by the no-progress rule.
  *Slice:* 2 An interrupted item is retried
  *Kind:* fix
- [ ] **F3 Open the pull request with the REST API when GraphQL is blocked.** In `auto-runner/bin/auto_run.py` `publish`, when `gh pr create` fails and its output mentions GraphQL, call `gh api repos/<owner>/<repo>/pulls -X POST -f title=... -f head=... -f base=... -f body=... --jq .html_url` through the same `pr_runner`, with owner and repository taken from `autocentral.repo_key` on the push remote's URL (`git remote get-url <remote>`; it already handles https and ssh forms), not a second parser; record the URL, or both errors when it also fails. Update `auto-runner/README.md` and the root `CHANGELOG.md`.
  *Acceptance:* tests with a fake `pr_runner`: a GraphQL 403 followed by a REST success records the REST URL; a non-GraphQL failure makes no REST call; both failing records both errors; owner/repo come out right for `https://github.com/o/r.git` and `git@github.com:o/r.git` remotes (in `tests/auto_runner/test_publish.py`); `python -m pytest -q` passes.
  *Slice:* 3 The run opens its pull request in cloud sessions
  *Kind:* fix

## Spec check
One run, 2 findings, both fixed: F3 reuses `autocentral.repo_key` instead of a new URL parser, and slice 2 has its
own journey (J1b).

## Needs a person
- Nothing to deploy. Check J2 the next time a run finishes in a cloud session.
