---
description: Show the last auto-runner iterations (item, result, commit, cost) with the end of each log
argument-hint: [-n 5] [--full N]
---

Run this and show the output as it is. It only reads; it changes no file.

`python "${CLAUDE_PLUGIN_ROOT}/bin/auto_report.py" log --project "${CLAUDE_PROJECT_DIR}" $ARGUMENTS`

`-n` sets how many iterations to list (default 5). `--full N` prints the whole log of the Nth most recent iteration (1 = latest).
A cost shows only when the CLI reported one; otherwise it says "cost not reported". The last line is the git command that shows the same work.
