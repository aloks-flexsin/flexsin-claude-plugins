# Plan: a task that was partly built resumes instead of failing (watcher and runner)

## Intent
When a run on a central task stops partway (the session's command limit, a crash, a restarted computer), the next
start must carry on with the same branch. Today the watcher (and anyone who runs `auto_run.py --enqueue <slug>` again)
fails at once: `autotasks.start` writes the request folder again, the runner checks its items against the queue,
finds every ID already there ("R3: this ID is already used") and exits 1; the watcher then repeats this every poll and
the task never finishes without a person. Seen on task #18 on 2026-11-25. Success: starting the same task again
continues its open items on its existing branch and prints that it is resuming.
Out of scope: resuming a request whose items changed (that still refuses), and the watcher's other steps.

## Context (what exists to build on)
- `auto_run.run(..., enqueue=slug)`: reads `.auto/pending/<slug>/items.md`, validates it against the worktree queue
  with `queue.validate`, which refuses IDs that already exist.
- Reusing a worktree whose branch has unmerged commits already continues its items ("continuing its items").
- `autotasks.start` (re)writes `.auto/pending/task-<N>-<brief>/` on every build; `autowatch.step` calls it, then the build.
- `queue.parse_items(text)` -> `(id, checked, block)`.

## Key decisions
- **Resume when the request is already in the queue, exactly.** In `auto_run.run`, when `enqueue` is given and every
  item of the request is already in the worktree queue with the same text (ignoring the tick and status lines), it is
  the same request: say `request '<slug>' is already queued on <branch>: resuming it`, remove the pending folder
  (it would otherwise be added again on a later branch) and continue as a run without `enqueue`. If only some IDs
  exist, or the text differs, it still refuses as today. One rule in the runner covers the watcher and a person.

## Slices
1. **Resume a partly built task.**

## Journeys
- **J1 Resume after a stop.** Slice 1. Roles: developer.
  1. As developer, start a build of a task, stop it after the first item, then start the same task again
     (`autowatch.py`, or `auto_run.py --enqueue <slug>`).
  *Expect:* it prints that it is resuming, works the remaining items on the same branch and reports the task done.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Comparing items | `queue.parse_items` | free | n/a | reuse | same parser the check uses |

## End to end
- Plugin only; no service change. Docs: `auto-runner/README.md`, root `CHANGELOG.md`.

## Queue items
- [ ] **B1 Re-enqueuing a request that is already queued resumes it.** In `auto-runner/bin/auto_run.py`, before the `queue.validate` check for `enqueue`, compare the request's items (`queue.parse_items`) with the worktree queue's: when every request item ID is present with the same item text (the block without its tick and any status line the runner added), print `request '<slug>' is already queued on <branch>: resuming it`, call `autotasks.adopt(auto, auto / 'pending' / <slug>)` so `autotasks.current()` returns the newest claim that `autotasks.start` wrote (the one then beaten and reported), then delete `.auto/pending/<slug>/` and carry on as a run without `enqueue`. When another run holds the lock (`lock.held()`), do none of this: keep today's message ("a run is in progress ... checked and waiting"), return 0, and leave the pending folder and `current-task.json` untouched. When some IDs are present or the text differs, keep today's refusal. In `tests/auto_runner/fake_central.py`, let `/tasks/next` hand out a `building` or `queued` task with action `build` and a fresh claim, and handle `POST /api/v1/tasks/N/heartbeat` (409 on a stale claim; skip if task #18 already added it) and `POST /api/v1/tasks/N/report`, recording each call's claim and result. Update `auto-runner/README.md` and the root `CHANGELOG.md`.
  *Acceptance:* the fake's new routes have their own tests (a build task is handed out with a new claim; a stale-claim heartbeat gets 409; a report is recorded); tests in `tests/auto_runner/` with a fake claude: a run of a two-item request stopped after the first item, started again with the same `--enqueue` slug, prints the resuming line, removes the pending folder, works the second item on the same branch and exits 0; with the lock held by a live run, re-enqueuing the same request prints today's waiting message and changes neither the pending folder nor `current-task.json`; a request with one ID already in the queue and one new is refused as today; a request whose item text changed is refused as today; `autowatch.step` with the real `_default_build`, a fake claude and `fake_central.py`, on a task stopped after its first item and taken again (so `autotasks.start` rewrote the request with a new claim), prints the resuming line, removes the pending folder, works the second item on the same branch, returns outcome `ok`, and the fake central records the task as done with the new claim; `python -m pytest -q` passes.
  *Slice:* 1 Resume a partly built task
  *Kind:* fix

## Spec check
Four rounds; findings fixed (the claim call, the fake service's build and report routes, the held lock). Last run:
no findings.

## Needs a person
- Nothing to deploy. Restart the watcher after it ships.
