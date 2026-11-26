---
description: Show where work stands: the queue, the last runs, my central tasks and anything waiting on a person
---

Run these and show the output as it is. They only read; they change no file.

1. `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_report.py" status --project "${CLAUDE_PROJECT_DIR}"` (queue counts, running item,
   blocked questions, recorded spend).
2. `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_report.py" log --project "${CLAUDE_PROJECT_DIR}" -n 3` (the last iterations).
3. When the project is connected to the central service:
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" list --project "${CLAUDE_PROJECT_DIR}"` (tasks, plans waiting,
   branches, pull requests).

Then add one sentence on what, if anything, needs the user: a blocked item waiting for an answer, a plan waiting for
approval (manual mode), or a finished run waiting for review. `/auto-runner:log -n 10` shows more iterations.
