---
description: Stop this computer's runner, watcher or supervisor loop cleanly (between steps, nothing is lost)
---

Create the STOP file in this project: `.auto/STOP` (an empty file; use the Write tool with no content). The runner, the
watcher and `autosupervisor.py watch` started from this folder all check it between steps and stop cleanly; work in
progress stays on its branch, already pushed.

Then say in one line what was stopped and that deleting `.auto/STOP` is needed before `/auto-runner:start` starts again.
If the user runs `/loop 15m /auto-runner:tick`, tell them to end that loop too.
