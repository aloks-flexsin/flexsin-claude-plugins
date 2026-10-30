---
description: Plan a task, then (once you accept the plan) queue it and run it unattended in a separate worktree
argument-hint: <what to build> [budget=5] [iterations=N]
---

The user wants this done in autonomous mode: **$ARGUMENTS**

Follow these steps in order. Do not skip the plan, and do not start the run before the plan is accepted.

## 1. Plan first
Switch to plan mode now (the EnterPlanMode tool; if it is only listed as deferred, load it with ToolSearch first). In plan mode, read the project's rules files
(the `rules_files` key of `.claude/auto.json`, usually `CLAUDE.md`) and the queue file (the `queue_file` key, default `.auto/queue.md`). Note the IDs already used there and
in `${CLAUDE_PROJECT_DIR}/.auto/pending/`, and the "Needs a person" list. Then explore the code the task touches. If the task is trivial (about ten minutes of work),
say so in the plan and propose doing it directly instead of queueing it.

Write the plan in the usual way (context, approach, files, risks, how it will be verified), and end it with a section **Queue items** that contains the items exactly as they will go into the queue file:

- One item per iteration of work: small enough to finish, test and commit in one sitting. Split anything bigger; a split item must not depend on a later one.
- Each item is `- [ ] **ID Title.** what to build, where, and what must not change` followed by an indented `*Acceptance:*` line that says what proves it is done (a named test or a command), not "works".
- When `.claude/auto.json` has `"changelog": {"enabled": true}`, give each item an indented `*Kind:*` line: `feature`, `fix`, `breaking` or `internal` (the changelog heading its change goes under; the runner refuses items without one while the changelog is on).
- IDs: a letter plus the next free number not used in the queue file and not used by any waiting request. Use one new letter for the whole task.
- Only work an unattended run can finish: no secrets, no live services, no real customer data, no open product decision. Write fakes and tests instead.
- Anything that needs a person (deploying, live checks, accounts, a decision) goes in a separate short list **Needs a person**, not in the items. Say what you assumed for any open question.
- New behaviour gets tests. End the last item with docs updates, marked unverified unless a test proves otherwise.

Then call ExitPlanMode so the user can accept or change the plan. If they ask for changes, revise and ask again.

## 2. After the plan is accepted
1. Pick a short kebab-case slug for this task (for example `csv-export`). If `${CLAUDE_PROJECT_DIR}/.auto/pending/<slug>` already exists, add the date.
2. Create the folder `${CLAUDE_PROJECT_DIR}/.auto/pending/<slug>/` containing `items.md` (only the Queue items from the plan, verbatim, no headings) and `plan.md` (the whole accepted plan).
3. Start it in the background (Bash tool with `run_in_background: true`; the first line of output shows whether the request was accepted):

   `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_run.py" --project "${CLAUDE_PROJECT_DIR}" --enqueue <slug> --allow-dirty --max-iterations <number of items> --max-budget-usd 5`

   Use `budget=` or `iterations=` from the user's text if they gave them. If it prints "Not added" with reasons, fix `items.md` and run it once more; after two failures stop and show the user the reasons.
   If it says a run is already in progress, the request is checked and waiting: tell the user it will be added when the next runner starts.
   If it refuses to start (preflight), show the reasons and suggest the init command.
4. Tell the user, briefly: what was queued (IDs and titles), the cap (iterations and USD per iteration), that the run reads only committed files (say if `git status` shows uncommitted work it will not see),
   how to watch (the status and log commands), how to stop (create `.auto/STOP`), and that you will review the result when the run ends. Also list the "Needs a person" items.

## 3. When the run ends (you will be notified)
Run the log command, read the commits and the diff on the run's branch, and any question left in the queue file. Report what was done, what the tests prove, what is unverified,
any question waiting, and what the user must do next (review, merge, the "Needs a person" list). Never merge, push or deploy on your own.
