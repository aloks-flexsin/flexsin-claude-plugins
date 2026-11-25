# Plan: the watcher builds a task that had to wait for an earlier branch

## Intent
When the watcher takes an approved task while this checkout's previous request branch is still unmerged, the runner
correctly says the request waits ("one request per branch") and exits 0. The watcher reports that as "done", the task
stays Queued under this runner on central, and `tasks/next` never hands it back, so it is never built until a person
runs it. Seen with task #21 on 2026-11-25. Success: once the earlier branch is merged, the watcher builds the waiting
request at its next step without a person, and its log says "waiting" rather than "done" meanwhile.
Out of scope: merging branches, and changing "one request per branch".

## Context (what exists to build on)
- `autowatch.step`: `take_next` -> `autotasks.start` (writes `.auto/pending/task-<N>-<brief>/`) -> `_default_build` ->
  `auto_run.run(project, enqueue=slug)`; the outcome is `ok` when the exit code is 0.
- `auto_run.run`: with an unmerged request branch, a pending request "waits" (`autotasks.wait_note`, the note on central)
  and stays in `.auto/pending/`; without `enqueue`, the oldest valid pending request starts once the branch is merged.
- `autowatch._loop` reports each outcome; `report`/heartbeat in `.auto/run/watch.json`.

## Key decisions
- **Waiting is its own outcome.** After a build, when the request folder is still in `.auto/pending/`, the step's
  outcome is `{'ok': True, 'waiting': True, 'detail': 'waits for <branch> to be merged'}` and the loop logs
  "waiting", not "done".
- **Pending requests come first.** At the start of each step, before `take_next`, when `.auto/pending/` holds a
  `task-*` request whose `task.json` claim this checkout still holds, the watcher calls the build for it (no new
  claim). It either starts (the branch is merged now) or waits again; only when nothing is pending does it claim a new
  task. This keeps one request per branch and never needs central to hand a task back.

## Slices
1. **A waiting task is built later without a person.**

## Journeys
- **J1 Waiting, then built.** Slice 1. Roles: developer.
  1. As developer, with an unmerged request branch in the checkout, let the watcher take an approved task.
  *Expect:* the log says the task is waiting for that branch. 2. Merge the branch. *Expect:* at its next step the
  watcher builds the waiting task and opens its pull request.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Starting a waiting request | `auto_run.run` without `enqueue` (oldest pending) | free | n/a | reuse | the runner already picks it up |

## End to end
- Plugin only. Docs: `auto-runner/README.md`, root `CHANGELOG.md`.

## Queue items
- [ ] **E1 A build that only waited is reported as waiting.** In `auto-runner/bin/autowatch.py` `step`: after a build that exited 0 without raising, when `.auto/pending/<slug>/` still exists, the outcome is `ok` True, `waiting` True, detail `waits for an earlier branch to be merged`; a build that exited non-zero, hit the usage limit or raised keeps today's outcome and pause even with the request still pending. `_loop` logs "waiting" for such an outcome (not "done"), writes the heartbeat `waiting_for_merge` and waits one poll before the next step. Update `auto-runner/README.md` (watch mode).
  *Acceptance:* tests with the watch tests' fakes: a fake build that returns 0 and leaves its request pending gives `waiting` True, the loop logs "waiting", writes `waiting_for_merge` and sleeps one poll (fake clock and sleep); a fake build that returns 1 and leaves the request pending gives `ok` False, logs "failed" and writes `waiting_after_failure`; a build that removes its request is reported "done" as today; `python -m pytest -q` passes.
  *Slice:* 1 A waiting task is built later without a person
  *Kind:* fix
- [ ] **E2 The watcher resumes a waiting request first.** In `auto-runner/bin/auto_run.py` make `pending_requests` sort `task-<N>-…` folders by their number, then other folders by name (so `task-9` comes before `task-10`, for the runner and the watcher alike). In `auto-runner/bin/autotasks.py` add `check_claim(project, number, claim, env=None, send=None)`: posts the task heartbeat `{'claim': claim, 'state': 'queued'}` (never 'building', so the task stays Queued); it catches `autodeliver.TransportRefused` itself and returns `'lost'` on status 409, raises `TaskError(str(e))` for any other refusal, and lets `TransportUnavailable` raise. In `tests/auto_runner/fake_central.py` add `FakeTasks.refuse_heartbeat` (an HTTP status; when set, task heartbeats answer it) for the 403 tests. In `auto-runner/bin/autowatch.py` `step`, before `autotasks.take_next`: look only at the first entry of `auto_run.pending_requests`; when it is a `task-*` folder whose `task.json` claim equals the one in `.auto/run/tasks/<N>.json`, call `check_claim`: on `'lost'` delete `.auto/run/tasks/<N>.json` and the folder and go on to `take_next` as today; otherwise call `on_start(number, 'build')` and `(build or _default_build)(project, slug, ...)` with `claude` wrapped by `meter.wrap(base, number, 'build')`, skipping `autotasks.start` and never calling `reopen_build`, and return the outcome (`action` 'build', `title` from the folder's `plan.md` first heading or empty; E1's waiting rule applies) without calling `take_next`. When the first entry is anything else, resume nothing and call `take_next` as today. Update `auto-runner/README.md`.
  *Acceptance:* tests: `pending_requests` lists `task-9-x` before `task-10-x` and other folders after them; `check_claim` against `fake_central.py` returns `'lost'` on 409, raises `TaskError` on 403 and lets an unreachable service raise, and the fake records state `queued`; with a matching first folder the step builds it without `autotasks.start` or `take_next`; with tasks 9 and 10 pending the step builds 9; when the first entry's claim differs (or it is a non-task request) nothing is resumed and `take_next` is called; on 409 the claim file and folder are removed and `take_next` is called; on a 403 the loop logs it and waits one poll and the claim file and folder stay; a resumed build's claude calls go through the meter; `python -m pytest -q` passes.
  *Slice:* 1 A waiting task is built later without a person
  *Kind:* fix
- [ ] **E3 Plan work continues while a request waits.** In `autowatch.step`, when E2's resumed build returns an outcome with `waiting` True, call `take_next` in the same step: a `plan` answer is planned as today; when the plan succeeds the step returns its outcome marked `waiting` True (the loop writes `waiting_for_merge` and sleeps one poll), and when it fails the step returns the failed plan's outcome unchanged (`ok` False: the loop logs "failed", writes `waiting_after_failure` and waits one poll); a `build` answer is not built now: it gets `autotasks.start` (its own pending folder, heartbeat `queued`) but no `on_start` and no build, and the step returns the resumed build's waiting outcome; later steps resume it through E2 once it is first in line. When `take_next` answers no task, the step returns the resumed build's waiting outcome unchanged; when `autotasks.start` raises for the new `build` answer, `reopen_build` is called for that task and the step still returns the resumed build's waiting outcome. After a resumed build that ends without waiting (done or failed), `take_next` is not called. Update `auto-runner/README.md` (watch mode) and the root `CHANGELOG.md`.
  *Acceptance:* tests with the watch tests' fakes: a resumed build that waits followed by a `plan` answer plans it and returns the plan's outcome with `waiting` True; followed by a `build` answer, that task gets its own pending folder through `autotasks.start`, with no build and no `on_start`, and the step returns the waiting outcome; when `take_next` answers no task the waiting outcome is returned unchanged; when `autotasks.start` raises for the new build answer, `reopen_build` is called and the waiting outcome is returned; a failed plan in that case logs "failed" and writes `waiting_after_failure`; a resumed build that ends done or failed does not call `take_next`; `python -m pytest -q` passes.
  *Slice:* 1 A waiting task is built later without a person
  *Kind:* fix

## Spec check
Sixteen rounds; findings fixed (pause after waiting, the rebuild skips start and reopen, failures keep their outcome, lost claims, ordering, the meter, plan work while waiting; split into three items).
Last run: no findings.

## Needs a person
- Nothing to deploy.
