---
description: Run one Supervisor pass (approve or send back plans, merge green pull requests) and print a summary
---

Run this and show the output as it is. It needs the supervisor account's token and settings in the environment (see the
README); it refuses to run, with a non-zero exit code, if the token is not a supervisor's.

`python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py"`

Then say in one sentence what the pass did, or, on a non-zero exit, what is missing (exit 2: a setting is not set; 3: the token
is not a supervisor's or the service did not answer; 1: a step reported an error). Never print or ask for a token.

To keep supervising until stopped, run `python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py" watch` (a pass every 15
minutes; `--every SECONDS` to change it) in the background; it covers every project in `AUTO_SUPERVISOR_PROJECTS`
(`slug=clone dir,...`) and stops when `.auto/STOP` exists in the folder it was started from.

To add a task instead of running a pass (decision record 0010, 6a: only work that traces to something already asked for),
run `python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py" add --title "<title>" --from "<what it comes from>" --for <member's
email> [--description-file F] [--plan F --items F] [--project <slug>]` and show its one line. A plan sent with it waits for
a person to approve it.
