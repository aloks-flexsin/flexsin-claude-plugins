---
description: Start working on the task pool hands-free (the runner), or `start supervisor` for the supervisor loop
argument-hint: [supervisor]
---

Start hands-free work on this computer (decision record 0012). Arguments: **$ARGUMENTS**

**No arguments: the runner.** Read `${CLAUDE_PLUGIN_ROOT}/commands/auto.md` and follow its section "W. Watch mode": the
watcher takes this person's tasks and the pool's, plans them, and builds approved ones until `.auto/STOP` exists.

**`supervisor`: the supervisor loop.** Start it in the background (Bash tool with `run_in_background: true`):
`python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py" watch`. It decides plans, merges green pull requests, deploys and
keeps the queue moving every 15 minutes for each project in `AUTO_SUPERVISOR_PROJECTS` (or the single project in
`AUTO_SUPERVISOR_PROJECT`); it needs the supervisor settings described in the README and stops when `.auto/STOP` exists
in this folder. If it exits at once, show its message (exit 2: a setting is missing; 3: not a supervisor token).

**Without a background process.** Tell the user they can instead keep the whole lifecycle moving from this session with
Claude Code's loop: `/loop 15m /auto-runner:tick` (one step each time; stopping the loop stops the work).

Stop with `/auto-runner:stop`.
