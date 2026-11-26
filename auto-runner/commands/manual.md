---
description: Switch a task, the project, or this session's edits to manual mode (a person decides), or back to auto
argument-hint: on|off [task N] | edits
---

Manual mode brings back every person-in-the-loop step (decision record 0012). Arguments: **$ARGUMENTS**

- **`on task N` / `off task N`**: run
  `python "${CLAUDE_PLUGIN_ROOT}/bin/autotasks.py" manual N on|off --project "${CLAUDE_PROJECT_DIR}"` and show its line.
  A manual task's plan waits for a person; the supervisor leaves it alone. Only the task's assignee, a manager or above
  may change it.
- **`on` / `off`** (the project): two switches, both must say auto for the supervisor to decide alone.
  1. The repository: `"autonomy": "manual"` (or `"auto"`) in `.claude/auto.json`. Make that change on a branch and
     open a pull request, like any change; it takes effect once merged.
  2. The central service: an owner or admin switches it on the project's Settings › Repository page. Give the user
     the link from `python "${CLAUDE_PLUGIN_ROOT}/bin/autocentral.py" status --project "${CLAUDE_PROJECT_DIR}"`.
- **`edits`**: the hook has already let this session edit the main checkout for 12 hours when `enforce_runner` is on
  (this was `/auto-runner:allow-edits`). Say so in one line.
