# Task 10: show queued tasks as Queued, keep their claim, and let a person close a task

## Intent
A task planned in the terminal (`autotasks record`), or an approved task made into a request (`autotasks start`), is
marked **Building** and claimed at once, even while it only waits in a runner's `.auto/pending/` (one request per
branch). Seen on 2026-11-24: tasks 7 and 8 showed Building for hours while task 6 ran. Their claims then expired,
because the runner only beats the task it works on, and task 7's run had to be started without its claim. Outcome:
- a waiting task shows **Queued** and keeps its claim until its run starts, then shows Building;
- the page says why it waits ("waits for branch X to be merged");
- an assignee, manager, owner or admin can **close** a task that should not be built, such as duplicate #9, with a
  reason. Today only a plan waiting for approval can be rejected.

Out of scope: the Supervisor's queue ordering (decision record 0010, 6a), and changing what a claim means for runners
that are working.

## Key decisions
1. **A new status, `queued`**, between approved/recorded and building. Options: (a) a status; (b) a flag on building.
   **(a):** the page, the filters and `take_next` already work by status.
2. **The claim on a queued task never expires by silence**, like a plan waiting for approval (`claim_live` already
   treats `plan_review` this way). An admin can still release it. That way waiting does not lose it, and a dead
   runner's task can still be freed by a person.
3. **Old runners keep working.** The plugin's `record` (0.20.0) creates a task and never says "now building". So
   `report` and `heartbeat` accept a `queued` task as if it were building, and a heartbeat moves `queued` to
   `building`. An old runner therefore still ends its task correctly.
4. **Closing** sets status `rejected` with a reason (the same final state as a rejected plan), clears the claim and
   writes an audit row. Allowed on `open`, `planning`, `changes_requested`, `approved`, `queued`; not on `building`
   (stop the runner first) or a final state.

## End to end
- Data: no new columns (`status` is text; the reason goes in `note`). Existing tasks are unchanged.
- Security: closing uses the same roles as approving a plan (`can_decide`: the assignee, and manager and above),
  behind CSRF and the confirm step, and is audited.
- Deployment: a person deploys the service. Runners that are not updated keep working (decision 3).
- Docs: `auto-runner/central/README.md`, `auto-runner/README.md`.

## Slices
- **Slice 1 Queued shown as Queued.** A recorded or started task shows Queued with its claim kept, and Building once its run starts.
- **Slice 2 Why it waits.** A queued task's page shows the runner's waiting reason.
- **Slice 3 Close a task.** The assignee or a manager+ closes a task with a reason from its page.

## Journeys
- **J1 Queued, then Building.** Slice 1. Roles: developer.
  1. While a run is working on one task, plan a second in the terminal (`/auto-runner:auto <task>`).
  2. Open the project's Tasks page.
  *Expect:* the second task shows Queued, and its claim is still held 3 hours later. When its run starts it shows Building.
- **J2 Why it waits.** Slice 2. Roles: developer.
  1. Open the queued task's page.
  *Expect:* it says it waits for the first task's branch to be merged.
- **J3 Close a duplicate.** Slice 3. Roles: admin.
  1. Open an open task that duplicates another, choose Close, enter "duplicate of #10", confirm.
  *Expect:* the task shows Rejected with the reason, its activity lists who closed it, and no runner takes it.

## Libraries
Nothing new: the existing Flask views, `ui.html` macros, `ui.confirm_or_page` and the audit log.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Task states and closing | `central/tasks.py` statuses, `can_decide`, `ui.confirm_or_page` | free | repo's own | extend | same pattern as plan approval |

## Queue items

- [ ] **Q1 Central: a queued status that keeps its claim.** In `auto-runner/central/tasks.py` add `queued` to `STATUSES` and `DESCRIBE` ("queued: waits for its run to start"), and show it in the "In progress" tab and on the task page (label Queued). `claim_live` treats `queued` like `plan_review` (alive while the claim id is set). `POST /api/v1/tasks` (record, approval "terminal") stores the task as `queued` instead of `building`. `POST /api/v1/tasks/<n>/heartbeat` accepts an optional `"state"`: `"queued"` moves an `approved` or `building` task the caller holds to `queued`; `"building"` (or a heartbeat with no state on a `queued` task) moves `queued` to `building`. `report` accepts a `queued` task as it accepts `building`. `take_next` never takes a `queued` task another runner holds. Admin release moves a `queued` task to `approved` (add it to the release mapping), so `take_next` can take it again. Do not change `auto-runner/bin/`.
  *Slice:* 1 Queued shown as Queued
  *Acceptance:* `python -m pytest -q tests/central/test_tasks.py` passes with new tests: a recorded task is `queued` and still claimed 3 hours later (fake clock), while a `building` one expires; a heartbeat with no state moves it to `building`; `state: "queued"` moves an approved task back to `queued`; `report` on a `queued` task with result done ends it as done; another runner's `next` skips it; admin release leaves it `approved` with no claim, and `next` then returns it; the Tasks page lists it under In progress as "Queued"; full `python -m pytest -q` green.
- [ ] **Q2 Runner: say queued when waiting, building when the run starts.** In `auto-runner/bin/autotasks.py`, `start N` sends a heartbeat with `"state": "queued"` after writing the request folder, and `record` needs no change (the service makes it `queued`). When a run takes a request on a new branch, `auto-runner/bin/auto_run.py` sends a heartbeat with `"state": "building"` for that request's task (from its `task.json`) before the first iteration. If it answers 409, the run says the claim was lost and carries on without a central task, instead of stopping. Update `auto-runner/README.md`.
  *Slice:* 1 Queued shown as Queued
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_central_tasks.py tests/auto_runner/test_runner.py` passes with new tests against `tests/auto_runner/fake_central.py` (and the real central app through its test client where the existing tests do): after `start`, the task is `queued`; after a run takes the request it is `building`; a 409 on that first heartbeat logs "claim lost" and the run still builds the items; the docs check passes; full suite green.
- [ ] **Q3 Why a queued task waits.** The heartbeat accepts an optional `"note"` (at most 300 characters, stored in `note`). When `auto_run.py` leaves a request waiting ("waits: <branch> is not merged into origin/main yet"), it sends a heartbeat with `"state": "queued"` and that sentence as the note for the request's task, if the request has a `task.json`. It never fails the run when the service cannot be reached. The task page shows the note under the status while the task is `queued`. Moving a task to `building` (the heartbeat from Q1/Q2, or `take_next`) clears a waiting note.
  *Slice:* 2 Why it waits
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/central/test_tasks.py` passes: a run with a second request that waits sends the note for the waiting task and keeps its claim; once that task moves to `building` its note is empty; the task page of that task shows "waits for" and the branch name; a 503 from the service is logged and the run goes on; full suite green.
- [ ] **Q4 Close a task from its page.** Add `POST /projects/<slug>/tasks/<n>/close` with a required reason (at most 500 characters), allowed by `can_decide` (assignee, manager and above, admins) for `open`, `planning`, `changes_requested`, `approved` and `queued`, refused for `building` (with "stop its runner first") and final states. It sets `rejected`, stores the reason in `note`, clears the claim, writes an audit row `task.close` and goes through `ui.confirm_or_page`. The task page shows a Close form for those states and roles only, and the activity list shows who closed it and why. Update `auto-runner/central/README.md` and `auto-runner/docs/central-guide.md` (docs marked unverified).
  *Slice:* 3 Close a task
  *Acceptance:* `python -m pytest -q tests/central/test_tasks.py tests/central/test_work_and_tasks.py` passes: the assignee and an admin close an open task with a reason (it becomes rejected, the reason shows, an audit row exists, `next` no longer takes it); a developer who is not the assignee gets 403 or 404 and no form; closing a building task is refused with the message; a missing reason is refused; the CSRF token is required; the docs check passes; full suite green.

## Needs a person
- Deploy the service (then run `init-db`; no schema change here, so it is only needed if earlier changes are not deployed yet).
- Close #9 as a duplicate of #10 once Q4 is live (or reject its plan when it comes up).

## Verification
`python -m pytest -q` per item, using the real central app through its test client and the runner's fake central.
Live behaviour is unverified until a person deploys.

## Spec check
Round 1 (2 findings, both fixed): releasing a queued task now leaves it `approved`, so it can be taken again, and
the test checks that. Moving to `building` clears the waiting note, also tested. The fixes were not re-checked: one
round, to keep the queue moving.
