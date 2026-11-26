---
description: Add a task to the pool (or for yourself); in auto mode it is planned, approved and built with no one waiting
argument-hint: <what to build> [--pool]
---

Add a task on the central service (decision record 0012). The task: **$ARGUMENTS**

1. Turn the request into a short title (one line) and a description: what to build, why, and how we know it is done.
   Ask only if the request is too unclear to write those; do not plan it here.
2. Write the description to `.auto/run/add-description.md`, then run:
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" add --title "<title>" --description-file .auto/run/add-description.md --project "${CLAUDE_PROJECT_DIR}"`
   Add `--pool` when the user said the pool (any free runner takes it); without it the task is the user's own.
3. Show its one line. In auto mode a runner plans it, the supervisor approves the plan and it is built and merged; in
   manual mode the plan waits for a person on the task page. If it says "Not done", show why (usually: run
   `/auto-runner:setup` first).

To plan and build it in this session instead, use `/auto-runner:auto <what to build>`.
