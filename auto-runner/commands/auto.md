---
description: Plan a task, then (once you accept the plan) queue it and run it unattended in a separate worktree
argument-hint: [<what to build>] [budget=5] [iterations=N]   (no task: take my next task from the central service; `watch`: keep taking them)
---

The user wants this done in autonomous mode: **$ARGUMENTS**

**If the arguments are exactly `watch`**, follow "W. Watch mode" and stop there.
**If no task is given** (only `budget=`/`iterations=` or nothing), follow "0. My next task from the central service" and
stop there. Otherwise follow steps 1 to 3 in order. Do not skip the plan, and do not start the run before the plan is accepted.

## W. Watch mode (`/auto-runner:auto watch`)
The watcher takes this person's central tasks one after another with no one at the terminal (decision record 0009): it
plans new tasks headlessly (the plan waits for approval on the central page) and builds approved ones. It needs the
project connected and this computer signed in (`/auto-runner:central`).
1. Start it in the background (Bash tool with `run_in_background: true`):
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autowatch.py" --project "${CLAUDE_PROJECT_DIR}"`.
   If it prints "Not started: another watcher holds .auto/run/watch.lock", one is already running on this checkout (only
   one build runs per checkout): tell the user and stop. If it prints "Not started" for another reason, show it (usually:
   run `/auto-runner:central` first) and stop.
2. Tell the user, briefly: what it does each step; the guardrails (each plan is capped by `watch.plan_budget_usd`, default
   2 USD; all of its `claude -p` calls together by `watch.daily_budget_usd` per local day, default 20 USD, after which it
   pauses until local midnight; at Claude's usage limit it waits for the reset); where to look (`.auto/run/watch.json` for
   its state and last step, `.auto/run/spend-<date>.json` for today's spend, the project's Tasks page for plans to
   approve); and how to stop it: create `.auto/STOP` (and delete it before starting it again).
3. Do not start the runner yourself while it watches, and do not merge, push or deploy anything it builds.

## 0. My next task from the central service (no task given)
Tasks live on the central service (decision record 0007); this needs the project connected and this computer signed in
(`/auto-runner:central`). Repeat until a run is started or nothing is left:
1. Run `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" next --project "${CLAUDE_PROJECT_DIR}"`. If it says "Not done",
   tell the user why (usually: run `/auto-runner:central` first) and stop. If there is no task waiting, say so and stop.
2. **Action: plan** (a new task, or one sent back with a comment to address). Plan it exactly as step 1 below describes
   (plan mode, the same sections, Reuse check, the spec check), using its description (and the comment) as the task.
   Do **not** ask the user to accept it: the approval happens on the central page. Write the whole plan to
   `.auto/run/tasks/<N>-plan.md` and only its Queue items (verbatim, no headings) to `.auto/run/tasks/<N>-items.md`, then run
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" plan <N> --plan .auto/run/tasks/<N>-plan.md --items .auto/run/tasks/<N>-items.md --project "${CLAUDE_PROJECT_DIR}"`.
   Tell the user the plan waits for approval on the project's Tasks page (the assignee, a manager, an owner or an admin
   approves it), then go back to 0.1 for the next task: this runner keeps its claim on the planned task meanwhile.
3. **Action: build** (its plan was approved). Run `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" start <N> --project "${CLAUDE_PROJECT_DIR}"`;
   it prints a request slug. Start the runner with that slug exactly as in step 2.3 below, then report as in steps 2.4
   and 3. The runner keeps the claim alive, stops if an admin releases the task, and reports it done or stopped at the end.

## 1. Plan first
Switch to plan mode now (the EnterPlanMode tool; if it is only listed as deferred, load it with ToolSearch first). In plan mode, read the project's rules files
(the `rules_files` key of `.claude/auto.json`, usually `CLAUDE.md`) and the queue file (the `queue_file` key, default `.auto/queue.md`). Note the IDs already used there and
in `${CLAUDE_PROJECT_DIR}/.auto/pending/`, and the "Needs a person" list. Read the engineering principles: the file named by `principles.file`
in `.claude/auto.json` (relative to the project), else `${CLAUDE_PLUGIN_ROOT}/docs/engineering-principles.md`; skip this only when
`principles.enabled` is false. Then explore the code the task touches, including what it already has to build on (its components, UI
library, helpers and packages), so the plan reuses them instead of writing new code. If the task is trivial (about ten minutes of work),
say so in the plan and propose doing it directly instead of queueing it.

Write the plan in the usual way (context, approach, files, risks, how it will be verified). Under the principles:

- **Intent first.** Open the plan with an **Intent** section: the outcome the user wants, for whom, how success will be seen,
  and what is out of scope. If the request's wording and its intent differ, say which you followed and why.
- **Key decisions.** Add a **Key decisions** section: for each decision that matters, the options, your choice and why.
  Decide the technical ones yourself. Mark the few only a person can make (product behaviour the request leaves open, cost
  or licences, anything hard to undo) and put them under **Needs a person** with your recommendation.
- **End to end.** Cover what it takes to work in real use, not only the code: data and migrations, security, errors,
  configuration, deployment and rollback, monitoring and docs. Say what is left out on purpose.
- **Vertical slices.** List the slices first: Slice 1, Slice 2, ... Each one is a thin end-to-end piece (UI, API, logic, data as needed)
  that ends in something a person could push, deploy and use. Slice 1 is the smallest useful version. Say for each slice what a
  user can do once it is done. Never plan by layer ("all tables, then all endpoints, then all screens").
- **Journeys.** Add a **Journeys** section: for each slice a person can use, one or more user journeys that walk the whole
  path across screens and services, in this exact form (steps are what a person does, `*Expect:*` what they must see):

      - **J1 Correction reaches the bell.** Slice 2. Roles: reviewer, owner.
        1. As reviewer, file a correction for any order.
        2. As owner, open the home page.
        *Expect:* the bell shows one new notification for that correction.

  When `acceptance.journeys.enabled` is on, the runner starts the app after the last item of each slice and an independent
  agent clicks through that slice's journeys and the earlier slices'; a failed journey becomes a fix item. Write journeys
  a person would accept as proof, not restatements of an item's tests.
- **Libraries first.** Name the existing components and the open-source packages (with their licences) the work will use, and say
  why for anything you plan to build yourself. For each significant new capability (a payment flow, search, file storage, a
  chart library, an external API; not every item), add a row to a **Reuse check** table: requirement, existing options (with
  their real links), cost (free, open-source, free tier with its limits, paid), licence, recommendation (reuse, integrate,
  extend or build) and why. Check each option's page or registry entry before you name it; write "unverified" where you could
  not. Research belongs here, in planning: the items then build what the plan chose.

End the plan with a section **Queue items** that contains the items exactly as they will go into the queue file:

- One item per iteration of work: small enough to finish, test and commit in one sitting. Split anything bigger; a split item must not depend on a later one.
- Each item has an indented `*Slice:*` line, the slice number then its name (`  *Slice:* 1 Sign up with email`). All items of slice 1 come
  before slice 2's, and so on. The last item of each slice is the one that makes it usable, and its *Acceptance:* line proves a behaviour
  a user can see. While `principles.require_slices` is on (the default), the runner refuses items without a `*Slice:*` line.
- Each item is `- [ ] **ID Title.** what to build, where, and what must not change` followed by an indented `*Acceptance:*` line that says what proves it is done (a named test or a command), not "works".
- When `.claude/auto.json` has `"changelog": {"enabled": true}`, give each item an indented `*Kind:*` line: `feature`, `fix`, `breaking` or `internal` (the changelog heading its change goes under; the runner refuses items without one while the changelog is on).
- IDs: a letter plus the next free number not used in the queue file and not used by any waiting request. Use one new letter for the whole task.
- Only work an unattended run can finish: no secrets, no live services, no real customer data, no open product decision. Write fakes and tests instead.
- Anything that needs a person (deploying, live checks, accounts, a decision) goes in a separate short list **Needs a person**, not in the items. Say what you assumed for any open question.
- New behaviour gets tests. End the last item with docs updates, marked unverified unless a test proves otherwise.

Before you show the plan, have it checked by a separate reviewer that reads only the plan and the code:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autospec.py" check --plan "<the plan file>" --project "${CLAUDE_PROJECT_DIR}"`

It prints "no findings", a list of findings (item, kind, detail, fix) or "spec check not done" with the reason. Fix every
finding in the plan's items (add the missing item earlier, reorder, sharpen the acceptance line, split the item), or move a
product question under **Needs a person**. Then run the check again, at most `review.max_plan_revisions` times (default 2) in
all. Add a short **Spec check** section to the plan: what it found, what you changed, and anything still open. If the check
could not be done, say so there and why; do not claim it passed.

Then call ExitPlanMode so the user can accept or change the plan. If they ask for changes, revise and ask again.

## 2. After the plan is accepted
1. Pick a short kebab-case slug for this task (for example `csv-export`). If `${CLAUDE_PROJECT_DIR}/.auto/pending/<slug>` already exists, add the date.
2. Create the folder `${CLAUDE_PROJECT_DIR}/.auto/pending/<slug>/` containing `items.md` (only the Queue items from the plan, verbatim, no headings), `journeys.md` (only the plan's Journeys, verbatim, when it has any) and `plan.md` (the whole accepted plan).
   When the project is connected to the central service, also record the task there so the team sees it:
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" record --slug <slug> --title "<a short title>" --plan .auto/pending/<slug>/plan.md --items .auto/pending/<slug>/items.md --project "${CLAUDE_PROJECT_DIR}"`.
   If it says "Not done" (not signed in, or local mode), tell the user in one line and carry on: the run works without it.
3. Start it in the background (Bash tool with `run_in_background: true`; the first line of output shows whether the request was accepted):

   `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_run.py" --project "${CLAUDE_PROJECT_DIR}" --enqueue <slug> --allow-dirty --max-iterations <number of items> --max-budget-usd 5`

   Use `budget=` or `iterations=` from the user's text if they gave them. If it prints "Not added" with reasons, fix `items.md` and run it once more; after two failures stop and show the user the reasons.
   If it says a run is already in progress, the request is checked and waiting: tell the user it starts on its own branch in a later run.
   If it says the request waits because a branch is not merged yet, tell the user which branch and pull request to review and merge first
   (one request per branch).
   If it refuses to start (preflight), show the reasons and suggest the init command. When the reason is a missing `AUTO_RUNNER_USER`
   or `AUTO_RUNNER_ACCOUNT`, ask the user for their username and the Claude account they use; never guess them. They set the
   variables themselves (the refusal shows how) and restart Claude Code.
4. Tell the user, briefly: what was queued (IDs and titles), the cap (iterations and USD per iteration), that the run reads only committed files (say if `git status` shows uncommitted work it will not see),
   how to watch (the status and log commands), how to stop (create `.auto/STOP`), and that you will review the result when the run ends. Also list the "Needs a person" items.

## 3. When the run ends (you will be notified)
Run the log command, read the commits and the diff on the run's branch, and any question left in the queue file. Report what was done, what the tests prove, what is unverified,
any question waiting, and what the user must do next (review and merge the pull request the runner opened, or push the branch when
publishing is off; the "Needs a person" list). Never merge, push or deploy on your own: only the runner pushes its own branch.
