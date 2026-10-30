---
description: Show the auto-runner queue (counts by state, running item, blocked questions, recorded spend)
---

Run this and show the output as it is. It only reads; it changes no file.

`python "${CLAUDE_PLUGIN_ROOT}/bin/auto_report.py" status --project "${CLAUDE_PROJECT_DIR}"`

Then add one sentence on what, if anything, needs the user: a blocked item waiting for an answer, or a finished run waiting for review.
