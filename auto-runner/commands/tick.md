---
description: One step of the hands-free lifecycle (for /loop 15m /auto-runner:tick): take or continue a task, then supervise
---

One idempotent step, then return (decision record 0012). Run it under Claude Code's loop to keep work moving:
`/loop 15m /auto-runner:tick`.

1. If `.auto/STOP` exists, say "stopped (delete .auto/STOP to go on)" and do nothing else.
2. **Work.** Run `python "${CLAUDE_PLUGIN_ROOT}/bin/autowatch.py" --once --project "${CLAUDE_PROJECT_DIR}"`: it takes or
   continues one task from this person's list or the pool, plans it or builds its next items, pushes the branch and
   returns. If it says "Not started", show why and skip to step 3.
3. **Supervise.** Run one pass as the Supervise stage under your own sign-in (no setup needed):
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py"` (decide plans, merge green pull requests, deploy, order the
   queue). Exit 3 means you are below developer in this project: skip this step and say so once.
4. Report in at most three lines: what moved, what is waiting, and anything that needs a person.

Never start a second watcher while one runs in the background (step 2 then says another one holds the lock: that is fine).
